"""Temporal crop-refine video detailer for the native LTX-2 / 2.3 family.

A standalone video-FILE-in -> video-FILE-out pipe that refines faces and hands
as spatiotemporal TUBES: the same LTX model that generated the clip re-runs, at a
light denoise strength, over a stabilized crop that follows each subject through
time, and the result is feathered back in. Refining a face as a coherent tube
(rather than frame-by-frame) is what keeps the refinement temporally stable --
the model sees real motion, not per-frame jitter.

Because it is a pure file->file transform it slots between the generator/upscaler
and the gallery in ANY LTX pipeline, decoupled from how the video was produced.
The heavy lifting is split across sibling modules, each independently testable:

  * ``detection.py`` -- per-frame face/hand detection (MediaPipe default, Apache-2.0)
  * ``tracking.py``  -- pure IoU linking, scene-cut splitting, filter/cap/merge
  * ``windowing.py`` -- pure stabilized fixed/moving tube-window geometry
  * ``refine.py``    -- the LTX encode -> low-noise denoise -> decode tube refine
  * ``compositing.py`` -- pure feathered spatial + temporal paste-back

Memory discipline (the box runs earlyoom with no swap): frames are held as ONE
uint8 buffer and refined tubes are blended back in place -- never two full fp32
copies of a long clip. Each tube's tensors are freed before the next. DiT/VAE
are placed once and offloaded at the end; ``place_dit_for_sequence`` sizes the
activation reserve to each (small) tube.

The generator pipe before this one warm-parks its ~23GB DiT resident, so
``_free_room_for_tube_refine`` must run ONCE before the per-track loop to free
activation headroom for the first tube's encode. Only the initial state needs
an explicit sweep: each tube's encode/denoise is already VRAM-coherent
(``encode_with_oom_retry`` eviction ladder + per-tube ``place_dit_for_sequence``).
Unlike the upscale path, no TE-eviction follow-up: this pipe keeps the DiT for
its own denoise moments later, so freeing the TE would force a same-gen reload.
"""

from __future__ import annotations

import logging
import tempfile
from typing import Any, Dict, List

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
from src.platform.runtime.native.memory.residency import get_residency_manager
from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4
from src.pipelines.pipes._shared.media.video_read import read_video_frames
# color_fix lives in the seedvr2 pipe (Apache-2.0); importing the submodule
# directly pulls in only numpy/torch, not seedvr2's heavy generator deps.
from src.pipelines.pipes.generator.seedvr2.color_fix import color_correct_batch
from src.pipelines.pipes.detailer.video_ltx.compositing import composite_tube, resize_patches_to_window
from src.pipelines.pipes.detailer.video_ltx.detection import build_frame_detectors, detect_tracks
from src.pipelines.pipes.detailer.video_ltx.refine import refine_tube_pixels
from src.pipelines.pipes.detailer.video_ltx.windowing import snap_working_resolution, stabilize_window

logger = logging.getLogger(__name__)


def _free_room_for_tube_refine(bundle: Any, device: str) -> None:
    """Evict a resident DiT and every other foreign GPU-resident component
    BEFORE the per-track tube-refine loop's own GPU work (see the module
    docstring). Mirrors ``latent_upscaler/ltx``'s ``_free_room_for_upscale``
    idiom (offload the DiT if resident, then ``GpuResidencyManager.offload_all``
    + ``clear_gpu_memory``); skips that function's TE-host-RAM follow-up since
    this pipe needs the DiT again for its own denoise a moment later.

    ``bundle.dit``/``bundle.vae`` are excluded from the eviction sweep: the
    DiT is about to be re-placed per tube by ``place_dit_for_sequence``
    (inside ``refine_tube_pixels``) and the VAE is what this pipe's own tube
    encodes are about to move onto the GPU. A no-op on a non-CUDA device,
    mirroring ``place_dit_for_sequence``'s/``_free_room_for_upscale``'s
    identical early return.
    """
    if not str(device).startswith("cuda"):
        get_profiler().mark("detailer.free_room", device=str(device), dit_was_resident=False)
        return

    alloc0 = torch.cuda.memory_allocated(device) / (1 << 30) if torch.cuda.is_available() else 0.0
    dit = getattr(bundle, "dit", None)
    dit_dev = str(getattr(dit, "device", "<no dit>"))
    dit_was_resident = dit is not None and dit_dev.startswith("cuda")
    if dit_was_resident:
        dit.offload()
    own_models = tuple(m for m in (getattr(bundle, "vae", None), dit) if m is not None)
    get_residency_manager().offload_all(device, exclude=own_models)
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


class DetailerVideoLtxPipe(BasePipe):
    name = "detailer"
    description = ("Refine faces and hands in an LTX-2/2.3 video as stabilized "
                  "spatiotemporal tubes (same model, light denoise, feathered paste-back)")
    display_title = "Refining faces and hands"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "device": "cuda",
            "strength": "balanced",
            "detect_faces": True,
            "detect_hands": True,
            "detection_backend": "mediapipe",
            "detection_stride": 6,
            "detection_confidence": 0.5,
            "max_tracks": 4,
            "min_track_seconds": 0.5,
            "iou_threshold": 0.3,
            "cut_threshold": 0.35,
            "pad_factor": 1.8,
            "area_threshold": 0.40,
            "working_short_side": 512,
            "color_correction": "wavelet",
            "feather_border_frac": 0.08,
            "temporal_ramp_frames": 4,
            "face_model": "models/mediapipe/face_landmarker.task",
            "hand_model": "models/mediapipe/hand_landmarker.task",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
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

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "LTX model bundle (same model that generated the clip)",
                          is_array=False),
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

    # -- entry -------------------------------------------------------------

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        bundle = pipe_input.input["model"]
        if getattr(bundle.spec, "family", None) != "ltx":
            raise ValueError(
                f"detailer/video_ltx: loaded model '{getattr(bundle.spec, 'family', '?')}' is not an "
                f"LTX-2/2.3 checkpoint -- this detailer re-runs the LTX model over each tube."
            )

        videos = pipe_input.input.get("video") or []
        if not videos:
            return PipeOutput(output={"video": []})
        conditioning = pipe_input.input.get("conditioning") or []
        if not conditioning:
            raise ValueError("detailer/video_ltx requires 'conditioning' (the generation's positive prompt)")
        seeds = pipe_input.input.get("seed") or []
        device = self.config.get("device", "cuda")

        # Make room BEFORE the per-track loop's own GPU work: the generator pipe
        # before this one warm-parks its DiT resident, and the first tube's VAE
        # encode otherwise starts with zero activation headroom.
        _free_room_for_tube_refine(bundle, device)

        outputs: List[str] = []
        try:
            for i, video_path in enumerate(videos):
                cond_model = conditioning[i] if i < len(conditioning) else conditioning[-1]
                seed = int(seeds[i]) if i < len(seeds) else (int(seeds[0]) if seeds else 0)
                outputs.append(self._process_one(bundle, cond_model, video_path, seed, device, generation_outputs))
        finally:
            # Tubes are done -- release the model weights this pipe pinned.
            try:
                bundle.dit.offload()
                bundle.vae.offload()
            except Exception:  # pragma: no cover - best-effort teardown
                logger.debug("[VIDEO_DETAILER] model offload failed", exc_info=True)
            clear_gpu_memory()

        return PipeOutput(output={"video": outputs})

    # -- one video ---------------------------------------------------------

    def _process_one(
        self, bundle: Any, cond_model: Any, video_path: str, seed: int, device: str,
        generation_outputs: callable,
    ) -> str:
        generation_outputs(ProgressGenerationOutput(
            state="Scanning for faces and hands", icon=Icon(name="face-smile", effect="pulse")))
        frames_pil, fps = read_video_frames(video_path)
        frames = np.stack([np.asarray(im.convert("RGB")) for im in frames_pil])  # (T,H,W,3) uint8
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
            # Every requested kind was skipped for a missing model (not because
            # both toggles were off) -- nothing to detect, so hand back the
            # source untouched rather than running a zero-detector loop.
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
            # Nothing to refine -- return the source untouched (no re-encode:
            # preserves the original quality AND audio exactly).
            generation_outputs(ProgressGenerationOutput(
                state="No faces or hands found -- video unchanged", icon=Icon(name="check")))
            return video_path

        strength = self.config.get("strength", "balanced")
        for ti, track in enumerate(tracks):
            generation_outputs(ProgressGenerationOutput(
                state=f"Refining {track.kind} <<NUMBER:{ti + 1}>>/<<NUMBER:{len(tracks)}>>",
                icon=Icon(name="sparkles", effect="pulse")))
            self._refine_track(bundle, cond_model, frames, track, width, height, strength, device, fps, seed,
                               track_number=ti + 1, generation_outputs=generation_outputs)

        out_path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        # Audio passthrough: preserve the source clip's audio track (a no-op
        # when it has none -- encode_frames_to_mp4 probes first).
        encode_frames_to_mp4(frames, out_path, fps=fps, audio=video_path)
        generation_outputs(ProgressGenerationOutput(
            state=f"Enhanced <<NUMBER:{len(tracks)}>> region(s)", icon=Icon(name="check")))
        return out_path

    def _refine_track(
        self, bundle: Any, cond_model: Any, frames: np.ndarray, track: Any,
        width: int, height: int, strength: str, device: str, fps: float, seed: int,
        *, track_number: int, generation_outputs: callable,
    ) -> None:
        window = stabilize_window(
            track, width, height,
            pad_factor=float(self.config.get("pad_factor", 1.8)),
            area_threshold=float(self.config.get("area_threshold", 0.40)),
        )
        tw, th = snap_working_resolution(
            window.width, window.height, short_side=int(self.config.get("working_short_side", 512)))

        pixels, crops = self._tube_to_pixels(frames, window, tw, th, device)
        try:
            refined = refine_tube_pixels(
                bundle, cond_model, pixels, strength=strength, device=device, fps=fps, seed=seed)
        finally:
            del pixels

        patches = resize_patches_to_window([refined[i] for i in range(refined.shape[0])], window)
        patches = color_correct_batch(
            patches, crops, self.config.get("color_correction", "wavelet"), device=device)
        # Emit the compare BEFORE the paste-back, so it shows the isolated tube,
        # not the feathered composite. `crops`/`patches` are the window's size.
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
        """Emit a per-tube before/after compare artifact.

        A/B-ing two separate generations to judge the detailer is confounded --
        each run re-rolls the WHOLE upscale+refine, so the faces differ for
        reasons unrelated to the detailer. Instead every run emits its OWN
        evidence per refined track: the tube's representative middle frame, the
        original crop vs. the refined+colour-matched patch. Both are already the
        tube window's exact size, so they line up as a clean side-by-side.

        Reaches the UI as a ``compare_images`` ``pipe_artifact`` message (the
        same idiom the standalone ``artifact`` pipe uses -- see
        ``CompareImagesGenerationOutput`` / ``serialize_compare_images_output``):
        ``{compare_image, compare_label, to_image, to_label}`` base64 pair the
        frontend renders as a labelled before/after. Naturally capped at the
        track cap (one pair per track, ``max_tracks`` <= 4 by default)."""
        if not patches or not crops:
            return
        from PIL import Image  # local: PIL is already a hard media/detection dep

        mid = len(patches) // 2  # representative frame (tube middle)
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
        """Crop the tube's per-frame windows and bicubic-upscale to working
        resolution ``(th, tw)`` -> ``(1, 3, n, th, tw)`` in ``[-1, 1]``. Returns
        that tensor plus the list of ORIGINAL uint8 ``(wh, ww, 3)`` crops (kept
        for the colour-match source).

        Bicubic, not bilinear: this crop is almost always UPSCALED (small
        faces/hands are the common case -- see ``snap_working_resolution``),
        and at a light/balanced refine strength the low-noise denoise keeps a
        majority weight on this interpolated latent (``strength`` -> starting
        sigma in ``refine.py`` is 0.40-0.70, i.e. 30-60% of the pre-refine
        signal survives untouched). A blurrier bilinear interpolant here bakes
        softness into the refine's OWN input, not just its output -- measured
        via a CPU repro (identity-model resize round-trip, Laplacian-variance
        sharpness metric): bicubic keeps ~55% more high-frequency energy than
        bilinear at the same upscale factor. Clamped to ``[0, 1]`` first since
        bicubic can ring slightly above/below the source range."""
        crops = []
        for f in window.frames:
            x0, y0, x1, y1 = window.box_at(f)
            crops.append(np.ascontiguousarray(frames[f, y0:y1, x0:x1]))
        arr = np.stack(crops)  # (n, wh, ww, 3) uint8
        t = torch.from_numpy(arr).to(device=device).float().div_(255.0).permute(0, 3, 1, 2)  # (n,3,wh,ww)
        t = torch.nn.functional.interpolate(t, size=(th, tw), mode="bicubic", align_corners=False)
        t = t.clamp_(0.0, 1.0)
        t = (t * 2.0 - 1.0).permute(1, 0, 2, 3).unsqueeze(0)  # (1,3,n,th,tw)
        return t, crops
