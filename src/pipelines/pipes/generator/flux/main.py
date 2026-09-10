"""Generator for the native Flux family (txt2img).

Consumes the ``model`` bundle from ``model_loader/flux`` and the ``conditioning``
list from the shared ``prompt_encoder`` pipe (the mandated pipe path — this pipe
never re-encodes prompts). Per seed it builds a ``Conditioning`` from the
``ConditioningModel`` role dicts, runs ``NativeGenerator.sample`` (flow-matching
denoise with the preset's sampler + embedded guidance), decodes, and emits the
image through the shared gallery path.

``build_context``/``generate_one`` (shared by every native flow-matching family)
live in ``FlowMatchGeneratorPipe``; this module only carries Flux's own config
schema/defaults.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.pipes._shared.generation.flow_generator_pipe import (
    FlowMatchGeneratorPipe,
    iterate_mode_config_specs,
    spectral_progressive_config_specs,
)
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.guidance_options import (
    apply_schedule_settings,
    schedule_settings_config_specs,
)


class GeneratorFluxPipe(FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native Flux-family generator (flow matching, embedded guidance)"
    family_tag = "GENERATOR FLUX"

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        apply_schedule_settings(ctx, self.config)
        return ctx

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "txt2img",
            "steps": 20,
            "guidance": 3.5,
            "shift": None,
            "sampler": "euler",
            "resolution": "1024x1024",
            "quantity": 1,
            "seed": -1,
            "device": "cuda",
            "denoise": 0.55,
            "preview": True,
            "step_cache": {},
            "schedule": "",
            "schedule_options": {},
            "manual_sigmas": "",
            "detail_strength": None,
            "detail_start": None,
            "detail_end": None,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=True, choices=["txt2img", "img2img"]),
            *cls.img2img_config_specs(default_denoise=0.55),
            PipeConfigSpec("steps", int, 20, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 3.5, "Embedded (distilled) guidance scale", required=False,
                           min_value=0.0, max_value=30.0),
            PipeConfigSpec("shift", float, None, "Sigma-shift override (constant-shift variants only)", required=False),
            PipeConfigSpec("sampler", str, "euler", "Sampler: any key registered on the sampler registry "
                           "(see GET /api/sampling/catalog)", required=False),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews to the workbench during sampling", required=False),
            *schedule_settings_config_specs(),
            *iterate_mode_config_specs(),
            *spectral_progressive_config_specs(),
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
            PipeInputSpec("model", IOType.MODEL, True, "Flux model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            cls.img2img_input_spec(),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True),
        ]
