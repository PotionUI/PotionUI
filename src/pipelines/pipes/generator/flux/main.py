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

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec, logger
from src.pipelines.outputs import GenerationExecutionError, ImageGenerationOutput, Icon
from src.pipelines.pipes._shared.generation.flow_generator_pipe import (
    FlowMatchGeneratorPipe,
    iterate_mode_config_specs,
)
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.guidance_options import (
    apply_schedule_settings,
    schedule_settings_config_specs,
)
from src.pipelines.pipes._shared.generation.config_specs import (
    device_config_spec,
    preview_config_spec,
    seed_config_spec,
)
from src.pipelines.pipes._shared.generation.nag_inputs import attach_nag
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter, native_step_hooks
from src.pipelines.pipes._shared.imaging.alpha import flatten_onto
from src.pipelines.pipes.generator.flux3.references import (
    GRID,
    prepare_multi_reference,
    prepare_reference,
)
from src.platform.runtime.native.engine import Conditioning, NativeGenerator

_SIZE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$")


class GeneratorFluxPipe(FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native Flux-family generator (flow matching, embedded guidance)"
    family_tag = "GENERATOR FLUX"

    MAX_REFERENCES = 4

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        apply_schedule_settings(ctx, self.config)
        if ctx.extra.get("mode") == "edit":
            gen: NativeGenerator = ctx.extra["generator"]
            if gen.spec.variant != "flux2":
                raise GenerationExecutionError(
                    "Reference-picture editing needs a FLUX.2 Klein checkpoint; FLUX.1 cannot take reference pictures."
                )
            ctx.extra["output_size"] = self._parse_size(self.config.get("output_size"))
        return ctx

    @staticmethod
    def _parse_size(raw: Any) -> Optional[Tuple[int, int]]:
        match = _SIZE.match(str(raw or ""))
        if match is None:
            return None
        width, height = int(match.group(1)), int(match.group(2))
        return (width, height) if width > 0 and height > 0 else None

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter):
        if ctx.extra.get("mode") == "edit":
            return self._generate_edit(ctx, index, seed, progress)
        return super().generate_one(ctx, index, seed, progress)

    def _prepare_edit(self, ctx: GeneratorContext) -> Dict[str, Any]:
        prepared = ctx.extra.get("edit_prepared")
        if prepared is not None:
            return prepared
        images = (ctx.extra.get("images") or [])[:self.MAX_REFERENCES]
        if not images:
            raise GenerationExecutionError("FLUX.2 edit needs at least one reference image.")
        gen: NativeGenerator = ctx.extra["generator"]
        sources = [flatten_onto(image) for image in images]
        for number, source in enumerate(sources, start=1):
            if min(source.size) < GRID:
                raise GenerationExecutionError(
                    f"Reference image {number} is too small ({source.width}x{source.height}); "
                    f"each side needs at least {GRID} px."
                )
        crop = None
        if len(sources) == 1:
            reference, crop = prepare_reference(sources[0])
            references = [reference]
        else:
            references = [prepare_multi_reference(source) for source in sources]
        requested = ctx.extra.get("output_size")
        if requested is not None:
            width, height = gen.snap_resolution(*requested)
            crop = None
        else:
            width, height = references[0].size
        ref_latents = [gen.encode_image(np.asarray(reference)) for reference in references]
        prepared = {"ref_latents": ref_latents, "width": width, "height": height, "crop": crop}
        ctx.extra["edit_prepared"] = prepared
        return prepared

    def _generate_edit(
        self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter,
    ) -> ImageGenerationOutput:
        gen: NativeGenerator = ctx.extra["generator"]
        conditioning = ctx.extra["conditioning"]
        prepared = self._prepare_edit(ctx)
        width, height = prepared["width"], prepared["height"]

        cond_model = conditioning[index] if index < len(conditioning) else conditioning[-1]
        uncond = dict(cond_model.n_embeds) if cond_model.n_embeds else None
        cond = attach_nag(dict(cond_model.embeds), uncond, self.config, attention_mask=True)
        cond["ref_latents"] = prepared["ref_latents"]
        if uncond is not None:
            uncond["ref_latents"] = prepared["ref_latents"]

        def on_progress(_fraction: float, step_index: int, total: int) -> None:
            progress.step(step_index + 1, total, state="EDIT", icon=Icon(name="bolt", effect="pulse"))

        logger.debug("[%s] edit image %d/%d, seed %d", self.family_tag, index + 1, ctx.quantity, seed)
        latent = gen.sample(
            Conditioning(cond=cond, uncond=uncond),
            gen.latent_shape_for(width, height),
            steps=ctx.extra["steps"],
            seed=seed,
            cfg_scale=ctx.extra["guidance"],
            sampler=ctx.extra["sampler"],
            guidance_options=ctx.extra.get("guidance_options"),
            sampler_options=ctx.extra.get("sampler_options"),
            step_cache_options=ctx.extra.get("step_cache_options"),
            schedule_settings=ctx.extra.get("schedule_settings"),
            hooks=native_step_hooks(gen, progress, on_progress, preview=self.config.get("preview", True),
                                    extra=self.extra_step_hooks()),
            is_cancelled=ctx.is_cancelled,
        )
        image = Image.fromarray(gen.decode(latent)[0])
        if prepared["crop"] is not None:
            image = image.crop(prepared["crop"])
        return ImageGenerationOutput(
            image=image, temporary=True, seed=seed,
            resolution=image.size, cfg=ctx.extra["guidance"], step=ctx.extra["steps"],
        )

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=True, choices=["txt2img", "img2img", "inpaint", "edit"]),
            *cls.img2img_config_specs(default_denoise=0.55),
            PipeConfigSpec("steps", int, 20, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 3.5, "Embedded (distilled) guidance scale", required=False,
                           min_value=0.0, max_value=30.0),
            PipeConfigSpec("shift", float, None, "Sigma-shift override (constant-shift variants only)", required=False),
            PipeConfigSpec("sampler", str, "euler", "Sampler: any key registered on the sampler registry "
                           "(see GET /api/sampling/catalog)", required=False),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("output_size", str, "", "Edit output size (WxH); blank matches the first reference "
                           "(FLUX.2 Klein only)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            seed_config_spec(),
            device_config_spec(),
            preview_config_spec(),
            *schedule_settings_config_specs(),
            *iterate_mode_config_specs(),
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
            cls.inpaint_input_spec(),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True),
        ]
