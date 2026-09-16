from __future__ import annotations

import random
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
from PIL import Image

from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import CompareImagesGenerationOutput, ImageGenerationOutput, Icon
from src.platform.runtime.native.engine import Conditioning
from src.platform.runtime.native.sampling import make_preview_hook
from src.pipelines.pipes._shared.generation.img2img import img2img_denoise
from src.pipelines.pipes._shared.generation.native_generator import build_native_generator
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter, native_step_hooks
from src.pipelines.pipes.detailer.native.colour import match_colour
from src.pipelines.pipes.detailer.native.detection import FaceDetection, build_face_detector
from src.pipelines.pipes.detailer.native.latent_blend import LatentInpaintFilter, latent_mask_tensor
from src.pipelines.pipes.detailer.native.loras import (
    DROP,
    KEEP,
    MODES,
    SELECT,
    baked_loras,
    face_lora_scope,
    face_loras,
)
from src.pipelines.pipes.detailer.native.overlay import (
    OverlayFace,
    face_label,
    render_overlay,
    step_suffix,
)
from src.pipelines.pipes.detailer.native.mask import (
    build_face_mask,
    default_dilate,
    mediapipe_oval_ring,
    oval_polygon,
)

_SKIP_TAG = "skip"
_TOO_SMALL = "too small"
_TOO_LARGE = "too large"


@dataclass
class _FaceRun:
    conditioning: Any
    seeds: Any
    ring: Any
    min_face_ratio: float
    max_face_ratio: float
    max_faces: int
    crop_padding: float
    crop_size: int
    mask_dilate: float
    feather: int
    steps: int
    guidance: float
    sampler: str
    denoise: float
    latent_mask: bool
    colour_match: bool
    overlay: bool
    seed_offset: int
    loras_mode: str
    selected_loras: List[Dict[str, Any]]
    run_loras: List[Dict[str, Any]]
    dit: Any


@dataclass
class _FacePlan:
    face: FaceDetection
    box: Tuple[int, int, int, int]
    mask: Any = field(default=None)
    mask_mode: str = field(default="box")
    reason: Optional[str] = field(default=None)


class DetailerNativePipe(BasePipe):
    name = "detailer"
    description = "Family-agnostic native face detailer: detect -> mask-guided latent inpaint -> colour match -> feather back in"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "detection_backend": "mediapipe",
            "detection_confidence": 0.5,
            "max_faces": 4,
            "min_face_ratio": 0.02,
            "max_face_ratio": 0.6,
            "crop_padding": 0.6,
            "crop_size": 1536,
            "denoise": 0.3,
            "steps": 12,
            "guidance": 1.0,
            "sampler": "euler",
            "feather": 24,
            "mask_mode": "oval",
            "mask_dilate": 0.12,
            "latent_mask": True,
            "colour_match": True,
            "overlay": True,
            "loras_mode": "keep",
            "loras": [],
            "run_loras": [],
            "seed_offset": 0,
            "face_model": None,
            "yolo_model": None,
            "device": "cuda",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("detection_backend", str, "mediapipe",
                           "Detector backend (mediapipe = Apache-2.0 default; yolo = AGPL, opt-in)",
                           required=False, choices=["mediapipe", "yolo"]),
            PipeConfigSpec("detection_confidence", float, 0.5, "Face detection confidence threshold",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("max_faces", int, 4, "Maximum number of faces refined per image",
                           required=False, min_value=1, max_value=20),
            PipeConfigSpec("min_face_ratio", float, 0.02,
                           "Skip faces smaller than this fraction of the frame area",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("max_face_ratio", float, 0.6,
                           "Skip faces wider than this fraction of the frame's short side",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("crop_padding", float, 0.6,
                           "Padding around each face box, as a fraction of the box's own size",
                           required=False, min_value=0.0, max_value=2.0),
            PipeConfigSpec("crop_size", int, 1536,
                           "Working resolution each face crop is resized to before refining",
                           required=False, min_value=256, max_value=2048),
            PipeConfigSpec("denoise", float, 0.3, "img2img denoise strength for each face crop",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("steps", int, 12, "Denoising steps per face", required=False,
                           min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 1.0, "CFG scale for the face refine pass", required=False,
                           min_value=0.0, max_value=30.0),
            PipeConfigSpec("sampler", str, "euler", "Sampler for the face refine pass", required=False,
                           choices=["euler", "dpmpp_2m", "unipc"]),
            PipeConfigSpec("feather", int, 24, "Feather width (px) blending the refined face back in",
                           required=False, min_value=0, max_value=256),
            PipeConfigSpec("mask_mode", str, "oval",
                           "Face mask shape: oval follows the landmark face contour, box is a rounded rectangle",
                           required=False, choices=["oval", "box"]),
            PipeConfigSpec("mask_dilate", float, 0.12,
                           "Grow the face mask by this fraction of the face box's short side",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("latent_mask", bool, True,
                           "Re-pin latents outside the face mask to the original at every sampling step",
                           required=False),
            PipeConfigSpec("colour_match", bool, True,
                           "Match the refined face's colour statistics to the original crop before compositing",
                           required=False),
            PipeConfigSpec("loras_mode", str, "keep",
                           "What happens to the run's LoRAs during the face pass",
                           required=False, choices=list(MODES)),
            PipeConfigSpec("loras", list, [],
                           "LoRA stack applied for the face pass when loras_mode is 'select'",
                           required=False),
            PipeConfigSpec("run_loras", list, [],
                           "The run's own LoRA stack, restored after the face pass",
                           required=False),
            PipeConfigSpec("overlay", bool, True,
                           "Draw detection brackets, mask contour and per-face labels on the live preview",
                           required=False),
            PipeConfigSpec("seed_offset", int, 0, "Added to every face's per-face seed", required=False),
            PipeConfigSpec("face_model", str, None,
                           "MediaPipe face landmarker model path (mediapipe backend only)", required=False),
            PipeConfigSpec("yolo_model", str, None,
                           "YOLO face detector model path (yolo backend only)", required=False),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Images to detail", is_array=True),
            PipeInputSpec("model", IOType.MODEL, True, "Native model bundle (reused from generation)",
                          is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True,
                          "Prompt conditioning reused per image", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Per-image seeds", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Images with refined faces", is_array=True),
        ]

    def process(
        self,
        pipe_input: PipeInput,
        generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        images = pipe_input.input["image"]
        if not isinstance(images, list):
            images = [images]
        if not images:
            return PipeOutput(output={"image": images})

        denoise = float(self.config.get("denoise", 0.3))
        if denoise <= 0.0:
            return PipeOutput(output={"image": images})

        bundle = pipe_input.input["model"]
        conditioning = pipe_input.input.get("conditioning") or []
        seeds = pipe_input.input.get("seed") or []

        backend = self.config.get("detection_backend", "mediapipe")
        confidence = float(self.config.get("detection_confidence", 0.5))
        max_faces = int(self.config.get("max_faces", 4))
        min_face_ratio = float(self.config.get("min_face_ratio", 0.02))
        max_face_ratio = float(self.config.get("max_face_ratio", 0.6))
        crop_padding = float(self.config.get("crop_padding", 0.6))
        crop_size = int(self.config.get("crop_size", 1536))
        steps = int(self.config.get("steps", 12))
        guidance = float(self.config.get("guidance", 2.5))
        sampler = self.config.get("sampler", "euler")
        feather = int(self.config.get("feather", 24))
        mask_mode = self.config.get("mask_mode", "oval")
        mask_dilate = float(self.config.get("mask_dilate", 0.12))
        latent_mask = bool(self.config.get("latent_mask", True))
        colour_match = bool(self.config.get("colour_match", True))
        overlay = bool(self.config.get("overlay", True))
        loras_mode = self.config.get("loras_mode", KEEP)
        selected_loras = face_loras(self.config.get("loras"))
        run_loras = baked_loras(self.config.get("run_loras"))
        seed_offset = int(self.config.get("seed_offset", 0))
        device = self.config.get("device", "cuda")
        model_path = self.config.get("yolo_model") if backend == "yolo" else self.config.get("face_model")
        if not model_path:
            raise ValueError(f"detailer/native: no {backend} face detector model selected")

        detector = build_face_detector(
            backend, model_path=model_path, confidence=confidence, max_faces=max_faces, device=device,
        )
        generator = build_native_generator(bundle, device=device)
        progress = ProgressEmitter(generation_outputs, title=self.name)
        ring = mediapipe_oval_ring() if mask_mode == "oval" else None

        results: List[Image.Image] = []
        lora_scope = ExitStack()
        try:
            self._detail_images(
                images, results, detector, generator, progress, generation_outputs, is_cancelled,
                lora_scope, _FaceRun(
                    conditioning=conditioning, seeds=seeds, ring=ring,
                    min_face_ratio=min_face_ratio, max_face_ratio=max_face_ratio,
                    max_faces=max_faces, crop_padding=crop_padding, crop_size=crop_size,
                    mask_dilate=mask_dilate, feather=feather, steps=steps, guidance=guidance,
                    sampler=sampler, denoise=denoise, latent_mask=latent_mask,
                    colour_match=colour_match, overlay=overlay, seed_offset=seed_offset,
                    loras_mode=loras_mode, selected_loras=selected_loras, run_loras=run_loras,
                    dit=getattr(bundle, "dit", None),
                ),
            )
        finally:
            lora_scope.close()

        return PipeOutput(output={"image": results})

    def _detail_images(self, images, results, detector, generator, progress, generation_outputs,
                       is_cancelled, lora_scope, run: "_FaceRun") -> None:
        conditioning = run.conditioning
        seeds = run.seeds
        ring = run.ring
        min_face_ratio = run.min_face_ratio
        max_face_ratio = run.max_face_ratio
        max_faces = run.max_faces
        crop_padding = run.crop_padding
        crop_size = run.crop_size
        mask_dilate = run.mask_dilate
        feather = run.feather
        steps = run.steps
        guidance = run.guidance
        sampler = run.sampler
        denoise = run.denoise
        latent_mask = run.latent_mask
        colour_match = run.colour_match
        overlay = run.overlay
        seed_offset = run.seed_offset
        lora_scope_open: List[bool] = []

        for index, source in enumerate(images):
            image = source.convert("RGB") if isinstance(source, Image.Image) \
                else Image.fromarray(np.asarray(source)).convert("RGB")

            if is_cancelled and is_cancelled():
                results.append(image)
                continue

            plans = self._plan_faces(
                detector.detect(image), image, min_face_ratio, max_face_ratio, max_faces,
                crop_padding, ring, mask_dilate, feather,
            )
            faces = [plan for plan in plans if plan.reason is None]
            if not faces:
                results.append(image)
                continue

            generation_outputs(ImageGenerationOutput(
                image=self._overlay_frame(image, plans, overlay),
                temporary=True,
            ))

            cond_obj = self._conditioning_for(conditioning, index)
            seed = self._seed_for(seeds, index)
            current = image.copy()

            for face_idx, plan in enumerate(faces):
                face = plan.face
                if is_cancelled and is_cancelled():
                    break

                progress.state(
                    f"Refining face <<NUMBER:{face_idx + 1}>>/<<NUMBER:{len(faces)}>>, "
                    f"image {index + 1}/{len(images)}",
                    icon=Icon(name="face-smile", effect="pulse"),
                )

                x1, y1, x2, y2 = plan.box
                region = current.crop((x1, y1, x2, y2))
                crop_w, crop_h = x2 - x1, y2 - y1
                target_w, target_h = generator.snap_resolution(crop_size, crop_size)
                resized = region.resize((target_w, target_h), Image.LANCZOS)

                if not lora_scope_open:
                    lora_scope_open.append(True)
                    lora_scope.enter_context(face_lora_scope(
                        run.dit, run.loras_mode, run.selected_loras, run.run_loras,
                    ))
                    if run.loras_mode == DROP:
                        progress.state("Faces: LoRAs dropped", icon=Icon(name="face-smile"))
                    elif run.loras_mode == SELECT:
                        progress.state(
                            f"Faces: <<NUMBER:{len(run.selected_loras)}>> LoRA(s) selected",
                            icon=Icon(name="face-smile"),
                        )

                generation_outputs(ImageGenerationOutput(
                    image=self._overlay_frame(current, plans, overlay, active=plan), temporary=True,
                ))

                mask = plan.mask
                tracker = {"step": 0, "total": steps}

                def on_step(_fraction, step_index, total, tracker=tracker):
                    tracker["step"] = step_index + 1
                    tracker["total"] = total
                    progress.step(step_index + 1, total, state="FACE_DETAILER",
                                  icon=Icon(name="face-smile", effect="pulse"))

                face_seed = int(seed) + face_idx + seed_offset
                base_frame = current.copy()
                preview_hook = make_preview_hook(
                    getattr(generator, "spec", None),
                    self._in_place_preview(progress, base_frame, plan, plans, overlay, tracker),
                )
                extra_hooks = (preview_hook,) if preview_hook is not None else ()
                latent_kwargs: Dict[str, Any] = {}
                if latent_mask:
                    original_latent = generator.encode_image(np.asarray(resized))
                    noise = self._seeded_noise(original_latent, face_seed)
                    latent_kwargs = {"init_latent": original_latent, "noise": noise}
                    extra_hooks = extra_hooks + (
                        LatentInpaintFilter(
                            original_latent.float(),
                            noise.float(),
                            latent_mask_tensor(mask.resize((target_w, target_h), Image.BOX),
                                               original_latent.shape),
                        ),
                    )

                refined = img2img_denoise(
                    generator, np.asarray(resized), cond_obj,
                    steps=steps, seed=face_seed, cfg_scale=guidance, sampler=sampler,
                    denoise=denoise, is_cancelled=is_cancelled,
                    hooks=native_step_hooks(generator, progress, on_step, preview=False, extra=extra_hooks),
                    **latent_kwargs,
                )[0]
                refined_pil = Image.fromarray(refined).resize((crop_w, crop_h), Image.LANCZOS)
                if colour_match:
                    refined_pil = self._colour_matched(refined_pil, region, mask)

                current.paste(refined_pil, (x1, y1), mask)

                next_plan = faces[face_idx + 1] if face_idx + 1 < len(faces) else None
                generation_outputs(ImageGenerationOutput(
                    image=self._overlay_frame(
                        current, plans, overlay and next_plan is not None, active=next_plan,
                    ),
                    temporary=True,
                ))
                generation_outputs(CompareImagesGenerationOutput(
                    index=index, compare=("Base", region), to=("Face", refined_pil),
                ))

            results.append(current)

    @classmethod
    def _in_place_preview(cls, progress: ProgressEmitter, base: Image.Image, plan: "_FacePlan",
                          plans: List["_FacePlan"], overlay: bool, tracker: Dict[str, int]):
        def emit(preview: Image.Image) -> None:
            frame = base.copy()
            frame.paste(preview.convert("RGB").resize(plan.mask.size, Image.LANCZOS), plan.box[:2], plan.mask)
            progress.preview(cls._overlay_frame(
                frame, plans, overlay, active=plan, step=(tracker["step"], tracker["total"]),
            ))

        return emit

    @classmethod
    def _overlay_frame(cls, frame: Image.Image, plans: List["_FacePlan"], overlay: bool,
                       *, active: Optional["_FacePlan"] = None, step=None) -> Image.Image:
        if not overlay:
            return frame.copy()
        kept = [plan for plan in plans if plan.reason is None]
        if active is not None:
            label = face_label(
                kept.index(active) + 1, active.face.confidence,
                (active.box[2] - active.box[0], active.box[3] - active.box[1]), active.mask_mode,
            )
            if step is not None:
                label = step_suffix(label, step[0], step[1])
            quiet = [
                OverlayFace(box=plan.box, mask=plan.mask)
                for plan in plans if plan is not active and plan.reason is None
            ]
            return render_overlay(
                frame,
                [OverlayFace(box=active.box, mask=active.mask, label=label, focus=True)] + quiet,
                protect=[plan.box for plan in plans if plan is not active],
            )

        faces = []
        for plan in plans:
            if plan.reason is not None:
                faces.append(OverlayFace(box=plan.box, tag=_SKIP_TAG, skipped=True))
                continue
            faces.append(OverlayFace(box=plan.box, mask=plan.mask, tag=str(kept.index(plan) + 1)))
        return render_overlay(frame, faces)

    @classmethod
    def _plan_faces(cls, detections, image: Image.Image, min_ratio: float, max_ratio: float,
                    max_faces: int, crop_padding: float, ring, dilate_fraction: float,
                    feather: int) -> List["_FacePlan"]:
        width, height = image.size
        area = max(1, width * height)
        short_side = max(1, min(width, height))
        kept: List[_FacePlan] = []
        skipped: List[_FacePlan] = []
        for face in detections:
            box = cls._pad_and_square(face.box, width, height, crop_padding)
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            box_w, box_h = face.box[2] - face.box[0], face.box[3] - face.box[1]
            if face.area / area < min_ratio:
                skipped.append(_FacePlan(face=face, box=tuple(box), reason=_TOO_SMALL))
                continue
            if max_ratio > 0.0 and max(box_w, box_h) / short_side > max_ratio:
                skipped.append(_FacePlan(face=face, box=tuple(box), reason=_TOO_LARGE))
                continue
            kept.append(_FacePlan(face=face, box=tuple(box)))

        kept.sort(key=lambda plan: plan.face.area, reverse=True)
        kept = kept[:max_faces]
        for plan in kept:
            plan.mask, plan.mask_mode = cls._face_mask(plan.face, plan.box, ring, dilate_fraction, feather)
        return kept + skipped

    @staticmethod
    def _face_mask(face: FaceDetection, box, ring, dilate_fraction: float, feather: int):
        crop_x, crop_y = box[0], box[1]
        crop_w, crop_h = box[2] - box[0], box[3] - box[1]
        local_box = (face.box[0] - crop_x, face.box[1] - crop_y,
                     face.box[2] - crop_x, face.box[3] - crop_y)
        polygon = None
        if ring and face.landmarks:
            local = [(px - crop_x, py - crop_y) for px, py in face.landmarks]
            polygon = oval_polygon(local, ring)
        mask = build_face_mask(
            (crop_w, crop_h), polygon=polygon, box=local_box,
            dilate=default_dilate(face.box, dilate_fraction), feather=feather,
        )
        return mask, "oval" if polygon else "box"

    @staticmethod
    def _colour_matched(refined: Image.Image, region: Image.Image, mask: Image.Image) -> Image.Image:
        weights = np.asarray(mask, dtype=np.float64) / 255.0
        matched = match_colour(
            np.asarray(refined, dtype=np.float64),
            np.asarray(region.convert("RGB"), dtype=np.float64),
            weights,
        )
        return Image.fromarray(np.clip(np.rint(matched), 0, 255).astype(np.uint8), mode="RGB")

    @staticmethod
    def _seeded_noise(latent: "torch.Tensor", seed: int) -> "torch.Tensor":
        generator = torch.Generator(device=latent.device).manual_seed(int(seed))
        return torch.randn(latent.shape, generator=generator, device=latent.device, dtype=latent.dtype)

    @staticmethod
    def _pad_and_square(box, img_w: int, img_h: int, padding_frac: float) -> List[int]:
        x1, y1, x2, y2 = box
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        side = max(x2 - x1, y2 - y1) * (1.0 + 2.0 * padding_frac)
        half = side / 2.0
        nx1 = max(0.0, cx - half)
        ny1 = max(0.0, cy - half)
        nx2 = min(float(img_w), cx + half)
        ny2 = min(float(img_h), cy + half)
        return [int(round(nx1)), int(round(ny1)), int(round(nx2)), int(round(ny2))]

    @staticmethod
    def _conditioning_for(conditioning: List[Any], index: int) -> Conditioning:
        cond_model = conditioning[index] if index < len(conditioning) else conditioning[-1]
        return Conditioning(cond=cond_model.embeds, uncond=cond_model.n_embeds or None)

    @staticmethod
    def _seed_for(seeds: List[Any], index: int) -> int:
        if index < len(seeds):
            return int(seeds[index])
        return random.randint(0, 2 ** 31 - 1)
