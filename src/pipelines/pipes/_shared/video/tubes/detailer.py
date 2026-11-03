from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import numpy as np
import torch

from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import CompareImagesGenerationOutput, Icon, ProgressGenerationOutput
from src.platform.observability.profiling import get_profiler
from src.platform.runtime.device import clear_gpu_memory
from src.platform.runtime.native.memory.residency import get_residency_registry
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4
from src.pipelines.pipes._shared.media.video_read import read_video_frames
from src.pipelines.pipes.generator.seedvr2.color_fix import color_correct_batch
from src.pipelines.pipes._shared.video.tubes.compositing import composite_tube, resize_patches_to_window
from src.pipelines.pipes._shared.video.tubes.detection import build_frame_detectors, detect_tracks
from src.pipelines.pipes._shared.video.tubes.windowing import snap_working_resolution, stabilize_window
from src.pipelines.pipes._shared.generation.config_specs import device_config_spec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TubeClip:
    source: str
    start_frame: int
    num_frames: int
    fps: float


def free_room_for_tube_refine(bundle: Any, device: str, own_models: Tuple[Any, ...]) -> None:
    if not str(device).startswith("cuda"):
        get_profiler().mark("detailer.free_room", device=str(device), dit_was_resident=False)
        return

    alloc0 = torch.cuda.memory_allocated(device) / (1 << 30) if torch.cuda.is_available() else 0.0
    dit = getattr(bundle, "dit", None)
    dit_dev = str(getattr(dit, "device", "<no dit>"))
    dit_was_resident = dit is not None and dit_dev.startswith("cuda")
    if dit_was_resident:
        dit.offload()
    get_residency_registry().offload_all(device, exclude=tuple(m for m in own_models if m is not None))
    clear_gpu_memory()
    alloc1 = torch.cuda.memory_allocated(device) / (1 << 30) if torch.cuda.is_available() else 0.0
    logger.debug(
        "[VIDEO_DETAILER] eviction pass: dit.device=%s, allocated %.2fGB -> %.2fGB",
        dit_dev, alloc0, alloc1,
    )
    get_profiler().mark(
        "detailer.free_room", device=str(device), dit_was_resident=dit_was_resident,
        alloc_before_gb=round(alloc0, 2), alloc_after_gb=round(alloc1, 2),
    )


def tube_detailer_configuration() -> List[PipeConfigSpec]:
    return [
        device_config_spec(),
        PipeConfigSpec("strength", str, "balanced", "Refinement strength (starting sigma)",
                       required=False, choices=["light", "balanced", "strong"]),
        PipeConfigSpec("detect_faces", bool, True, "Detect and refine faces", required=False),
        PipeConfigSpec("detect_hands", bool, True, "Detect and refine hands", required=False),
        PipeConfigSpec("detection_backend", str, "mediapipe",
                       "Detector backend (mediapipe = Apache-2.0 default; yolo = AGPL, opt-in)",
                       required=False, choices=["mediapipe", "yolo"]),
        PipeConfigSpec("detection_stride", int, 6, "Detect every Nth frame", required=False,
                       min_value=1, max_value=60),
        PipeConfigSpec("detection_confidence", float, 0.5, "Detector confidence threshold",
                       required=False, min_value=0.0, max_value=1.0),
        PipeConfigSpec("max_tracks", int, 4, "Max subjects to refine per clip", required=False,
                       min_value=1, max_value=16),
        PipeConfigSpec("min_track_seconds", float, 0.5, "Discard tracks shorter than this",
                       required=False, min_value=0.0, max_value=10.0),
        PipeConfigSpec("iou_threshold", float, 0.3, "IoU threshold for linking detections into tracks",
                       required=False, min_value=0.0, max_value=1.0),
        PipeConfigSpec("cut_threshold", float, 0.35, "Histogram distance above which a scene cut splits a track",
                       required=False, min_value=0.0, max_value=1.0),
        PipeConfigSpec("pad_factor", float, 1.8, "Context padding around each subject's window",
                       required=False, min_value=1.0, max_value=4.0),
        PipeConfigSpec("area_threshold", float, 0.40,
                       "Fraction of frame area above which the tube window follows the subject instead of holding fixed",
                       required=False, min_value=0.05, max_value=1.0),
        PipeConfigSpec("working_short_side", int, 512, "Refine working-resolution short side (px)",
                       required=False, min_value=128, max_value=1024),
        PipeConfigSpec("color_correction", str, "wavelet", "Match refined patch colour to the original",
                       required=False, choices=["wavelet", "adain", "none"]),
        PipeConfigSpec("feather_border_frac", float, 0.08, "Spatial feather border (fraction of tube size)",
                       required=False, min_value=0.0, max_value=0.5),
        PipeConfigSpec("temporal_ramp_frames", int, 4, "Fade-in/out length at each tube segment end (frames)",
                       required=False, min_value=0, max_value=30),
        PipeConfigSpec("face_model", str, "models/mediapipe/face_landmarker.task",
                       "MediaPipe face landmarker model path", required=False),
        PipeConfigSpec("hand_model", str, "models/mediapipe/hand_landmarker.task",
                       "MediaPipe hand landmarker model path", required=False),
    ]


class VideoTubeDetailerPipe(BasePipe):
    name = "detailer"
    display_title = "Refining faces and hands"
    model_family: str = ""
    model_label: str = ""
    pipe_label: str = ""

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return tube_detailer_configuration()

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True,
                          f"{cls.model_label} model bundle (same model that generated the clip)", is_array=False),
            PipeInputSpec("video", IOType.VIDEO, True, "Video file(s) to refine", is_array=True),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True,
                          "Encoded prompt conditioning (the generation's positive prompt)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds (per video, for the refine noise)", is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("video", IOType.VIDEO, "Refined video file(s)", is_array=True),
        ]

    def own_models(self, bundle: Any) -> Tuple[Any, ...]:
        raise NotImplementedError

    def free_room(self, bundle: Any, device: str) -> None:
        free_room_for_tube_refine(bundle, device, self.own_models(bundle))

    def working_resolution(self, window: Any) -> Tuple[int, int]:
        return snap_working_resolution(
            window.width, window.height, short_side=int(self.config.get("working_short_side", 512)))

    def refine_tube(
        self, bundle: Any, cond_model: Any, pixels: torch.Tensor, clip: TubeClip, *,
        strength: str, device: str, seed: int, generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> np.ndarray:
        raise NotImplementedError

    def release_models(self, bundle: Any) -> None:
        for model in self.own_models(bundle):
            if model is not None:
                model.offload()

    def process(
        self, pipe_input: PipeInput, generation_outputs: callable, is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        bundle = pipe_input.input["model"]
        if getattr(bundle.spec, "family", None) != self.model_family:
            raise ValueError(
                f"{self.pipe_label}: loaded model '{getattr(bundle.spec, 'family', '?')}' is not a "
                f"{self.model_label} checkpoint -- this detailer re-runs the {self.model_label} model over each tube."
            )

        videos = pipe_input.input.get("video") or []
        if not videos:
            return PipeOutput(output={"video": []})
        conditioning = pipe_input.input.get("conditioning") or []
        if not conditioning:
            raise ValueError(f"{self.pipe_label} requires 'conditioning' (the generation's positive prompt)")
        seeds = pipe_input.input.get("seed") or []
        device = self.config.get("device", "cuda")

        self.free_room(bundle, device)

        outputs: List[str] = []
        try:
            for i, video_path in enumerate(videos):
                cond_model = conditioning[i] if i < len(conditioning) else conditioning[-1]
                seed = int(seeds[i]) if i < len(seeds) else (int(seeds[0]) if seeds else 0)
                outputs.append(self._process_one(
                    bundle, cond_model, video_path, seed, device, generation_outputs, is_cancelled))
        finally:
            try:
                self.release_models(bundle)
            except Exception:
                logger.debug("[VIDEO_DETAILER] model offload failed", exc_info=True)
            clear_gpu_memory()

        return PipeOutput(output={"video": outputs})

    def _process_one(
        self, bundle: Any, cond_model: Any, video_path: str, seed: int, device: str,
        generation_outputs: callable, is_cancelled: Optional[callable] = None,
    ) -> str:
        generation_outputs(ProgressGenerationOutput(
            state="Scanning for faces and hands", icon=Icon(name="face-smile", effect="pulse")))
        frames_pil, fps = read_video_frames(video_path)
        frames = np.stack([np.asarray(im.convert("RGB")) for im in frames_pil])
        del frames_pil
        height, width = int(frames.shape[1]), int(frames.shape[2])

        build_result = build_frame_detectors(
            detect_faces=bool(self.config.get("detect_faces", True)),
            detect_hands=bool(self.config.get("detect_hands", True)),
            backend=self.config.get("detection_backend", "mediapipe"),
            face_model=self.config.get("face_model", "models/mediapipe/face_landmarker.task"),
            hand_model=self.config.get("hand_model", "models/mediapipe/hand_landmarker.task"),
            confidence=float(self.config.get("detection_confidence", 0.5)),
        )
        detectors = build_result.detectors
        for kind in build_result.missing:
            generation_outputs(ProgressGenerationOutput(
                state=f"{kind.title()} detection model isn't downloaded yet -- skipping {kind} enhancement",
                icon=Icon(name="warning")))

        if not detectors and build_result.missing:
            generation_outputs(ProgressGenerationOutput(
                state="Face and hand enhancement was skipped because the detection model isn't available yet",
                icon=Icon(name="check")))
            return video_path

        tracks = detect_tracks(
            frames, detectors,
            stride=int(self.config.get("detection_stride", 6)), fps=fps,
            iou_threshold=float(self.config.get("iou_threshold", 0.3)),
            cut_threshold=float(self.config.get("cut_threshold", 0.35)),
            min_track_seconds=float(self.config.get("min_track_seconds", 0.5)),
            max_tracks=int(self.config.get("max_tracks", 4)),
        )

        if not tracks:
            generation_outputs(ProgressGenerationOutput(
                state="No faces or hands found -- video unchanged", icon=Icon(name="check")))
            return video_path

        strength = self.config.get("strength", "balanced")
        for ti, track in enumerate(tracks):
            generation_outputs(ProgressGenerationOutput(
                state=f"Refining {track.kind} <<NUMBER:{ti + 1}>>/<<NUMBER:{len(tracks)}>>",
                icon=Icon(name="sparkles", effect="pulse")))
            self._refine_track(bundle, cond_model, frames, track, width, height, strength, device, fps, seed,
                               video_path=video_path, track_number=ti + 1, generation_outputs=generation_outputs,
                               is_cancelled=is_cancelled)

        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        encode_frames_to_mp4(frames, out_path, fps=fps, audio=video_path)
        generation_outputs(ProgressGenerationOutput(
            state=f"Enhanced <<NUMBER:{len(tracks)}>> region(s)", icon=Icon(name="check")))
        return out_path

    def _refine_track(
        self, bundle: Any, cond_model: Any, frames: np.ndarray, track: Any,
        width: int, height: int, strength: str, device: str, fps: float, seed: int,
        *, video_path: str, track_number: int, generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> None:
        window = stabilize_window(
            track, width, height,
            pad_factor=float(self.config.get("pad_factor", 1.8)),
            area_threshold=float(self.config.get("area_threshold", 0.40)),
        )
        tw, th = self.working_resolution(window)

        pixels, crops = self._tube_to_pixels(frames, window, tw, th, device)
        clip = TubeClip(source=video_path, start_frame=int(window.start_frame),
                        num_frames=len(window.frames), fps=float(fps))
        try:
            refined = self.refine_tube(
                bundle, cond_model, pixels, clip,
                strength=strength, device=device, seed=seed, generation_outputs=generation_outputs,
                is_cancelled=is_cancelled)
        finally:
            del pixels

        patches = resize_patches_to_window([refined[i] for i in range(refined.shape[0])], window)
        patches = color_correct_batch(
            patches, crops, self.config.get("color_correction", "wavelet"), device=device)
        self._emit_tube_compare(generation_outputs, track_number, track, crops, patches)
        composite_tube(
            frames, patches, window,
            border_frac=float(self.config.get("feather_border_frac", 0.08)),
            ramp_frames=int(self.config.get("temporal_ramp_frames", 4)))

    @staticmethod
    def _emit_tube_compare(
        generation_outputs: callable, track_number: int, track: Any,
        crops: List[np.ndarray], patches: List[np.ndarray],
    ) -> None:
        if not patches or not crops:
            return
        from PIL import Image

        mid = len(patches) // 2
        before = Image.fromarray(np.ascontiguousarray(crops[mid]))
        after = Image.fromarray(np.ascontiguousarray(patches[mid]))
        label = f"{str(track.kind).title()} {track_number}"
        generation_outputs(CompareImagesGenerationOutput(
            index=track_number - 1,
            compare=(f"{label} - before enhancement", before),
            to=(f"{label} - after enhancement", after),
        ))

    @staticmethod
    def _tube_to_pixels(frames: np.ndarray, window: Any, tw: int, th: int, device: str):
        crops = []
        for f in window.frames:
            x0, y0, x1, y1 = window.box_at(f)
            crops.append(np.ascontiguousarray(frames[f, y0:y1, x0:x1]))
        arr = np.stack(crops)
        t = torch.from_numpy(arr).to(device=device).float().div_(255.0).permute(0, 3, 1, 2)
        t = torch.nn.functional.interpolate(t, size=(th, tw), mode="bicubic", align_corners=False)
        t = t.clamp_(0.0, 1.0)
        t = (t * 2.0 - 1.0).permute(1, 0, 2, 3).unsqueeze(0)
        return t, crops
