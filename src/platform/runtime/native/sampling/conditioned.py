"""Pre-noised denoise entry for token-conditioned (masked-timestep) sampling.

``denoise()`` owns the flow-matching init mix ``x = sigma0*noise +
(1-sigma0)*latents`` — a GLOBAL blend that cannot express per-token
conditioning strengths. Token-conditioned generation (LTX keyframes /
IC-LoRA references) mixes its own init per token (see
``src/pipelines/pipes/generator/video_ltx/conditioning.mix_initial_noise``) and enters
the sampler here, with the schedule/guidance/sampler machinery shared with
``denoise`` (``denoise_loop.py`` stays untouched, per its module contract).
"""

from __future__ import annotations

import logging
import math

import torch

from .denoise_loop import SAMPLERS, _CachingGuidance, make_guidance
from .flow_schedule import build_sigmas
from .hooks import with_numerics_watchdog
from .step_cache import StepCacheSet

logger = logging.getLogger(__name__)

Tensor = torch.Tensor


def conditioned_sigmas(
    steps: int,
    sampling_settings: dict,
    *,
    denoise_strength: float = 1.0,
    image_seq_len: int | None = None,
) -> Tensor:
    """The exact sigma schedule ``denoise_prenoised`` will run — callers use
    ``sigmas[0]`` to mix their pre-noised init at the true starting noise level
    (1.0 for a full ``denoise_strength=1.0`` schedule, lower when truncated).

    Reads the same optional schedule-shaping knobs as ``denoise_loop.denoise``
    (``schedule``/``schedule_options``/``detail_strength``/``detail_start``/
    ``detail_end``) from ``sampling_settings``; all default to build_sigmas's
    own no-op defaults, so existing callers are unaffected.
    """
    return build_sigmas(
        steps,
        shift=sampling_settings.get("shift"),
        base_shift=sampling_settings.get("base_shift"),
        max_shift=sampling_settings.get("max_shift"),
        dynamic_shift=sampling_settings.get("dynamic_shift"),
        fixed_mu=sampling_settings.get("fixed_mu"),
        image_seq_len=image_seq_len,
        denoise=denoise_strength,
        schedule=sampling_settings.get("schedule"),
        schedule_options=sampling_settings.get("schedule_options"),
        detail_strength=sampling_settings.get("detail_strength", 0.0),
        detail_start=sampling_settings.get("detail_start", 0.1),
        detail_end=sampling_settings.get("detail_end", 0.9),
    )


def denoise_prenoised(
    model_forward,
    x_init: Tensor,
    cond: dict,
    uncond: dict | None = None,
    *,
    steps: int,
    sampler_name: str = "euler",
    sampling_settings: dict,
    guidance_scale,
    sigmas: Tensor | None = None,
    hooks=(),
    is_cancelled=None,
    cfg_zero_star: bool = True,
    zero_init_steps: int = 0,
    sampler_options: dict | None = None,
    step_cache_options: dict | None = None,
    guidance_override=None,
) -> Tensor:
    """Run the sampler over an ALREADY-MIXED initial state and return the final
    latent.

    Identical contract to :func:`~.denoise_loop.denoise` except the caller
    supplies ``x_init`` at ``sigmas[0]`` noise level (build it against
    :func:`conditioned_sigmas`; pass the same tensor via ``sigmas`` to
    guarantee schedule identity). ``model_forward(x, sigma, conditioning) ->
    velocity`` — per-token timestep masking, packing, and x0-space blends all
    live inside the caller's wrapper. ``sampler_options`` is forwarded
    unmodified to the chosen sampler, same as ``denoise``. ``step_cache_options``
    enables FBCache the same way as ``denoise`` (see :mod:`~.step_cache`) —
    absent/``rel_threshold<=0`` is a byte-identical no-op, the guidance
    strategy is never wrapped.

    ``guidance_override``: an already-built ``GuidanceStrategy`` instance (e.g.
    ``MultiModalGuidance``).  When provided, bypasses ``_make_guidance``
    (``guidance_scale``/``cfg_zero_star``/``zero_init_steps`` are ignored) and
    the ``step_cache_options`` wrapping is skipped (the override is used as-is).
    """
    if sampler_name not in SAMPLERS:
        raise ValueError(f"unknown sampler {sampler_name!r}; available: {sorted(SAMPLERS)}")
    sampler = SAMPLERS[sampler_name]

    if sigmas is None:
        sigmas = conditioned_sigmas(steps, sampling_settings)
    # FP32 trajectory fix: move sigmas to fp32, NOT x_init's dtype (same rationale
    # as denoise_loop.py — bf16-rounded schedule carries significant per-step error).
    sigmas = sigmas.to(device=x_init.device, dtype=torch.float32)

    # Record the original trajectory dtype to cast back at exit.
    traj_dtype = x_init.dtype
    # Upcast the pre-noised initial state to fp32 trajectory (exact for bf16->fp32).
    x_init = x_init.float()

    if guidance_override is not None:
        # Same silent-discard risk as denoise_loop.denoise --
        # guidance_scale below is never consulted once an override is active.
        # Loud (not silent) whenever the caller passed a real value, since that
        # usually means a conflicting regime (e.g. distilled_mode) resolved
        # cfg/steps/sampler while quality_mode's guider won the actual
        # sampling strategy -- exactly what check_guider_mode_conflict()
        # guards against upstream; this is defense-in-depth.
        if not math.isclose(float(guidance_scale), 1.0):
            override_cfg = getattr(getattr(guidance_override, "video_params", None), "cfg_scale", None)
            logger.warning(
                "denoise_prenoised: guidance_override (%s) is active and discards the passed "
                "guidance_scale=%.3f (override's own cfg=%s) -- check for conflicting "
                "quality_mode/distilled_mode config",
                type(guidance_override).__name__, float(guidance_scale),
                f"{override_cfg:.3f}" if override_cfg is not None else "n/a",
            )
        guidance = guidance_override
        cache_set = None
    else:
        guidance = make_guidance(sampling_settings, guidance_scale, cfg_zero_star, zero_init_steps)

        cache_set = StepCacheSet(step_cache_options) if step_cache_options else None
        if cache_set is not None and cache_set.enabled:
            guidance = _CachingGuidance(guidance, cache_set, total_steps=len(sigmas) - 1)
        else:
            cache_set = None

    logger.debug(
        "denoise_prenoised: sampler=%s steps=%d guidance=%s sigma0=%.5f",
        sampler_name, len(sigmas) - 1, sampling_settings.get("guidance"), float(sigmas[0]),
    )

    # FP32 trajectory fix: wrap model_forward to cast at the boundary (same as
    # denoise_loop.py — sampler sees fp32, model receives traj_dtype).
    def model_forward_fp32(x_fp32: Tensor, sigma_fp32: Tensor, conditioning: dict) -> Tensor:
        x_model = x_fp32.to(traj_dtype)
        sigma_model = sigma_fp32.to(traj_dtype)
        v_model = model_forward(x_model, sigma_model, conditioning)
        return v_model.float()

    watched_hooks = with_numerics_watchdog(hooks, sampler_name, sampler_options)
    latent = sampler(
        model_forward_fp32,
        x_init,
        sigmas,
        guidance,
        cond,
        uncond,
        hooks=watched_hooks,
        is_cancelled=is_cancelled,
        sampler_options=sampler_options,
    )

    if cache_set is not None:
        totals = cache_set.totals()
        logger.debug(
            "FBCache: skipped %d of %d model forwards (rel_threshold=%.3f)",
            totals["skipped"], totals["skipped"] + totals["computed"],
            cache_set.options["rel_threshold"],
        )

    # Cast the final latent back to the original dtype (one final rounding).
    return latent.to(traj_dtype)
