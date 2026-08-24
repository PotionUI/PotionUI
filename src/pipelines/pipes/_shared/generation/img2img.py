"""Family-agnostic img2img denoise for native generators.

One helper, ``img2img_denoise``, turns an input image plus a pre-built
:class:`Conditioning` into refined pixels by VAE-encoding the image to a
model-native latent and running a *partial* (truncated-schedule) denoise on any
:class:`~src.platform.runtime.native.engine.NativeGenerator` instance. It owns no model math
-- the engine's ``encode_image`` (pixels -> latent) and ``sample(init_latent=,
denoise_strength=)`` (noise-mix + truncated schedule) do the work; this just
sequences the three phases (encode -> sample -> decode) the same way every
generator's txt2img path sequences sample -> decode.

Both callers use it unchanged: the generator pipes' img2img *mode* (refine one
whole image) and the ``tiled_refiner`` pipe (refine one tile at a time). Keeping
it generator-instance-agnostic is what lets Anima's ``AnimaNativeGenerator`` (its
LLMAdapter forward) and the plain ``NativeGenerator`` families share it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional

import numpy as np
from PIL import Image

from src.pipelines.outputs import ImageGenerationOutput
from src.pipelines.contracts import IOType, PipeConfigSpec, PipeInputSpec
from src.pipelines.outputs import Icon
from src.pipelines.pipes._shared.generation.progress import native_step_hooks

if TYPE_CHECKING:  # avoid a hard import cycle through the engine at pipe-import time
    from src.platform.runtime.native.engine import Conditioning, NativeGenerator
    from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
    from src.pipelines.pipes._shared.generation.progress import ProgressEmitter


def img2img_denoise(
    gen: "NativeGenerator",
    image: "np.ndarray",
    conditioning: "Conditioning | dict | tuple",
    *,
    steps: int,
    seed: int,
    cfg_scale: float,
    sampler: str = "euler",
    denoise: float = 0.5,
    hooks=(),
    is_cancelled=None,
    vram_free_gb: float | None = None,
    guidance_options: dict | None = None,
    sampler_options: dict | None = None,
    step_cache_options: dict | None = None,
    schedule_settings: dict | None = None,
    sigmas=None,
) -> np.ndarray:
    """Refine ``image`` toward ``conditioning`` at ``denoise`` strength; return uint8 HWC pixels.

    ``image`` is what :meth:`NativeGenerator.encode_image` accepts -- a uint8 HWC
    array (``(H,W,3)`` or ``(B,H,W,3)``, exactly the shape ``decode`` emits) or a
    ``[-1,1]`` tensor. ``denoise`` is the fraction of the schedule to walk: ``1.0``
    is a full (txt2img-equivalent) resample, low values (0.2-0.4) preserve
    structure and only sharpen -- the tiled-refine regime. ``denoise <= 0`` is a
    no-op identity (the input image is returned unchanged), so a "refine" stage can
    be dialled to zero without a special case at the call site.

    Returns the decoded ``(B, H, W, 3)`` uint8 array (batch preserved).

    ``sigmas``, when given, is forwarded to :meth:`NativeGenerator.sample`
    unchanged -- an explicit schedule wins over ``denoise``/``steps`` there
    (see that method's docstring); ``None`` (the default) is the existing
    ``denoise``-truncated derived-schedule behaviour, untouched.
    """
    if denoise <= 0.0:
        return np.asarray(image)

    init_latent = gen.encode_image(image, vram_free_gb=vram_free_gb)
    latent = gen.sample(
        conditioning,
        init_latent.shape,
        steps=steps,
        seed=seed,
        cfg_scale=cfg_scale,
        sampler=sampler,
        denoise_strength=denoise,
        init_latent=init_latent,
        hooks=hooks,
        is_cancelled=is_cancelled,
        guidance_options=guidance_options,
        sampler_options=sampler_options,
        step_cache_options=step_cache_options,
        schedule_settings=schedule_settings,
        sigmas=sigmas,
    )
    return gen.decode(latent, vram_free_gb=vram_free_gb)


# Pixel-area budget for an img2img refine whose mode declares no output
# resolution — a source larger than this is downscaled to it so a big upload
# can't drive an untiled full-res VAE encode / DiT denoise.
_DEFAULT_IMG2IMG_AREA = 1024 * 1024


class Img2ImgGeneratorMixin:
    """Opt-in img2img mode for a :class:`BaseGeneratorPipe` subclass.

    Every native generator's txt2img loop is identical bar the family's forward
    adapter, so the img2img wiring (an ``image`` input, a ``denoise`` config, and a
    "refine this frame instead of sampling from noise" branch) is shared here rather
    than copied per family. A generator opts in by:

      1. adding ``"img2img"`` to its ``mode`` choices and appending
         :meth:`img2img_config_specs` / :meth:`img2img_input_spec`;
      2. stashing :meth:`img2img_context` into its ``GeneratorContext.extra`` (which
         already carries ``steps``/``guidance``/``sampler``);
      3. returning early on :meth:`maybe_img2img` at the top of ``generate_one``.
    """

    @staticmethod
    def img2img_config_specs(default_denoise: float = 0.55) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                "denoise", float, default_denoise,
                "img2img denoise strength (fraction of the schedule to walk)",
                required=False, min_value=0.0, max_value=1.0,
            ),
            PipeConfigSpec(
                "img2img_scale", float, 0.0,
                "Literal multiplier of the SOURCE image's own (width, height) for the img2img "
                "target size, snapped to the model's granularity same as every other path here. "
                "0 (default) is off and leaves the existing resolution-area-match / area-cap "
                "behaviour byte-identical -- set this instead of (not alongside) the mode's "
                "``resolution`` config for a true \"upscale by Nx of whatever was uploaded\" mode.",
                required=False, min_value=0.0, max_value=8.0,
            ),
        ]

    @staticmethod
    def img2img_input_spec() -> PipeInputSpec:
        return PipeInputSpec("image", IOType.IMAGE, False, "Input images for img2img mode", is_array=True)

    def img2img_context(self, pipe_input) -> dict:
        """The ``extra`` keys img2img needs; merge into ``GeneratorContext.extra``."""
        return {
            "mode": self.config.get("mode", "txt2img"),
            "denoise": float(self.config.get("denoise", 0.55)),
            "images": pipe_input.input.get("image") or [],
            # An explicit sigma schedule (e.g. Krea-2's ``refine_tail``), set
            # by a family that needs to override the denoise-truncated derived
            # schedule; ``None`` here is a byte-identical no-op for every other
            # family (see ``maybe_img2img``'s ``sigmas=`` forward below).
            "sigmas": None,
        }

    def maybe_img2img(
        self,
        gen: "NativeGenerator",
        conditioning: "Conditioning",
        ctx: "GeneratorContext",
        index: int,
        seed: int,
        progress: "ProgressEmitter",
    ) -> Optional[ImageGenerationOutput]:
        """Run an img2img refine for image ``index`` when in img2img mode with a
        source image; return ``None`` to fall through to the txt2img path.

        Reads ``steps``/``guidance``/``sampler`` from ``ctx.extra`` (the same keys
        the txt2img path uses), so a generator only adds one early-return line.
        """
        if ctx.extra.get("mode") != "img2img":
            return None
        images = ctx.extra.get("images") or []
        source = self._select_source(images, index)
        if source is None:
            return None

        # Bound the encode/denoise cost by pixel AREA while preserving the
        # source's aspect — a hard resize to the form size would squash a
        # differently-shaped source, and the raw upload size is the 17GB-encode
        # RAM bomb. With a form resolution: match its area (up or down). Without
        # one: cap the area, downscale only. Snap each axis so a non-multiple
        # can't break the patchify rearrange (same rule txt2img obeys).
        src = source.convert("RGB")
        snap_w, snap_h = self._img2img_target_size(gen, ctx, src.size)
        if (snap_w, snap_h) != src.size:
            src = src.resize((snap_w, snap_h), Image.LANCZOS)

        def on_progress(_fraction: float, step_index: int, total: int) -> None:
            progress.step(step_index + 1, total, state="IMG2IMG", icon=Icon(name="bolt", effect="pulse"))

        pixels = img2img_denoise(
            gen, np.asarray(src), conditioning,
            steps=ctx.extra["steps"], seed=seed, cfg_scale=ctx.extra["guidance"],
            sampler=ctx.extra["sampler"], denoise=ctx.extra["denoise"],
            guidance_options=ctx.extra.get("guidance_options"),
            sampler_options=ctx.extra.get("sampler_options"),
            step_cache_options=ctx.extra.get("step_cache_options"),
            schedule_settings=ctx.extra.get("schedule_settings"),
            sigmas=ctx.extra.get("sigmas"),
            hooks=native_step_hooks(gen, progress, on_progress, preview=self.config.get("preview", True)),
            is_cancelled=ctx.is_cancelled,
        )
        image = Image.fromarray(pixels[0])
        return ImageGenerationOutput(
            image=image, temporary=True, seed=seed,
            resolution=image.size, cfg=ctx.extra["guidance"], step=ctx.extra["steps"],
        )

    @staticmethod
    def _select_source(images: List[Any], index: int) -> Optional[Image.Image]:
        if not images:
            return None
        return images[index] if index < len(images) else images[-1]

    def _img2img_target_size(
        self, gen: "NativeGenerator", ctx: "GeneratorContext", src_size: tuple
    ) -> tuple:
        """The granularity-snapped ``(w, h)`` to encode the source at.

        ``img2img_scale`` (opt-in, off at 0) wins outright when set: the
        source's own ``(w, h)`` multiplied by that literal factor, snapped the
        same way as every other branch here -- a true "upscale by Nx of
        whatever was uploaded", independent of the mode's ``resolution``
        config (which a family may still declare, e.g. for its txt2img path;
        this branch simply never reads it). Absent (the default), behaviour is
        unchanged: its own aspect scaled to a pixel-area budget -- the form
        resolution's area when the mode carries one (scaling up or down),
        else a capped area (downscale only, so a small source is left
        untouched).
        """
        sw, sh = src_size
        src_area = max(1, sw * sh)
        img2img_scale = float(self.config.get("img2img_scale", 0.0) or 0.0)
        if img2img_scale > 0.0:
            return gen.snap_resolution(max(1, round(sw * img2img_scale)), max(1, round(sh * img2img_scale)))
        target_w = ctx.extra.get("width")
        target_h = ctx.extra.get("height")
        if target_w and target_h:
            scale = (int(target_w) * int(target_h) / src_area) ** 0.5
        else:
            scale = min(1.0, (self._img2img_area_cap() / src_area) ** 0.5)
        return gen.snap_resolution(max(1, round(sw * scale)), max(1, round(sh * scale)))

    def _img2img_area_cap(self) -> int:
        """Pixel-area budget when the img2img mode declares no output resolution:
        the family's own default generation area if it exposes one, else
        :data:`_DEFAULT_IMG2IMG_AREA`."""
        getter = getattr(self, "get_default_config", None)
        if callable(getter):
            res = (getter() or {}).get("resolution")
            if res:
                try:
                    w, h = (int(v) for v in str(res).lower().split("x"))
                    if w > 0 and h > 0:
                        return w * h
                except (ValueError, TypeError):
                    pass
        return _DEFAULT_IMG2IMG_AREA
