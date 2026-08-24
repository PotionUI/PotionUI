"""Shared base for native flow-matching txt2img generator pipes.

``generator/flux``, ``generator/krea2``, ``generator/qwen``, ``generator/z_image``
and ``generator/anima`` all consume the ``model`` bundle from their
``model_loader/<family>`` pipe and the ``conditioning`` list from the shared
``prompt_encoder`` pipe (the mandated path -- none of these pipes re-encode
prompts), build one ``Conditioning`` per seed from the ``ConditioningModel``
role dicts, run ``NativeGenerator.sample``, decode, and emit through the
shared gallery path. The only per-family variation is:

- the generator subclass to instantiate (plain ``NativeGenerator`` for every
  family but Anima, which needs ``AnimaNativeGenerator`` to thread its
  LLMAdapter's T5 tensors -- see ``_generator_class``);
- whether a sigma-shift override applies (``supports_shift``; Krea-2 is a
  turbo model with no shift knob);
- the log tag (``family_tag``).

Real per-family defaults/config schema/inputs/outputs stay in each family's
own ``main.py`` -- only the ``build_context``/``generate_one`` mechanics (and
the ``_optional_float`` shift-parsing helper) live here.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional

from PIL import Image

from src.pipelines.outputs import ImageGenerationOutput, WarmStartGenerationOutput
from src.platform.runtime.native.engine import Conditioning, NativeGenerator
from src.platform.runtime.native.memory import make_device_plan
from src.pipelines.contracts import logger
from src.pipelines.contracts import PipeConfigSpec, PipeInput
from src.pipelines.outputs import Icon
from src.pipelines.pipes._shared.generation.generator_base import BaseGeneratorPipe, GeneratorContext
from src.pipelines.pipes._shared.generation.img2img import Img2ImgGeneratorMixin
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter, native_step_hooks

# `spectral_progressive`'s only recognised sub-keys -- everything
# `SpectralProgressiveConfig` (sampling/spectral_progressive.py) accepts, plus
# the engine's own `enabled` toggle (popped before construction; see
# NativeGenerator._spectral_progressive_config).
_SPECTRAL_PROGRESSIVE_KEYS = frozenset({
    "enabled", "scales", "delta", "power_beta", "power_amplitude", "basis", "transitions",
})


def _validate_spectral_progressive(raw: Any) -> None:
    """Eagerly validate `spectral_progressive` at config-validation time
    (``FlowMatchGeneratorPipe.validate_config``) instead of only inside
    ``NativeGenerator.sample()`` -- a malformed preset fails before a
    generation starts. Mirrors ``NativeGenerator._spectral_progressive_config``'s
    own opts handling (pop ``enabled``, tuple-ize list sub-keys) so the same
    dict that would be accepted at runtime is what gets validated here.
    A falsy/absent ``raw`` (the off state) is a no-op.
    """
    if not raw:
        return
    if not isinstance(raw, dict):
        raise ValueError(f"'spectral_progressive' must be a dict, got {type(raw).__name__}")
    unknown = set(raw) - _SPECTRAL_PROGRESSIVE_KEYS
    if unknown:
        raise ValueError(f"'spectral_progressive' has unknown keys: {sorted(unknown)}")
    opts = dict(raw)
    opts.pop("enabled", None)
    if not opts:
        return
    for key in ("scales", "transitions"):
        if isinstance(opts.get(key), list):
            opts[key] = tuple(opts[key])
    from src.platform.runtime.native.sampling.spectral_progressive import SpectralProgressiveConfig
    try:
        SpectralProgressiveConfig(**opts)
    except TypeError as exc:
        raise ValueError(f"'spectral_progressive': {exc}") from exc


def iterate_mode_config_specs() -> List[PipeConfigSpec]:
    """Shared ``iterate_mode`` (trajectory warm-start) declaration. A family
    splices this into its own ``configuration()`` the same way it splices
    ``Img2ImgGeneratorMixin.img2img_config_specs`` -- ``FlowMatchGeneratorPipe.
    validate_config`` below validates the key regardless, so a family that
    hasn't (yet) spliced it in still gets type-checked, just without the
    per-parameter choices/range treatment ``configuration()`` gives every
    other declared knob. See ``NativeGenerator._plan_warm_start`` (engine.py)
    for the eligibility gate this knob feeds."""
    return [
        PipeConfigSpec(
            "iterate_mode", bool, False,
            "Iterate mode: resume a cached mid-trajectory latent instead of a "
            "cold denoise when a follow-up generation's conditioning barely "
            "changed, skipping the steps that already agree. Off by default "
            "(needs GPU validation). Only engages on the 'euler' sampler, "
            "txt2img (no input image), with APG momentum unset/0 -- every "
            "other combination silently falls back to a normal cold run.",
            required=False,
        ),
    ]


def spectral_progressive_config_specs() -> List[PipeConfigSpec]:
    """Shared ``spectral_progressive`` declaration -- same splice contract as
    :func:`iterate_mode_config_specs`. See
    ``NativeGenerator._spectral_progressive_config``/
    ``_sample_spectral_progressive`` (engine.py) for the eligibility gate and
    ``sampling/spectral_progressive.py`` for the math/sub-key semantics."""
    return [
        PipeConfigSpec(
            "spectral_progressive", dict, None,
            "Spectral Progressive Diffusion (opt-in prototype): denoise the "
            "early, high-sigma steps at a reduced latent resolution and grow "
            "to full resolution as the schedule's frequency bands stop being "
            "noise-dominated. {'scales': [0.5, 1.0], 'delta': 0.01, "
            "'power_beta': 2.5, 'power_amplitude': 1.0, 'basis': 'fft'|'dct', "
            "'transitions': null, 'enabled': true} -- usually only 'scales' is "
            "worth setting, the rest derive a sensible schedule from it. Off "
            "by default (needs GPU validation). Only engages on a "
            "constant-shift, 4D-image, txt2img family (Flux2/Klein, "
            "Z-Image); every dynamic-mu family (Flux1, Krea-2), video "
            "family, or img2img run silently falls back to the normal path.",
            required=False,
        ),
    ]


def _attach_nag(cond: dict, uncond: dict | None, config: dict) -> dict:
    """Attach NAG's negative context + params to the cond dict, read back by
    ``NativeGenerator._make_forward`` (see ``src/platform/runtime/native/nag.py``
    and ``arch/krea2/model.py`` -- the only arch consuming these keys today).
    Mirrors ``generator/txt2vid_wan22``'s ``_attach_nag``. No-op (returns
    ``cond`` unchanged) when ``nag_scale <= 1.0`` or there's no negative
    conditioning -- keeps every other flow-matching family (Flux/Qwen/Z-Image/
    Anima) byte-identical, since none of them expose a ``nag_scale`` config
    key nor consume ``nag_context`` in their arch forward.
    """
    nag_scale = float(config.get("nag_scale", 1.0))
    if nag_scale <= 1.0 or uncond is None:
        return cond
    return {
        **cond,
        "nag_context": uncond["context"],
        "nag_attention_mask": uncond.get("attention_mask"),
        "nag": {
            "scale": nag_scale,
            "tau": float(config.get("nag_tau", 3.5)),
            "alpha": float(config.get("nag_alpha", 0.5)),
        },
    }


class FlowMatchGeneratorPipe(Img2ImgGeneratorMixin, BaseGeneratorPipe):
    """``build_context``/``generate_one`` shared by the native flow-matching
    families. Subclasses set ``family_tag``/``supports_shift`` and override
    ``_generator_class`` (only Anima needs to)."""

    family_tag: str = "GENERATOR"
    supports_shift: bool = True

    def _generator_class(self):
        """The ``NativeGenerator`` subclass to instantiate. Overridden by Anima
        to return ``AnimaNativeGenerator``; a plain method (not a class
        attribute) so a family's own ``main.py`` module can still be patched
        in tests the same way it always was."""
        return NativeGenerator

    # -- context -----------------------------------------------------------

    def build_context(self, pipe_input: PipeInput) -> GeneratorContext:
        bundle = pipe_input.input["model"]
        conditioning = pipe_input.input["conditioning"] or []
        seeds = pipe_input.input.get("seed", [])

        # Missing-key fallbacks come from the family's own declared defaults —
        # they genuinely differ per family (turbo Krea-2: steps 8 / guidance 0;
        # Flux: 24 / 6.0; ...), so a flat literal here would silently change
        # semantics for any family whose config arrives without the key.
        defaults = self.get_default_config()
        steps = int(self.config.get("steps", defaults.get("steps", 20)))
        guidance = float(self.config.get("guidance", defaults.get("guidance", 6.0)))
        sampler = self.config.get("sampler", defaults.get("sampler", "euler"))
        quantity = int(self.config.get("quantity", defaults.get("quantity", 1)))
        device = self.config.get("device", defaults.get("device", "cuda"))

        resolution = str(self.config.get("resolution", defaults.get("resolution", "1024x1024"))).split("x")
        width, height = int(resolution[0]), int(resolution[1])

        # Optional TrueCFG knobs (cfg_zero_star / zero_init_steps), surfaced to
        # presets as a nested ``guidance_options`` dict. Kept distinct from the
        # scalar ``guidance`` (CFG scale) key. Empty/missing -> engine defaults,
        # so families that don't set it are unaffected.
        guidance_options = self.config.get("guidance_options") or {}

        # Sampler-algorithm options (e.g. ``{"eta": 0.5}`` for euler_sde,
        # ``{"restart_count": 2}`` for euler_restart) and FBCache step-skipping
        # options, both opaque dicts forwarded to ``NativeGenerator.sample()``
        # unchanged. Missing/empty -> None, a byte-identical no-op (same
        # treatment as ``guidance_options``). ``step_cache`` (not
        # ``step_cache_options``) is the preset-facing config key -- mapped here
        # to the name ``denoise()`` actually expects.
        sampler_options = self.config.get("sampler_options") or None
        step_cache_options = self.config.get("step_cache") or None

        # Schedule-shaping knobs (alternate sigma schedule + detail-daemon warp):
        # a nested dict whose recognised keys (schedule / schedule_options /
        # detail_strength / detail_start / detail_end) the engine whitelists into
        # the ModelSpec sampling_settings denoise() reads. Missing/empty -> None,
        # a byte-identical no-op (same treatment as sampler_options).
        schedule_settings = self.config.get("schedule_settings") or None

        # Trajectory warm-start ("Iterate mode ⚡"): resume from a cached
        # mid-trajectory latent when a follow-up generation barely changes the
        # conditioning. Opt-in; the engine gates it to euler + txt2img and stamps
        # resume metadata. Default off -> byte-identical to today.
        iterate_mode = bool(self.config.get("iterate_mode", False))

        # Spectral Progressive Diffusion (opt-in prototype): a nested config
        # {scales, delta, basis, ...}. The engine gates it to eligible families
        # (4D image latents, txt2img); ineligible families no-op with a log.
        spectral_progressive = self.config.get("spectral_progressive") or None

        device_plan = make_device_plan(preferred=device, dit_gb=bundle.dit.estimated_vram_gb)
        generator = self._generator_class()(bundle.dit, bundle.te_encoder, bundle.vae, device_plan)

        # Snap to the DiT's patch granularity so a non-multiple request can't
        # break the patchify rearrange (granularity varies per family: Flux1
        # 16px / Flux2 32px, Krea-2/Qwen/Z-Image/Anima 16px).
        width, height = generator.snap_resolution(width, height)

        # Optional sigma-shift override. Not every family exposes one (Krea-2
        # is a turbo model with no shift knob).
        if self.supports_shift:
            shift_override = self._optional_float(self.config.get("shift"))
            if shift_override is not None:
                generator.spec = replace(
                    generator.spec,
                    sampling_settings={**generator.spec.sampling_settings, "shift": shift_override},
                )

        logger.info("[%s] %s/%s: %d img @ %dx%d, %d steps, guidance %.2f, sampler %s",
                    self.family_tag, generator.spec.family, generator.spec.variant, quantity, width, height,
                    steps, guidance, sampler)

        return GeneratorContext(
            quantity=quantity,
            input_seeds=seeds,
            extra={
                "generator": generator,
                "conditioning": conditioning,
                "steps": steps,
                "guidance": guidance,
                "guidance_options": guidance_options,
                "sampler_options": sampler_options,
                "step_cache_options": step_cache_options,
                "schedule_settings": schedule_settings,
                "iterate_mode": iterate_mode,
                "spectral_progressive": spectral_progressive,
                "sampler": sampler,
                "width": width,
                "height": height,
                **self.img2img_context(pipe_input),
            },
        )

    # -- per-seed generation ----------------------------------------------

    def generate_one(self, ctx: GeneratorContext, index: int, seed: int, progress: ProgressEmitter) -> ImageGenerationOutput:
        gen: NativeGenerator = ctx.extra["generator"]
        conditioning = ctx.extra["conditioning"]
        steps = ctx.extra["steps"]
        guidance = ctx.extra["guidance"]
        sampler = ctx.extra["sampler"]
        width, height = ctx.extra["width"], ctx.extra["height"]

        cond_model = conditioning[index] if index < len(conditioning) else conditioning[-1]
        uncond = cond_model.n_embeds or None
        conditioning_obj = Conditioning(
            cond=_attach_nag(cond_model.embeds, uncond, self.config),
            uncond=uncond,
        )

        img2img = self.maybe_img2img(gen, conditioning_obj, ctx, index, seed, progress)
        if img2img is not None:
            return img2img

        # gen.latent_shape_for is the single owner of the per-family VAE-latent
        # math (4D 8x/16x for Flux, 5D causal-3D for Qwen/Krea-2/Anima, ...).
        latents_shape = gen.latent_shape_for(width, height)

        def on_progress(_fraction: float, step_index: int, total: int) -> None:
            progress.step(step_index + 1, total, state="TXT2IMG", icon=Icon(name="bolt", effect="pulse"))

        logger.debug("[%s] image %d/%d, seed %d", self.family_tag, index + 1, ctx.quantity, seed)
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
            warm_start=ctx.extra.get("iterate_mode", False),
            spectral_progressive=ctx.extra.get("spectral_progressive"),
            hooks=native_step_hooks(gen, progress, on_progress, preview=self.config.get("preview", True)),
            is_cancelled=ctx.is_cancelled,
        )
        # Iterate mode: when sample() actually resumed from a cached trajectory,
        # surface it as a pipe_artifact + a status line. A cold run leaves
        # gen.last_warm_start None/absent and emits nothing.
        warm = getattr(gen, "last_warm_start", None)
        if warm:
            progress.emit(WarmStartGenerationOutput(
                index=index,
                resume_step=warm["resume_step"],
                total_steps=warm["total_steps"],
                steps_skipped=warm["steps_skipped"],
                similarity=warm["similarity"],
            ))
            progress.state(
                f"Iterate mode: resumed at step {warm['resume_step']}/{warm['total_steps']}",
                icon=Icon(name="bolt", effect="pulse"),
            )

        pixels = gen.decode(latent)  # (B, H, W, 3) uint8
        image = Image.fromarray(pixels[0])

        return ImageGenerationOutput(
            image=image, temporary=True, seed=seed,
            resolution=(width, height), cfg=guidance, step=steps,
        )

    # -- config validation ---------------------------------------------------

    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> None:
        """Cross-field validation for the two opt-in engine knobs every
        flow-matching family shares (``iterate_mode``/``spectral_progressive``).
        Lives here, not in each family's own ``configuration()``, so it applies
        uniformly even to a family whose ``configuration()`` doesn't (yet)
        carry these keys -- ``validate_pipe_configuration``
        (``src/features/generation/generation.py``) calls this hook on the
        fully resolved config (declared specs applied AND unknown/passthrough
        keys preserved) for every pipe, regardless of what its own
        ``configuration()`` declares.
        """
        iterate_mode = config.get("iterate_mode")
        if iterate_mode is not None and not isinstance(iterate_mode, bool):
            raise ValueError(f"'iterate_mode' must be a bool, got {type(iterate_mode).__name__}")
        _validate_spectral_progressive(config.get("spectral_progressive"))

    # -- helpers -----------------------------------------------------------

    def _optional_float(self, raw: Any) -> Optional[float]:
        """Parse an optional numeric config value; '', None and Jinja's 'None' -> None."""
        if raw in (None, "", "None"):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning("[%s] ignoring non-numeric override %r", self.family_tag, raw)
            return None
