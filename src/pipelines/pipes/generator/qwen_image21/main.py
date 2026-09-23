"""Generator for the native Qwen-Image-2.1 family (txt2img / edit).

``edit`` (Qwen-Image-2.1's checkpoint-native reference-image conditioning, up
to 10 references) mirrors Qwen-Image 1.0's edit mode
(``generator/qwen``'s ``GeneratorQwenPipe.maybe_edit``) where the shapes
match: the conditioning handed in is already vision-grounded on the SAME
reference set (the edit preset wires ``media_loader``'s ``image`` into both
``prompt_encoder`` and this pipe; ``model_loader/qwen_image21``'s ``vision:
true`` loads the Qwen3-VL-8B vision tower), and this method adds the
``ref_latents`` half of the conditioning before running the full (from-noise)
sample. It diverges from 1.0 where the 2.1 checkpoint's own semantics differ:
each reference is VAE-encoded at its own area-target aspect and spliced into
the text run at the ``image_slots`` position the text encoder recorded (see
``QwenImage21TextEncoder._encode_with_images`` /
``QwenImage21DiT.build_sequence``) rather than simply appended after all the
text.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec, logger
from src.pipelines.outputs import GenerationExecutionError, ImageGenerationOutput, Icon
from src.pipelines.pipes._shared.generation.flow_generator_pipe import (
    FlowMatchGeneratorPipe,
    spectral_progressive_config_specs,
)
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.guidance_options import (
    apply_schedule_settings,
    schedule_settings_config_specs,
)
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter, native_step_hooks
from src.pipelines.pipes._shared.imaging.alpha import drop_opaque_alpha, flatten_onto
from src.platform.runtime.native.engine import Conditioning, NativeGenerator


class GeneratorQwenImage21Pipe(FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native Qwen-Image-2.1 generator (flow matching, true CFG)"
    family_tag = "GENERATOR QWEN IMAGE 2.1"

    EDIT_AREA_TARGET = 1024 * 1024
    MAX_EDIT_REFS = 10

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "txt2img",
            "steps": 40,
            "guidance": 4.0,
            "shift": None,
            "sampler": "euler",
            "resolution": "1024x1024",
            "quantity": 1,
            "seed": -1,
            "device": "cuda",
            "preview": True,
            "schedule": "",
            "schedule_options": {},
            "manual_sigmas": "",
            "detail_strength": None,
            "detail_start": None,
            "detail_end": None,
            "step_cache": {},
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=True, choices=["txt2img", "edit"]),
            PipeConfigSpec("steps", int, 40, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 4.0, "True CFG scale (Qwen true_cfg_scale)", required=False,
                           min_value=0.0, max_value=30.0),
            PipeConfigSpec("shift", float, None, "Sigma-shift override (multiplicative); blank -> resolution-dynamic mu (0.5 @ 256 tokens .. 0.9 @ 8192)", required=False),
            PipeConfigSpec("sampler", str, "euler", "Sampler: any key registered on the sampler registry "
                           "(see GET /api/sampling/catalog)", required=False),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews to the workbench during sampling", required=False),
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
            *spectral_progressive_config_specs(),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "Qwen-Image-2.1 model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            cls.img2img_input_spec(),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True),
        ]

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        apply_schedule_settings(ctx, self.config)
        return ctx

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter):
        edit = self.maybe_edit(ctx, index, seed, progress)
        out = edit if edit is not None else super().generate_one(ctx, index, seed, progress)
        out.image = drop_opaque_alpha(out.image)
        return out


    def maybe_edit(
        self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter,
    ) -> Optional[ImageGenerationOutput]:
        """Run a Qwen-Image-2.1 edit generation for image ``index``; return
        ``None`` to fall through to the txt2img path.

        Every output in the batch is conditioned on the FULL reference list
        (up to :attr:`MAX_EDIT_REFS`), same joint-conditioning contract as
        1.0's edit mode -- ``images[0]`` alone drives the output's size, every
        image is VAE-encoded in upload order, each at its own area-target
        aspect. ``image_slots`` (where the text encoder spliced each
        reference's vision tokens into the kept text sequence) rides the
        conditioning the ``prompt_encoder`` pipe already built for this SAME
        reference set; forced identical on ``uncond`` so an asymmetric splice
        point can never sneak into what CFG contrasts against.
        """
        if ctx.extra.get("mode") != "edit":
            return None
        images = (ctx.extra.get("images") or [])[:self.MAX_EDIT_REFS]
        if not images:
            raise GenerationExecutionError("Qwen-Image-2.1 edit requires a source image")

        gen: NativeGenerator = ctx.extra["generator"]
        conditioning = ctx.extra["conditioning"]
        steps = ctx.extra["steps"]
        guidance = ctx.extra["guidance"]
        sampler = ctx.extra["sampler"]

        cond_model = conditioning[index] if index < len(conditioning) else conditioning[-1]
        cond = dict(cond_model.embeds)
        uncond = dict(cond_model.n_embeds) if cond_model.n_embeds else None

        primary = flatten_onto(images[0])
        width, height = self._edit_target_size(gen, primary.size)

        ref_latents = []
        for i, image in enumerate(images):
            src = primary if i == 0 else flatten_onto(image)
            target = (width, height) if i == 0 else self._edit_target_size(gen, src.size)
            if target != src.size:
                src = src.resize(target, Image.LANCZOS)
            ref_latents.append(gen.encode_image(np.asarray(src)))

        cond["ref_latents"] = ref_latents
        image_slots = cond.get("image_slots")
        if uncond is not None:
            uncond["ref_latents"] = ref_latents
            if image_slots is not None:
                uncond["image_slots"] = image_slots
        conditioning_obj = Conditioning(cond=cond, uncond=uncond)

        latents_shape = gen.latent_shape_for(width, height)

        def on_progress(_fraction: float, step_index: int, total: int) -> None:
            progress.step(step_index + 1, total, state="EDIT", icon=Icon(name="bolt", effect="pulse"))

        logger.debug("[%s] edit image %d/%d, seed %d", self.family_tag, index + 1, ctx.quantity, seed)
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
            hooks=native_step_hooks(gen, progress, on_progress, preview=self.config.get("preview", True)),
            is_cancelled=ctx.is_cancelled,
        )
        pixels = gen.decode(latent)
        image = Image.fromarray(pixels[0])
        return ImageGenerationOutput(
            image=image, temporary=True, seed=seed,
            resolution=(width, height), cfg=guidance, step=steps,
        )

    def _edit_target_size(self, gen: NativeGenerator, src_size: tuple) -> tuple:
        """The ``(w, h)`` to VAE-encode the source at: its own aspect scaled
        to hit ``EDIT_AREA_TARGET`` exactly (up OR down), then snapped to the
        granularity ``gen.snap_resolution`` derives from this family's
        ModelSpec (16px -- see ``NativeGenerator._spatial_downscale``).
        Independent of the shared ``resolution`` config on purpose -- see the
        module docstring."""
        sw, sh = src_size
        scale = (self.EDIT_AREA_TARGET / max(1, sw * sh)) ** 0.5
        return gen.snap_resolution(max(1, round(sw * scale)), max(1, round(sh * scale)))
