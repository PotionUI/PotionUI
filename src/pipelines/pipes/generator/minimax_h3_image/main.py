from __future__ import annotations

from typing import Any, List, Optional, Tuple

from src.pipelines.contracts import IOType, PipeConfigSpec, PipeInput, PipeInputSpec, PipeOutputSpec
from src.pipelines.pipes._shared.generation.config_specs import (
    device_config_spec,
    preview_config_spec,
    seed_config_spec,
)
from src.pipelines.pipes._shared.generation.flow_generator_pipe import FlowMatchGeneratorPipe
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes.generator.minimax_h3_image.engine import MiniMaxH3ImageNativeGenerator
from src.pipelines.pipes.generator.minimax_h3_image.references import (
    MAX_REFERENCES,
    as_rgb,
    resize_to,
    resolve_canvas,
    vae_reference_size,
)
from src.platform.runtime.native.arch.minimax_h3.image import ReferenceLatents, augment_reference_latent
from src.platform.runtime.native.errors import NativeEngineUnsupportedError

FAMILY = "minimax_h3_image"
SCHEDULE = "h3_image_pdd"
SAMPLER = "euler"


class GeneratorMiniMaxH3ImagePipe(FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native MiniMax-H3-Image generator: text to image, or an edit guided by up to 16 pictures"
    family_tag = "GENERATOR H3 IMAGE"
    supports_shift = False

    def _generator_class(self):
        return MiniMaxH3ImageNativeGenerator

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=False, choices=["txt2img", "edit"]),
            PipeConfigSpec("steps", int, 6, "Denoising steps (the model is distilled for 6)", required=False,
                           min_value=1, max_value=12),
            PipeConfigSpec("sampler", str, SAMPLER, "Sampler (the step heads need plain Euler steps)",
                           required=False, choices=[SAMPLER]),
            PipeConfigSpec("resolution", str, "2048x2048", "Resolution (WxH), or 'auto' for Picture 1's size",
                           required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            seed_config_spec(),
            device_config_spec(),
            preview_config_spec(),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "MiniMax-H3-Image model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)",
                          is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            PipeInputSpec("reference_images", IOType.IMAGE, False, "Edit pictures in order; Picture 1 is edited",
                          is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True)]

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        references = [as_rgb(image) for image in (pipe_input.input.get("reference_images") or [])]
        if len(references) > MAX_REFERENCES:
            raise ValueError(f"MiniMax-H3-Image takes at most {MAX_REFERENCES} pictures, got {len(references)}")
        width, height = resolve_canvas(self.config.get("resolution", "2048x2048"), references)
        self.config = {**self.config, "resolution": f"{width}x{height}", "sampler": SAMPLER, "guidance": 1.0}
        ctx = super().build_context(pipe_input)
        gen: MiniMaxH3ImageNativeGenerator = ctx.extra["generator"]
        if gen.spec.family != FAMILY:
            raise NativeEngineUnsupportedError(
                f"this preset needs a MiniMax-H3-Image DiT, but the selected file is a {gen.spec.family} model"
            )
        module = gen.dit.module
        ctx.extra["step_cache_options"] = None
        ctx.extra["schedule_settings"] = {
            "schedule": SCHEDULE,
            "schedule_options": {"grid": list(module.config.pdd_sigmas)} if module.config.pdd_sigmas else {},
        }
        ctx.extra["patch_size"] = tuple(module.config.patch_size)
        ctx.extra["reference_latents"] = [
            gen.encode_reference(resize_to(picture, vae_reference_size(*picture.size))) for picture in references
        ]
        return ctx

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: Any):
        ctx.extra["current_seed"] = int(seed)
        return super().generate_one(ctx, index, seed, progress)

    def attach_conditioning(self, ctx: GeneratorContext, cond: dict,
                            uncond: Optional[dict]) -> Tuple[dict, Optional[dict]]:
        latents = ctx.extra.get("reference_latents") or []
        if not latents:
            return cond, None
        seed = ctx.extra.get("current_seed", 0)
        patch = ctx.extra["patch_size"]
        augmented = tuple(augment_reference_latent(latent, seed, patch) for latent in latents)
        return {**cond, "ref_latents": ReferenceLatents(augmented)}, None
