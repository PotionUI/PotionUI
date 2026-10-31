"""Generator for the native Z-Image family (txt2img).

Consumes the ``model`` bundle from ``model_loader/z_image`` and the
``conditioning`` list from the shared ``prompt_encoder`` pipe (the mandated pipe
path — this pipe never re-encodes prompts). Per seed it builds a ``Conditioning``
from the ``ConditioningModel`` role dicts, runs ``NativeGenerator.sample``
(flow-matching denoise, shift 3.0), decodes through the Flux-style 2D AE, and
emits the image through the shared gallery path.

Guidance is Z-Image's true CFG (spec ``"cfg"``): the distilled *turbo* runs
``cfg_scale == 1.0`` (a single forward — ``TrueCFG`` skips the uncond pass),
while base/finetune checkpoints use a real ``cfg_scale`` (~4) with the encoded
negative. The default here is the turbo profile; the preset overrides both.

``build_context``/``generate_one`` (shared by every native flow-matching family)
live in ``FlowMatchGeneratorPipe``; this module only carries Z-Image's own
config schema/defaults.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes._shared.generation.flow_generator_pipe import FlowMatchGeneratorPipe
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.guidance_options import (
    apply_schedule_settings,
    schedule_settings_config_specs,
)
from src.pipelines.pipes.generator.z_image.control import control_canvas, encode_control_context, first_image
from src.platform.runtime.native.sampling.flow_schedule import percent_to_sigma
from src.pipelines.pipes._shared.generation.config_specs import (
    device_config_spec,
    preview_config_spec,
    seed_config_spec,
)


class GeneratorZImagePipe(FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native Z-Image generator (flow matching, true CFG)"
    family_tag = "GENERATOR Z-IMAGE"

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        apply_schedule_settings(ctx, self.config)
        if ctx.extra.get("mode") == "control":
            self._control_context_setup(ctx, pipe_input)
        return ctx

    def _control_context_setup(self, ctx: GeneratorContext, pipe_input: PipeInput) -> None:
        dit_module = getattr(getattr(pipe_input.input.get("model"), "dit", None), "module", None)
        if getattr(dit_module, "fun_control", None) is None:
            raise GenerationExecutionError("The Fun ControlNet model isn't loaded. Pick it in the Models section.")
        control_image = first_image(pipe_input.input.get("control_image"))
        if control_image is None:
            raise GenerationExecutionError("Choose a Guide image.")
        gen = ctx.extra["generator"]
        width, height = control_canvas(gen, ctx.extra["width"], ctx.extra["height"], control_image)
        latent_shape = gen.latent_shape_for(width, height)
        image_seq_len = int(latent_shape[-2]) * int(latent_shape[-1])
        settings = gen.spec.sampling_settings
        ctx.extra.update({
            "control_sigma_range": (
                percent_to_sigma(float(self.config.get("control_start", 0.0)), settings, image_seq_len),
                percent_to_sigma(float(self.config.get("control_end", 1.0)), settings, image_seq_len),
            ),
            "width": width,
            "height": height,
            "iterate_mode": False,
            "control_inputs": control_image,
            "control_strength": float(self.config.get("control_strength", 1.0)),
        })

    def attach_conditioning(self, ctx: GeneratorContext, cond: dict,
                            uncond: Optional[dict]) -> Tuple[dict, Optional[dict]]:
        if ctx.extra.get("mode") != "control":
            return cond, uncond
        context = ctx.extra.get("control_context")
        if context is None:
            context = encode_control_context(
                ctx.extra["generator"], ctx.extra["width"], ctx.extra["height"], ctx.extra["control_inputs"])
            ctx.extra["control_context"] = context
        extra = {
            "control_context": context,
            "control_context_scale": ctx.extra["control_strength"],
            "control_sigma_range": ctx.extra["control_sigma_range"],
        }
        return {**cond, **extra}, ({**uncond, **extra} if uncond is not None else None)

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=True, choices=["txt2img", "img2img", "inpaint", "control"]),
            PipeConfigSpec("control_strength", float, 1.0, "Fun ControlNet strength (control_context_scale)",
                           required=False, min_value=0.0, max_value=2.0),
            PipeConfigSpec("control_start", float, 0.0, "Fraction of the sampling where the Fun ControlNet starts",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("control_end", float, 1.0, "Fraction of the sampling where the Fun ControlNet ends",
                           required=False, min_value=0.0, max_value=1.0),
            *cls.img2img_config_specs(default_denoise=0.55),
            PipeConfigSpec("steps", int, 8, "Denoising steps (turbo ~8, base ~30)", required=False, min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 1.0, "True CFG scale (turbo 1.0 = single forward; base ~4)", required=False,
                           min_value=0.0, max_value=30.0),
            PipeConfigSpec("shift", float, None, "Sigma-shift override; blank -> spec default (3.0)", required=False),
            PipeConfigSpec("sampler", str, "euler", "Sampler: any key registered on the sampler registry "
                           "(see GET /api/sampling/catalog)", required=False),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            seed_config_spec(),
            device_config_spec(),
            preview_config_spec(),
            *schedule_settings_config_specs(),
            PipeConfigSpec(
                "step_cache", dict, {},
                "FBCache step-skipping options, forwarded to NativeGenerator.sample() "
                "unmodified: {'rel_threshold': 0.12, 'warmup_steps': 4, "
                "'max_consecutive_skips': 3}. rel_threshold<=0 (default/absent) is off "
                "and never wraps the guidance strategy -- byte-identical to leaving this "
                "unset. Read directly by FlowMatchGeneratorPipe.build_context, not through "
                "the flat step_cache_threshold/warmup_steps/max_skips resolver the Wan/LTX "
                "video pipes use (this family has no such resolver).",
                required=False,
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "Z-Image model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            cls.img2img_input_spec(),
            cls.inpaint_input_spec(),
            PipeInputSpec("control_image", IOType.IMAGE, False, "Guide image for the Fun ControlNet", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True),
        ]
