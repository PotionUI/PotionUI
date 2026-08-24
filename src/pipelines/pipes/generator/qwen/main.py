"""Generator for the native Qwen-Image family (txt2img / img2img / edit).

Consumes the ``model`` bundle from ``model_loader/qwen`` and the ``conditioning``
list from the shared ``prompt_encoder`` pipe (the mandated pipe path — this pipe
never re-encodes prompts). Per seed it builds a ``Conditioning`` from the
``ConditioningModel`` role dicts, runs ``NativeGenerator.sample`` (flow-matching
denoise with the preset's sampler and TRUE classifier-free guidance — the
Qwen-Image spec's guidance mode is ``"cfg"``, so the sampler runs a cond + uncond
pass), decodes, and emits the image through the shared gallery path.

Unlike Flux's embedded (distilled) guidance, ``cfg_scale`` here is the Qwen
"true_cfg_scale" (~4.0), and the generator MUST pass a real uncond — hence the
conditioning is built with ``n_embeds`` (the encoded negative prompt).

``build_context``/``generate_one`` (shared by every native flow-matching family)
live in ``FlowMatchGeneratorPipe``; this module carries Qwen-Image's own config
schema/defaults, PLUS the ``edit`` mode (Qwen-Image-Edit):

Unlike ``img2img`` (a truncated-schedule denoise of a NOISED copy of the source),
``edit`` runs a full generation from noise whose text conditioning was already
vision-grounded on the source (``model_loader/qwen``'s ``vision: true`` + the
edit preset's ``prompt_encoder`` wiring an ``image`` input — see ``qwen_clip.py``)
and whose DiT call ALSO receives the source's clean VAE latent as
``ref_latents`` — the checkpoint's in-context edit path (``QwenImageDiT.forward``,
dormant until this stage). Mechanism cross-checked against ComfyUI's
``TextEncodeQwenImageEdit`` + ``comfy/ldm/qwen_image/model.py``: the source is
resized to a fixed ~1 megapixel AREA TARGET (aspect preserved, scaled up or
down to hit it, not merely capped) — deliberately NOT
``Img2ImgGeneratorMixin._img2img_target_size`` (that helper reads
``ctx.extra["width"/"height"]``, which come from the generic ``resolution``
config every mode shares; edit mode declares no ``resolution`` field of its
own, but a stray value on that shared key must not silently steer the resize
anyway — ``_edit_target_size`` below hardcodes the area target instead).

TE eviction: a real edit-mode GPU run OOM'd with the 8.35GB Qwen2.5-VL
TE still resident alongside the 19.12GB edit DiT — by the time THIS pipe runs,
``prompt_encoder`` has already produced the conditioning every mode needs, so
the TE is dead weight regardless of mode (not edit-specific — txt2img/img2img
carry the exact same waste, just hadn't OOM'd yet). ``build_context`` releases
it via ``bundle.te_cache_key`` + ``models.evict_dead_weight`` — the same
mechanism ``latent_upscaler/ltx/main.py``'s ``_unload_idle_te`` established,
mirrored here rather than reinvented. Safe with the prompt-embed cache: a hit
never touches the TE module at all; a miss just reloads it from disk
(``MODELS.acquire`` cache-misses), the same accepted cost the LTX pattern
already establishes. Qwen-specific (not lifted into the shared
``FlowMatchGeneratorPipe``) because Krea-2 is mid-rebuild and Flux/Wan/etc.
weren't part of this investigation — see the report for the case this
should generalize to next.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput, PipeInputSpec, PipeOutputSpec, PipeConfigSpec, logger
from src.pipelines.outputs import GenerationExecutionError, ImageGenerationOutput, Icon
from src.pipelines.pipes._shared.generation.flow_generator_pipe import FlowMatchGeneratorPipe, iterate_mode_config_specs
from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter, native_step_hooks
from src.platform.runtime.native.engine import Conditioning, NativeGenerator


class GeneratorQwenPipe(FlowMatchGeneratorPipe):
    name = "generator"
    description = "Native Qwen-Image generator (flow matching, true CFG)"
    family_tag = "GENERATOR QWEN"

    # ComfyUI TextEncodeQwenImageEdit's fixed resize target for the VAE-encoded
    # reference latent (aspect preserved, scaled up OR down to hit this area —
    # NOT a downscale-only cap). Independent of the shared "resolution" config.
    EDIT_AREA_TARGET = 1024 * 1024

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "txt2img",
            "steps": 20,
            "guidance": 4.0,
            "shift": None,
            "sampler": "euler",
            "resolution": "1024x1024",
            "quantity": 1,
            "seed": -1,
            "device": "cuda",
            "denoise": 0.55,
            "preview": True,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("mode", str, "txt2img", "Generation mode", required=True,
                           choices=["txt2img", "img2img", "edit"]),
            *cls.img2img_config_specs(default_denoise=0.55),
            PipeConfigSpec("steps", int, 20, "Denoising steps", required=False, min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 4.0, "True CFG scale (Qwen true_cfg_scale)", required=False,
                           min_value=0.0, max_value=30.0),
            PipeConfigSpec("shift", float, None, "Sigma-shift override; blank -> spec default (1.15)", required=False),
            PipeConfigSpec("sampler", str, "euler", "Sampler", required=False,
                           choices=["euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm"]),
            PipeConfigSpec("resolution", str, "1024x1024", "Resolution (WxH)", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images", required=False, min_value=1, max_value=10),
            PipeConfigSpec("seed", int, -1, "Random seed", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("preview", bool, True, "Emit live latent previews to the workbench during sampling", required=False),
            # Only txt2img engages this (see NativeGenerator._plan_warm_start) --
            # img2img/edit always pass init_latent and silently fall back to a
            # cold run, same as an ineligible sampler.
            *iterate_mode_config_specs(),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("model", IOType.MODEL, True, "Qwen-Image model bundle", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning (per image)", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            cls.img2img_input_spec(),
            PipeInputSpec("MODELS", IOType.SERVICE, False,
                          "Model lifecycle service, to release the idle TE's VRAM", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True),
        ]

    # -- TE eviction -----------------------------------------------

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        ctx = super().build_context(pipe_input)
        self._release_idle_te(pipe_input)
        return ctx

    def _release_idle_te(self, pipe_input: PipeInput) -> None:
        """Evict the TE's MODELS cache entry — see the module docstring's "TE
        eviction" section for the full rationale; mirrors ``latent_upscaler/
        ltx/main.py``'s ``_unload_idle_te`` (same ``bundle.te_cache_key`` +
        ``models.evict_dead_weight`` mechanism).

        Best-effort and silent: a missing ``te_cache_key`` (a bundle built
        outside the MODELS cache, e.g. isolated pipe tests), a missing
        ``MODELS`` service, or an eviction that raises are all treated as
        "nothing to do" — this is a VRAM optimisation, never something a
        generation should fail over.
        """
        bundle = pipe_input.input.get("model")
        models = pipe_input.input.get("MODELS")
        key = getattr(bundle, "te_cache_key", None)
        if not key or models is None:
            return
        evict = getattr(models, "evict_dead_weight", None)
        if not callable(evict):
            return
        try:
            evict(key)
        except Exception:  # pragma: no cover - best-effort; never fail the pipe over this
            logger.debug("[%s] TE eviction failed for key=%r", self.family_tag, key, exc_info=True)

    # -- edit mode -----------------------------------------

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter):
        edit = self.maybe_edit(ctx, index, seed, progress)
        if edit is not None:
            return edit
        return super().generate_one(ctx, index, seed, progress)

    def maybe_edit(
        self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter,
    ) -> Optional[ImageGenerationOutput]:
        """Run a Qwen-Image-Edit generation for image ``index`` when in ``edit``
        mode with 1-3 source images; return ``None`` to fall through to the
        txt2img/img2img path.

        The ``conditioning`` handed in must already be vision-grounded on the
        SAME reference set (the edit preset wires ``media_loader``'s ``image``
        into ``prompt_encoder`` too, and ``QwenClipTextEncoder.forwards_full_
        image_batch`` forwards the whole list rather than picking one per
        index) — this method does not re-encode text, it only adds the
        ``ref_latents`` half of the conditioning and runs the full
        (from-noise) sample.

        Every output in the batch is conditioned on the FULL reference list —
        not one reference picked per ``index`` — matching how the vision tower
        and the DiT's ``ref_latents`` loop (``QwenImageDiT.forward``) both
        already treat the set as shared. ``images[0]`` is the primary: it
        alone drives the output's size; every image (including it) is
        VAE-encoded in upload order into its own ``ref_latents`` entry, each
        independently resized to its own area-target aspect (the DiT packs
        each ref at its own ``pack_latents(ref, index=...)`` offset, so a
        reference's own aspect need not match the output canvas).
        """
        if ctx.extra.get("mode") != "edit":
            return None
        images = ctx.extra.get("images") or []
        if not images:
            raise GenerationExecutionError("Qwen-Image Edit requires a source image")

        gen: NativeGenerator = ctx.extra["generator"]
        conditioning = ctx.extra["conditioning"]
        steps = ctx.extra["steps"]
        guidance = ctx.extra["guidance"]
        sampler = ctx.extra["sampler"]

        cond_model = conditioning[index] if index < len(conditioning) else conditioning[-1]
        cond = dict(cond_model.embeds)
        uncond = dict(cond_model.n_embeds) if cond_model.n_embeds else None

        primary = images[0].convert("RGB")
        width, height = self._edit_target_size(gen, primary.size)

        ref_latents = []
        for i, image in enumerate(images):
            src = primary if i == 0 else image.convert("RGB")
            target = (width, height) if i == 0 else self._edit_target_size(gen, src.size)
            if target != src.size:
                src = src.resize(target, Image.LANCZOS)
            ref_latents.append(gen.encode_image(np.asarray(src)))

        cond["ref_latents"] = ref_latents
        if uncond is not None:
            uncond["ref_latents"] = ref_latents
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
        """The ``(w, h)`` to VAE-encode the source at: its own aspect scaled to
        hit ``EDIT_AREA_TARGET`` exactly (up OR down), then snapped to the
        DiT's patch granularity. Independent of the shared ``resolution``
        config on purpose — see the module docstring."""
        sw, sh = src_size
        scale = (self.EDIT_AREA_TARGET / max(1, sw * sh)) ** 0.5
        return gen.snap_resolution(max(1, round(sw * scale)), max(1, round(sh * scale)))
