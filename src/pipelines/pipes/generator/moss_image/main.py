from __future__ import annotations

from typing import List

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec, logger
from src.pipelines.pipes._shared.generation.flow_generator_pipe import FlowMatchGeneratorPipe
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.guidance_options import (
    apply_schedule_settings,
    parse_int_set,
    schedule_settings_config_specs,
)
from src.platform.runtime.native.engine import NativeGenerator
from src.pipelines.pipes._shared.generation.config_specs import device_config_spec, seed_config_spec


class GeneratorMossImagePipe(FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native MOSS-Image text-to-image generator (true CFG with a layer-skipped negative branch)"
    family_tag = "GENERATOR MOSS"

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=False,
                           choices=["txt2img", "inpaint"]),
            *cls.img2img_config_specs(default_denoise=1.0),
            PipeConfigSpec("steps", int, 50, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 3.0, "True CFG scale (3.0 for both SFT and Base)",
                           required=False, min_value=0.0, max_value=30.0),
            PipeConfigSpec("shift", float, None, "Sigma-shift override; blank uses the checkpoint's 5.0",
                           required=False),
            PipeConfigSpec("sampler", str, "euler", "Sampler: any key registered on the sampler registry "
                           "(see GET /api/sampling/catalog)", required=False),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            seed_config_spec(),
            device_config_spec(),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews to the workbench during sampling",
                           required=False),
            *schedule_settings_config_specs(),
            PipeConfigSpec(
                "guidance_options", dict, {},
                "TrueCFG options. 'uncond_skip_layers' lists the DiT blocks skipped on the negative pass "
                "(comma-separated or a list; empty turns the skip off, unset keeps the checkpoint's [22]). "
                "cfg_zero_star is always off.",
                required=False,
            ),
            PipeConfigSpec(
                "step_cache", dict, {},
                "FBCache step-skipping options, forwarded to NativeGenerator.sample() "
                "unmodified: {'rel_threshold': 0.12, 'warmup_steps': 4, "
                "'max_consecutive_skips': 3}. rel_threshold<=0 (default/absent) is off "
                "and never wraps the guidance strategy. The negative pass carries skip_layers "
                "and always recomputes.",
                required=False,
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "MOSS-Image model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            cls.img2img_input_spec(),
            cls.inpaint_input_spec(),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True),
        ]

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        apply_schedule_settings(ctx, self.config)
        gen: NativeGenerator = ctx.extra["generator"]
        options = {**(ctx.extra.get("guidance_options") or {}), "cfg_zero_star": False}
        if "uncond_skip_layers" in options:
            options["uncond_skip_layers"] = sorted(parse_int_set(options["uncond_skip_layers"]))
        ctx.extra["guidance_options"] = options
        if ctx.extra["guidance"] <= 1.0:
            logger.warning(
                "[%s] %s is a CFG model but guidance is %.2f: it will render without CFG; "
                "use the Base speed profile (CFG 3.0)", self.family_tag, gen.spec.variant, ctx.extra["guidance"],
            )
        return ctx
