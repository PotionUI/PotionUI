# Derived from: https://github.com/lbouaraba/comfyui-krea2edit (Apache-2.0)
from __future__ import annotations

import logging
from typing import List

from PIL import Image

from src.pipelines.contracts import IOType, PipeConfigSpec, PipeInputSpec, PipeOutputSpec
from src.pipelines.outputs import GenerationExecutionError, Icon, ImageGenerationOutput
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter, native_step_hooks
from src.pipelines.pipes.generator.krea2.main import GeneratorKrea2Pipe
from src.pipelines.pipes.generator.krea2_edit.geometry import (
    area_fit_size,
    fit_ref_mask,
    fit_source_area,
    fit_source_image,
)
from src.platform.runtime.native.arch.krea2.placement import REF_POSITIONS
from src.platform.runtime.native.engine import Conditioning

logger = logging.getLogger(__name__)


class GeneratorKrea2EditPipe(GeneratorKrea2Pipe):
    name = "generator"
    description = "Native Krea-2 instruction-based edit generator (in-context source tokens)"
    family_tag = "GENERATOR KREA2 EDIT"

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        specs = [spec for spec in super().configuration() if spec.name not in ("mode", "denoise", "refine_tail")]
        specs += [
            PipeConfigSpec("fit_mode", str, "fit", "How a source fits a mismatched output AR",
                           required=False, choices=["fit", "crop"]),
            PipeConfigSpec("ref_boost", float, 1.0, "Reference-fidelity dial for the last (subject) ref",
                           required=False, min_value=0.0, max_value=1000.0),
            PipeConfigSpec("ref_boost_a", float, 1.0, "Reference-fidelity dial for the first (scene) ref",
                           required=False, min_value=0.0, max_value=1000.0),
            PipeConfigSpec("ref_style", str, "standard", "Reference scheme the edit LoRA was trained with",
                           required=False, choices=["standard", "ostris"]),
            PipeConfigSpec("ref_pixels", int, 1048576, "Pixel-area cap for a reference (ostris style)",
                           required=False, min_value=65536, max_value=4194304),
            PipeConfigSpec("match_source", bool, False, "Take the output shape from the first source at the "
                           "resolution's pixel area", required=False),
            PipeConfigSpec("output_pixels", int, 0, "Size the output from the first source at up to this "
                           "pixel area; 0 uses the resolution (ostris style)",
                           required=False, min_value=0, max_value=16777216),
            PipeConfigSpec("ref_position", str, "aligned", "Where the last (subject) ref sits relative to the output",
                           required=False, choices=list(REF_POSITIONS)),
            PipeConfigSpec("ref_position_a", str, "aligned", "Where the first (scene) ref sits relative to the output",
                           required=False, choices=list(REF_POSITIONS)),
        ]
        return specs

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "Krea-2 model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, True, "Source image to edit (scene ref)", is_array=True),
            PipeInputSpec("image_b", IOType.IMAGE, False, "Optional second reference (subject) for a multi-ref edit", is_array=True),
            PipeInputSpec("mask", IOType.IMAGE, False,
                          "Optional reference-fidelity region mask painted on the source image "
                          "(ignored when a second reference is present)", is_array=True),
            PipeInputSpec("mask_b", IOType.IMAGE, False,
                          "Optional reference-fidelity region mask painted on the second reference; "
                          "wins over mask because the boost always targets the last reference", is_array=True),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the idle text encoder's VRAM before ref-doubled sampling",
                          is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [PipeOutputSpec("image", IOType.IMAGE, "Edited images", is_array=True)]

    def build_context(self, pipe_input) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        ctx.extra["images_b"] = pipe_input.input.get("image_b") or []
        ctx.extra["masks"] = pipe_input.input.get("mask") or []
        ctx.extra["masks_b"] = pipe_input.input.get("mask_b") or []
        return ctx

    @staticmethod
    def _pick(items: list, index: int):
        return items[index] if index < len(items) else items[-1]

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter) -> ImageGenerationOutput:
        gen = ctx.extra["generator"]
        conditioning = ctx.extra["conditioning"]
        steps = ctx.extra["steps"]
        guidance = ctx.extra["guidance"]
        sampler = ctx.extra["sampler"]
        width, height = ctx.extra["width"], ctx.extra["height"]
        fit_mode = self.config.get("fit_mode", "fit")
        ref_boost = float(self.config.get("ref_boost", 1.0))
        ref_boost_a = float(self.config.get("ref_boost_a", 1.0))
        ref_position = self.config.get("ref_position", "aligned")
        ref_position_a = self.config.get("ref_position_a", "aligned")

        images = ctx.extra.get("images") or []
        if not images:
            raise GenerationExecutionError("Krea 2 Edit requires a source image")
        images_b = ctx.extra.get("images_b") or []
        sources = [self._pick(images, index)]
        if images_b:
            sources.append(self._pick(images_b, index))

        cond_model = self._pick(conditioning, index)
        cond = dict(cond_model.embeds)
        uncond = dict(cond_model.n_embeds) if cond_model.n_embeds else None

        ostris = self.config.get("ref_style", "standard") == "ostris"
        output_pixels = int(self.config.get("output_pixels", 0) or 0)
        if output_pixels:
            width, height = gen.snap_resolution(*area_fit_size(*sources[0].size, output_pixels))
        elif self.config.get("match_source", False):
            width, height = self._img2img_target_size(gen, ctx, sources[0].size)
        if ostris:
            ref_pixels = int(self.config.get("ref_pixels", 1048576))
            ref_arrays = [fit_source_area(s, ref_pixels) for s in sources]
        else:
            ref_arrays = [fit_source_image(s, width, height, fit_mode) for s in sources]
        ref_latents = [gen.encode_image(array) for array in ref_arrays]
        ref_value = ref_latents if len(ref_latents) > 1 else ref_latents[0]
        reference_options = {
            "ref_latents": ref_value,
            "ref_boost": ref_boost,
            "ref_boost_a": ref_boost_a,
            "ref_position": "origin" if ostris else ref_position,
            "ref_position_a": "origin" if ostris else ref_position_a,
        }
        if ostris:
            reference_options["ref_timestep_zero"] = True
            reference_options["ref_isolated"] = True

        last_masks = [] if ostris else (ctx.extra.get("masks_b") or []) if images_b else (ctx.extra.get("masks") or [])
        if last_masks:
            reference_options["ref_boost_mask"] = fit_ref_mask(
                self._pick(last_masks, index), width, height, fit_mode, source_size=sources[-1].size
            )

        cond.update(reference_options)
        if uncond is not None:
            uncond.update(reference_options)
        conditioning_obj = Conditioning(cond=cond, uncond=uncond)

        latents_shape = gen.latent_shape_for(width, height)

        def on_progress(_fraction: float, step_index: int, total: int) -> None:
            progress.step(step_index + 1, total, state="EDIT", icon=Icon(name="bolt", effect="pulse"))

        logger.info("[%s] image %d/%d, seed %d", self.family_tag, index + 1, ctx.quantity, seed)
        latent = gen.sample(
            conditioning_obj,
            latents_shape,
            steps=steps,
            seed=seed,
            cfg_scale=guidance,
            sampler=sampler,
            guidance_options=ctx.extra.get("guidance_options"),
            sampler_options=ctx.extra.get("sampler_options"),
            step_cache_options=ctx.extra.get("step_cache_options"),
            schedule_settings=ctx.extra.get("schedule_settings"),
            hooks=native_step_hooks(gen, progress, on_progress, preview=self.config.get("preview", True),
                                    extra=self.extra_step_hooks()),
            is_cancelled=ctx.is_cancelled,
        )
        self._emit_lora_window_diagnostics(progress)
        pixels_out = gen.decode(latent)
        image = Image.fromarray(pixels_out[0])
        return ImageGenerationOutput(
            image=image, temporary=True, seed=seed,
            resolution=(width, height), cfg=guidance, step=steps,
        )
