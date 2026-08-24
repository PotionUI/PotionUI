"""The one denoise orchestrator every native generator calls.

``denoise`` ties the pieces together: build the sigma schedule from a
ModelSpec's ``sampling_settings``, pick a guidance strategy from that same
settings dict, scale the initial noise the flow-matching way, and run the
chosen step algorithm. Adding model N+1 must never edit this file — new
behaviour arrives as sampling_settings values, a guidance strategy, a StepHook,
or a wrapped ``model_forward``.

Expert routing (Wan 2.2 dual expert)
------------------------------------
``model_forward`` is just a callable ``(x, sigma, conditioning) -> velocity``.
Wan's high/low-noise experts are implemented *outside* this loop as a wrapper
callable that inspects ``sigma`` and dispatches to the right transformer; no
special-casing is needed here. The same seam covers LTX per-step guidance
(pass a per-step scale list into the strategy) and any future router.

Noise-scaling semantics (ComfyUI ``CONST.noise_scaling``)
---------------------------------------------------------
The initial latent is ``x = sigma0 * noise + (1 - sigma0) * latent`` where
``sigma0 = sigmas[0]``:

* **txt2img** — ``latents`` is zeros, ``denoise_strength == 1`` so ``sigma0 ==
  1.0`` and ``x == noise`` (pure noise).
* **img2img** — ``latents`` is the encoded image and ``denoise_strength < 1``
  truncates the schedule so ``sigma0 < 1.0``; ``x`` is a
  ``sigma0``-weighted blend of noise and the image latent.
"""

from __future__ import annotations

import logging
import math

import torch

from .algorithms import (
    ANCESTRAL_NOISE_SEED_OFFSET,
    sample_dpmpp_2m,
    sample_dpmpp_2m_sde,
    sample_dpmpp_3m,
    sample_euler,
    sample_euler_ancestral,
    sample_euler_ancestral_cfg_pp,
    sample_euler_cfg_pp,
    sample_euler_restart,
    sample_euler_sde,
    sample_lcm,
    sample_res_multistep,
    sample_unipc,
)
from .cfg import EmbeddedGuidance, GuidanceStrategy, NoCFG, SkipLayerGuidance, TrueCFG
from .flow_schedule import build_sigmas
from .hooks import with_numerics_watchdog
from .step_cache import StepCacheSet

logger = logging.getLogger(__name__)

Tensor = torch.Tensor

# Extensible step-algorithm registry. Each entry matches sample_euler's
# signature: (model_fn, x, sigmas, guidance, cond, uncond, hooks, is_cancelled,
# sampler_options). ``sampler_options`` is an opaque dict passed through
# unmodified from denoise()'s own ``sampler_options`` kwarg; the multistep
# entries (dpmpp_2m, unipc, dpmpp_3m, res_multistep) read only
# ``discontinuity_steps`` from it (see the comment below), and the single-step
# ones (euler among them) ignore it entirely.
SAMPLERS = {
    "euler": sample_euler,
    "dpmpp_2m": sample_dpmpp_2m,
    "unipc": sample_unipc,
    "euler_sde": sample_euler_sde,
    "euler_ancestral": sample_euler_ancestral,
    "euler_ancestral_cfg_pp": sample_euler_ancestral_cfg_pp,
    "euler_cfg_pp": sample_euler_cfg_pp,
    "euler_restart": sample_euler_restart,
    "dpmpp_2m_sde": sample_dpmpp_2m_sde,
    "dpmpp_3m": sample_dpmpp_3m,
    "res_multistep": sample_res_multistep,
    "lcm": sample_lcm,
}

# Samplers whose ``sampler_options['generator']`` (see each algorithm's own
# docstring) drives a per-step stochastic noise draw -- absent that key, the
# draw falls back to the UNSEEDED global RNG, breaking the seed-provenance
# guarantee (same seed twice must reproduce the same output). Every OTHER
# entry in SAMPLERS is either fully deterministic (euler, euler_cfg_pp,
# dpmpp_2m, unipc, dpmpp_3m, res_multistep) or already seeds its own restarts
# from the caller's seed_noise (euler_restart), so this set is intentionally
# the minority. ``euler_ancestral`` (LTX-2.5 stage-1) is unlike its siblings
# here: callers are expected to hand it a DEDICATED generator seeded off
# ``ANCESTRAL_NOISE_SEED_OFFSET``, not the shared per-seed generator this
# module's own docstring below argues for -- see
# ``sampling/algorithms/euler_ancestral.py``'s module docstring.
STOCHASTIC_SAMPLERS = frozenset(
    {"euler_sde", "euler_ancestral", "euler_ancestral_cfg_pp", "dpmpp_2m_sde", "lcm"}
)


def ensure_sampler_generator(
    sampler_options: dict | None,
    sampler: str,
    generator: "torch.Generator | None",
) -> dict | None:
    """Populate ``sampler_options['generator']`` from the request's own seeded
    ``torch.Generator`` when ``sampler`` is stochastic (:data:`STOCHASTIC_SAMPLERS`)
    and the caller hasn't already supplied one explicitly. This function is
    agnostic to WHICH generator the caller passes in -- for ``euler_ancestral``
    that is deliberately a dedicated, offset-seeded generator rather than the
    shared one the reasoning below is written for; see that sampler's module
    docstring.

    Design decision (seed-determinism fix): this reuses the SAME ``generator``
    object the caller used to draw its initial noise (and, for the video
    pipes, whatever FreeInit already drew from afterward) rather than minting
    a second ``torch.Generator(...).manual_seed(seed)`` from the same raw
    seed value. Two independently-seeded generators built from the identical
    seed integer would start at the IDENTICAL point in the Philox/MT stream,
    so the sampler's first per-step stochastic draw would exactly equal
    values already consumed by the init-noise draw -- a real correlation, not
    a merely theoretical one. Reusing the single advancing generator object
    (continue-the-stream, matching how ``freeinit.py``'s callers already draw
    ``noise``/``renoise_noise``/``fresh_noise`` off one shared generator in
    sequence) guarantees every draw across the whole per-seed run comes from
    one non-repeating stream, with no possibility of two draws coinciding.

    ``generator=None`` (no seed-derived generator available, e.g. an explicit
    ``noise=`` tensor was supplied instead of a seed) leaves ``sampler_options``
    untouched -- that caller already opted out of seed-based reproducibility.
    An explicit ``sampler_options['generator']`` from the caller always wins.
    """
    if sampler not in STOCHASTIC_SAMPLERS or generator is None:
        return sampler_options
    if sampler_options is not None and sampler_options.get("generator") is not None:
        return sampler_options
    merged = dict(sampler_options) if sampler_options else {}
    merged["generator"] = generator
    return merged


def _assert_finite_conditioning(cond: dict, uncond: dict | None) -> None:
    """One-time entry check: catch conditioning that arrived ALREADY non-finite,
    before the first sampling step runs.

    Distinguishes a poisoned INPUT (raised here as :class:`PoisonedConditioningError`)
    from in-loop divergence (:class:`SamplingNumericsError`, from the
    per-step watchdog) -- the same NaN/Inf symptom a few steps later reads
    very differently depending on which one it is. A single ``isfinite`` pass
    per tensor at entry is negligible next to the steps-many forward passes
    that follow.
    """
    from ..errors import PoisonedConditioningError

    for which, d in (("cond", cond), ("uncond", uncond)):
        if not d:
            continue
        for key, value in d.items():
            if torch.is_tensor(value) and torch.is_floating_point(value) and not torch.isfinite(value).all():
                raise PoisonedConditioningError(which, key)


def _expert_switch_step(sigmas: Tensor, boundary: float | None) -> int | None:
    """First step index whose sigma has crossed ``boundary``.

    Wan's dual-expert ``_ExpertRouter`` dispatches per step on the identical
    comparison (``sigma_val > boundary`` -> high, else low): the step this
    returns is the first one whose model_forward call reaches a DIFFERENT
    network than the previous step's. ``None`` when there's no boundary
    (single-expert model) or the schedule never crosses it.
    """
    if boundary is None:
        return None
    for i in range(len(sigmas) - 1):
        if float(sigmas[i]) <= boundary:
            return i
    return None


def _make_guidance(
    sampling_settings: dict,
    guidance_scale,
    cfg_zero_star: bool = True,
    zero_init_steps: int = 0,
) -> GuidanceStrategy:
    """Pick a guidance strategy from ``sampling_settings['guidance']``.

    ``cfg_zero_star``/``zero_init_steps`` only apply to the ``"cfg"`` (TrueCFG)
    strategy; other modes ignore them.

    Two further optional corrections, read directly from ``sampling_settings``
    (no top-level ``denoise()`` kwargs — same treatment as the schedule-shaping
    knobs above) so presets opt in without any call-site changes:

    * APG (see :class:`~.cfg.TrueCFG`) — ``apg_eta``/``apg_norm_threshold``/
      ``apg_momentum``, forwarded to ``TrueCFG`` only (other modes ignore them,
      same as ``cfg_zero_star``).
    * SLG (see :class:`~.cfg.SkipLayerGuidance`) — ``slg_scale`` (default
      ``0`` = off), ``slg_layers`` (no default — an empty/absent set is also a
      no-op), ``slg_sigma_start``/``slg_sigma_end`` (default the whole
      trajectory, ``1.0``/``0.0``). When ``slg_scale > 0`` the strategy built
      above (of ANY guidance mode) is wrapped, not replaced.
    """
    mode = sampling_settings.get("guidance")
    if mode == "embedded":
        strategy = EmbeddedGuidance(guidance_scale)
    elif mode == "cfg":
        strategy = TrueCFG(
            guidance_scale,
            cfg_zero_star=cfg_zero_star,
            zero_init_steps=zero_init_steps,
            apg_eta=sampling_settings.get("apg_eta", 1.0),
            apg_norm_threshold=sampling_settings.get("apg_norm_threshold", 0.0),
            apg_momentum=sampling_settings.get("apg_momentum", 0.0),
        )
    elif mode in (None, "none"):
        strategy = NoCFG()
    else:
        raise ValueError(f"unknown guidance mode: {mode!r}")

    slg_scale = sampling_settings.get("slg_scale", 0.0)
    if slg_scale > 0.0:
        strategy = SkipLayerGuidance(
            strategy,
            slg_scale=slg_scale,
            layers=sampling_settings.get("slg_layers"),
            sigma_start=sampling_settings.get("slg_sigma_start", 1.0),
            sigma_end=sampling_settings.get("slg_sigma_end", 0.0),
        )
    return strategy


# Public alias: conditioned.py builds the same strategies for its pre-noised loop.
make_guidance = _make_guidance


class _CachingGuidance:
    """Wrap a guidance strategy so each model forward carries a per-branch FBCache.

    The wrapped strategy stays unaware of caching: this shim swaps in a
    ``model_fn`` that (a) selects the cond-vs-uncond cache by the conditioning
    object it is handed (cond and uncond trajectories must never share a cache),
    (b) never caches the final step (``sigma_next == 0`` must be exact), and (c)
    never caches a degraded ``skip_layers`` pass so SLG's extra forward can't
    poison or consume the cond cache.

    Branch attribution rides on identity plus the one cond-side marker:
    ``TrueCFG``/``NoCFG`` hand ``model_fn`` the exact ``cond``/``uncond`` dict
    objects; ``EmbeddedGuidance`` hands a fresh ``{**cond, "guidance": ...}`` copy
    (single-branch), which the ``"guidance"`` marker attributes to the cond cache.
    Any OTHER dict — one that is neither ``cond``/``uncond`` by identity nor
    carries a known marker (S19: e.g. a strategy that shallow-copies ``uncond``) —
    is NOT cached (rather than silently poisoning the cond cache). ``FirstBlockCache``
    is injected into a shallow copy under the reserved ``"step_cache"`` key.
    """

    def __init__(self, inner: GuidanceStrategy, caches: StepCacheSet, total_steps: int) -> None:
        self.inner = inner
        self.caches = caches
        self.total_steps = total_steps

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index) -> Tensor:
        final = step_index >= self.total_steps - 1

        def cached_model_fn(xx, ss, conditioning):
            if final or "skip_layers" in conditioning:
                return model_fn(xx, ss, conditioning)
            if uncond is not None and conditioning is uncond:
                cache = self.caches.for_branch("uncond")
            elif conditioning is cond or "guidance" in conditioning:
                cache = self.caches.for_branch("cond")
            else:
                return model_fn(xx, ss, conditioning)  # unknown branch: don't cache
            return model_fn(xx, ss, {**conditioning, "step_cache": cache})

        return self.inner(cached_model_fn, x, sigma, cond, uncond, step_index)


def denoise(
    model_forward,
    latents: Tensor,
    cond: dict,
    uncond: dict | None = None,
    *,
    steps: int,
    sampler_name: str = "euler",
    sampling_settings: dict,
    guidance_scale,
    image_seq_len: int | None = None,
    hooks=(),
    is_cancelled=None,
    seed_noise: Tensor | None = None,
    denoise_strength: float = 1.0,
    cfg_zero_star: bool = True,
    zero_init_steps: int = 0,
    sampler_options: dict | None = None,
    step_cache_options: dict | None = None,
    resume: tuple[int, Tensor] | None = None,
    guidance_override: GuidanceStrategy | None = None,
    sigmas: Tensor | None = None,
    expert_boundary: float | None = None,
) -> Tensor:
    """Denoise ``latents`` to a clean latent and return it.

    ``model_forward(x, sigma, conditioning) -> velocity`` is the generator-side
    adapter over the arch ``forward(x, timestep, context, y, guidance)``: it maps
    ``sigma`` to the arch's timestep convention and unpacks the conditioning dict
    (``context``/``y`` plus the ``"guidance"`` key that :class:`EmbeddedGuidance`
    injects). ``cond``/``uncond`` are those opaque conditioning dicts.

    ``sampling_settings`` is a ModelSpec's dict: ``shift`` / ``base_shift`` /
    ``max_shift`` drive the schedule and ``guidance`` (``"embedded"`` | ``"cfg"``
    | ``None``) picks the strategy. ``image_seq_len`` enables Flux1's dynamic-mu
    schedule; omit it for constant-shift models. ``cfg_zero_star``/
    ``zero_init_steps`` only affect the ``"cfg"`` (TrueCFG) strategy — see
    :class:`~src.platform.runtime.native.sampling.cfg.TrueCFG`.

    ``sampling_settings`` may also carry schedule-shaping knobs, all optional
    and defaulted to :func:`~.flow_schedule.build_sigmas`'s own no-op defaults
    so existing presets are byte-identical unless they opt in: ``schedule``
    (``None`` (default, shift-based) | ``"beta"`` | ``"exponential"``),
    ``schedule_options`` (dict of that schedule's params), ``detail_strength``
    /``detail_start``/``detail_end`` (detail-daemon sigma warp).

    ``sampler_options`` is an opaque dict forwarded to the chosen sampler (see
    :data:`SAMPLERS`); e.g. ``{"eta": 0.5}`` for ``"euler_sde"`` or
    ``{"restart_count": 2}`` for ``"euler_restart"``. Samplers without options
    ignore it.

    ``step_cache_options`` enables FBCache step skipping (see
    :mod:`~.step_cache`): ``{"rel_threshold": 0.12, "warmup_steps": 4,
    "max_consecutive_skips": 3}``. ``rel_threshold <= 0`` (the default when the
    dict is absent) is a no-op — the guidance strategy is not wrapped and the
    path is byte-identical. Only arches whose ``forward`` honours the
    ``step_cache`` kwarg actually skip; others simply ignore it.

    ``resume`` is trajectory warm-start (see
    :mod:`~.trajectory_cache`): ``(start_step, resume_latent)``. When set, ``x``
    is the supplied on-trajectory latent (NOT re-noised) and the loop starts at
    ``start_step`` over the full sigma array, so the tail is bit-identical to the
    cold run's. Only the deterministic ``euler`` sampler supports it (the engine
    gates on that); passing it with any other sampler is a programming error.

    ``sigmas``: an already-built schedule tensor, used AS-IS
    instead of calling :func:`~.flow_schedule.build_sigmas` -- bypasses that
    function's ``schedule="manual"`` mode entirely, including its head/tail
    forcing (``sigmas[0] = 1.0``, ``sigmas[-1] = 0.0``). This is the only way
    to run a partial-noise refine pass with an EXPLICIT, non-1.0 starting
    sigma (e.g. Lightricks' own upscale-refine tail
    ``STAGE_2_DISTILLED_SIGMA_VALUES``) — mirrors :func:`~.conditioned.
    denoise_prenoised`'s identically-named parameter. ``steps``/
    ``denoise_strength``/every schedule-shaping ``sampling_settings`` key are
    ignored when this is set, matching that function's ``schedule="manual"``
    docs.

    ``expert_boundary``: the sigma threshold a multi-expert ``model_forward``
    switches networks at (e.g. Wan's ``_ExpertRouter.boundary``), or ``None``
    for a single-model run. When the built schedule crosses it, the crossing
    step is forwarded to the sampler as ``sampler_options['discontinuity_steps']``
    -- a multistep sampler (unipc today) resets its predictor/corrector
    history there instead of extrapolating across a step whose model output
    came from a different network than the previous step's.
    """
    if resume is not None and sampler_name != "euler":
        raise ValueError(f"trajectory resume requires sampler 'euler', got {sampler_name!r}")
    if sampler_name not in SAMPLERS:
        raise ValueError(
            f"unknown sampler {sampler_name!r}; available: {sorted(SAMPLERS)}"
        )
    sampler = SAMPLERS[sampler_name]
    _assert_finite_conditioning(cond, uncond)

    # FP32 trajectory: build sigmas in fp32 (schedule's native precision, no
    # bf16 rounding), then move only device, NOT dtype. The LTX 2.3
    # distilled schedule's early steps differ by 0.00625 while bf16 ULP near 1.0 is
    # 0.0039 — rounding the schedule itself carries 30-60% error per-step.
    if sigmas is not None:
        sigmas = sigmas.to(device=latents.device, dtype=torch.float32)
    else:
        sigmas = build_sigmas(
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
        ).to(device=latents.device, dtype=torch.float32)

    sigma0 = sigmas[0]
    # Record the original trajectory dtype to cast back at loop exit (pipes create
    # noise/latents in the DiT's dtype, typically bf16).
    traj_dtype = latents.dtype
    if resume is not None:
        # Warm-start: x is a cached on-trajectory latent (already past step
        # start_step-1); it is NOT re-noised, and the sampler skips the first
        # start_step iterations over the same full sigma array.
        start_step, resume_latent = resume
        total_steps = len(sigmas) - 1
        # S16: a bad resume_step must fail loudly, not silently return a noisy or
        # unfinished latent (start_step >= total would run an empty loop; a
        # negative one would be clamped to 0 by range()).
        if not (0 <= int(start_step) < total_steps):
            raise ValueError(
                f"resume start_step {start_step} out of range [0, {total_steps})"
            )
        if tuple(resume_latent.shape) != tuple(latents.shape):
            raise ValueError(
                f"resume latent shape {tuple(resume_latent.shape)} != {tuple(latents.shape)}"
            )
        # Upcast resume latent to fp32 trajectory (exact for bf16 -> fp32).
        x = resume_latent.to(device=latents.device, dtype=torch.float32)
    else:
        start_step = 0
        noise = seed_noise if seed_noise is not None else torch.randn_like(latents)
        # Upcast initial noise to fp32 trajectory (exact for bf16 -> fp32: same
        # seed, same initial noise values, only trajectory precision changes).
        x = (sigma0 * noise + (1.0 - sigma0) * latents).float()

    if guidance_override is not None:
        # guidance_override (e.g. MultiModalGuidance from quality_mode) fully
        # replaces the guidance strategy -- guidance_scale below is never
        # consulted. A non-1.0 guidance_scale here means some other regime
        # resolved cfg/steps/sampler while the override won the sampling
        # strategy -- the desync check_guider_mode_conflict() guards upstream.
        # Warn here too, as defense-in-depth.
        if not math.isclose(float(guidance_scale), 1.0):
            override_cfg = getattr(getattr(guidance_override, "video_params", None), "cfg_scale", None)
            logger.warning(
                "denoise: guidance_override (%s) is active and discards the passed "
                "guidance_scale=%.3f (override's own cfg=%s) -- check for conflicting "
                "quality_mode/distilled_mode config",
                type(guidance_override).__name__, float(guidance_scale),
                f"{override_cfg:.3f}" if override_cfg is not None else "n/a",
            )
        guidance = guidance_override
    else:
        guidance = _make_guidance(sampling_settings, guidance_scale, cfg_zero_star, zero_init_steps)

    cache_set = StepCacheSet(step_cache_options) if step_cache_options else None
    if cache_set is not None and cache_set.enabled:
        guidance = _CachingGuidance(guidance, cache_set, total_steps=len(sigmas) - 1)
    else:
        cache_set = None

    logger.debug(
        "denoise: sampler=%s steps=%d guidance=%s sigma0=%.5f denoise=%.3f",
        sampler_name, steps, sampling_settings.get("guidance"),
        float(sigma0), denoise_strength,
    )

    # FP32 trajectory fix: wrap model_forward at the boundary so the sampler sees
    # fp32 state (x, v, x0_est, ancestral noise via randn_like) while the model
    # receives its original traj_dtype. Guidance/CFG, preview hooks, and FBCache
    # operate on the fp32 trajectory; only the DiT forward runs in traj_dtype.
    def model_forward_fp32(x_fp32: Tensor, sigma_fp32: Tensor, conditioning: dict) -> Tensor:
        x_model = x_fp32.to(traj_dtype)
        sigma_model = sigma_fp32.to(traj_dtype)
        v_model = model_forward(x_model, sigma_model, conditioning)
        return v_model.float()

    # Multi-expert discontinuity: tell a multistep sampler (unipc, dpmpp_2m,
    # dpmpp_2m_sde, dpmpp_3m, res_multistep) which step index switches network
    # mid-run, so it can reset its predictor/corrector history there instead
    # of extrapolating across two different models' outputs. Merged into
    # sampler_options (the existing per-sampler opaque-dict seam) rather than
    # the uniform SAMPLERS call signature, so samplers that don't look for the
    # key (the single-step ones: euler, euler_sde, euler_ancestral_cfg_pp,
    # euler_cfg_pp, euler_restart, lcm) are unaffected.
    switch_step = _expert_switch_step(sigmas, expert_boundary)
    if switch_step is not None:
        sampler_options = {**(sampler_options or {}), "discontinuity_steps": frozenset({switch_step})}

    # NaN/Inf watchdog: fails the generation loudly instead of decoding a black
    # image (uniform across all samplers via the shared on_step dispatch).
    watched_hooks = with_numerics_watchdog(hooks, sampler_name, sampler_options)

    # start_step is only in euler's signature; pass it solely on a warm-start
    # (engine-gated to euler) so the uniform SAMPLERS contract is untouched.
    resume_kwargs = {"start_step": start_step} if start_step > 0 else {}
    latent = sampler(
        model_forward_fp32,
        x,
        sigmas,
        guidance,
        cond,
        uncond,
        hooks=watched_hooks,
        is_cancelled=is_cancelled,
        sampler_options=sampler_options,
        **resume_kwargs,
    )

    if cache_set is not None:
        totals = cache_set.totals()
        logger.debug(
            "FBCache: skipped %d of %d model forwards (rel_threshold=%.3f)",
            totals["skipped"], totals["skipped"] + totals["computed"],
            cache_set.options["rel_threshold"],
        )

    # Cast the final latent back to the original dtype (one final rounding, same as
    # the reference's decode-boundary cast). Downstream decode contracts unchanged.
    return latent.to(traj_dtype)
