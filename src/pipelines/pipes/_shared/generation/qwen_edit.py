from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PIL import Image

from src.pipelines.contracts import logger
from src.pipelines.outputs import GenerationExecutionError, ImageGenerationOutput, Icon
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter, native_step_hooks
from src.pipelines.pipes._shared.imaging.fit import FIT_MODES, fit_image
from src.platform.runtime.native.engine import Conditioning, NativeGenerator


class QwenEditMixin:
    EDIT_AREA_TARGET = 1024 * 1024
    MAX_EDIT_REFS: Optional[int] = None
    EDIT_MISSING_SOURCE_MESSAGE = "Qwen-Image Edit requires a source image"
    MIRRORED_EDIT_KEYS: Tuple[str, ...] = ()

    def normalise_edit_image(self, image: Image.Image) -> Image.Image:
        return image.convert("RGB")

    def maybe_edit(
        self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter,
    ) -> Optional[ImageGenerationOutput]:
        if ctx.extra.get("mode") != "edit":
            return None
        images = ctx.extra.get("images") or []
        if self.MAX_EDIT_REFS is not None:
            images = images[:self.MAX_EDIT_REFS]
        if not images:
            raise GenerationExecutionError(self.EDIT_MISSING_SOURCE_MESSAGE)

        gen: NativeGenerator = ctx.extra["generator"]
        conditioning = ctx.extra["conditioning"]
        steps = ctx.extra["steps"]
        guidance = ctx.extra["guidance"]
        sampler = ctx.extra["sampler"]

        cond_model = conditioning[index] if index < len(conditioning) else conditioning[-1]
        cond = dict(cond_model.embeds)
        uncond = dict(cond_model.n_embeds) if cond_model.n_embeds else None

        primary = self.normalise_edit_image(images[0])
        fit = self.edit_fit()
        if fit in FIT_MODES:
            width, height = int(ctx.extra["width"]), int(ctx.extra["height"])
            primary = fit_image(primary, (width, height), fit)
        else:
            width, height = self._edit_target_size(gen, primary.size)

        ref_latents = []
        for i, image in enumerate(images):
            src = primary if i == 0 else self.normalise_edit_image(image)
            target = (width, height) if i == 0 else self._edit_target_size(gen, src.size)
            if target != src.size:
                src = src.resize(target, Image.LANCZOS)
            ref_latents.append(gen.encode_image(np.asarray(src)))

        cond["ref_latents"] = ref_latents
        if uncond is not None:
            uncond["ref_latents"] = ref_latents
            for key in self.MIRRORED_EDIT_KEYS:
                if cond.get(key) is not None:
                    uncond[key] = cond[key]
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
            hooks=native_step_hooks(gen, progress, on_progress, preview=self.config.get("preview", True),
                                    decoder_path=self.config.get("preview_decoder") or None),
            is_cancelled=ctx.is_cancelled,
        )
        pixels = gen.decode(latent)
        image = Image.fromarray(pixels[0])
        return ImageGenerationOutput(
            image=image, temporary=True, seed=seed,
            resolution=(width, height), cfg=guidance, step=steps,
        )

    def edit_fit(self) -> str:
        fit = str(self.config.get("fit") or "match").strip().lower()
        return fit if fit in FIT_MODES else "match"

    def _edit_target_size(self, gen: NativeGenerator, src_size: tuple) -> tuple:
        sw, sh = src_size
        scale = (self.EDIT_AREA_TARGET / max(1, sw * sh)) ** 0.5
        return gen.snap_resolution(max(1, round(sw * scale)), max(1, round(sh * scale)))
