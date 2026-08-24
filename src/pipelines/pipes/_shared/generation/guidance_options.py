"""Shared APG / SLG / RIFLEx / sampler / step-cache config surface for pipes
that call the sampling core (``denoise`` / ``denoise_prenoised``) directly.

(Scope note: this module started as "guidance options" specifically; it now
also carries ``sampler_options``/``step_cache`` since those follow the exact
same shape — a flat PipeConfigSpec that resolves to an opaque kwarg/dict the
sampling core already accepts. Kept in one file rather than splitting, since
every one of these pipes needs the whole set together.)

The image-family pipes (``FlowMatchGeneratorPipe``) reach ``TrueCFG``'s
``cfg_zero_star``/``zero_init_steps`` knobs through a nested
``guidance_options`` dict forwarded to ``NativeGenerator.sample()``
(``engine.py``). The video pipes (Wan/LTX) have no ``NativeGenerator`` in the
loop — they build ``model_forward`` themselves and call
``src.platform.runtime.native.sampling.denoise``/``denoise_prenoised`` directly, passing
their own ``sampling_settings`` dict. ``denoise_loop._make_guidance`` reads
APG (``apg_eta``/``apg_norm_threshold``/``apg_momentum``) and SLG
(``slg_scale``/``slg_layers``/``slg_sigma_start``/``slg_sigma_end``) straight
out of THAT ``sampling_settings`` dict (not from a top-level ``denoise()``
kwarg), so the seam here is: declare flat ``PipeConfigSpec`` knobs (matching
the flat style the Wan pipes already use for ``cfg_zero_star``/
``zero_init_steps``, not a nested dict) and merge their resolved values into
the ``sampling_settings`` dict a pipe passes to ``denoise()``.

All the *_overrides() builders below use a None-sentinel: a pipe's
PipeConfigSpec default for every one of these knobs is ``None``, and
``get_default_config()`` in each pipe likewise seeds ``None`` (not the
sampling core's literal no-op value) for the same keys. A builder only emits
a key into the override dict when ``config.get(key) is not None`` — i.e.
when the preset's ``custom_config`` actually set it. This matters because
the merge at each call site is right-biased
(``{**spec.sampling_settings, **apg_settings_overrides(self.config)}``): if
the builder always emitted its literal defaults (``apg_eta=1.0`` etc.) for
every unset key, that would silently clobber any non-default value a
ModelSpec's own ``sampling_settings`` carries, even when the preset never
touched the knob. Omitting unset keys lets ``spec.sampling_settings`` values
survive; an explicitly-configured preset value still always wins.
"""

from __future__ import annotations

from typing import List

from src.platform.runtime.native.sampling import ensure_sampler_generator
from src.pipelines.contracts import logger
from src.pipelines.contracts import PipeConfigSpec


def apg_settings_config_specs() -> List[PipeConfigSpec]:
    """APG (Adaptive Projected Guidance, arXiv:2410.02416) config knobs.

    Defaults are ``None`` (not the sampling core's literal no-op values) so
    ``apg_settings_overrides`` below can tell "preset left this unset" apart
    from "preset explicitly set it to the no-op value" — see this module's
    docstring for why that distinction matters.
    """
    return [
        PipeConfigSpec(
            "apg_eta", float, None,
            "APG: down-weight of the CFG delta's component parallel to the cond "
            "prediction (unset = inherit the model's own sampling_settings, or "
            "1.0 = off / plain CFG if neither sets it; paper explores ~0.0-0.5 to "
            "reduce oversaturation at high CFG)",
            required=False, min_value=0.0, max_value=1.0,
        ),
        PipeConfigSpec(
            "apg_norm_threshold", float, None,
            "APG: cap the guidance delta's norm to this radius (unset = inherit; "
            "0 = off)",
            required=False, min_value=0.0,
        ),
        PipeConfigSpec(
            "apg_momentum", float, None,
            "APG: reverse-momentum coefficient applied to the guidance delta across "
            "steps (unset = inherit; 0 = off; paper uses small negative values, "
            "e.g. -0.5)",
            required=False, min_value=-1.0, max_value=1.0,
        ),
    ]


def apg_settings_overrides(config: dict) -> dict:
    """Build the ``sampling_settings`` APG overrides from pipe config.

    Only emits keys the preset actually set (non-``None``) — see this
    module's docstring for why an unconditional emit would be a bug.
    """
    overrides = {}
    if config.get("apg_eta") is not None:
        overrides["apg_eta"] = float(config["apg_eta"])
    if config.get("apg_norm_threshold") is not None:
        overrides["apg_norm_threshold"] = float(config["apg_norm_threshold"])
    if config.get("apg_momentum") is not None:
        overrides["apg_momentum"] = float(config["apg_momentum"])
    return overrides


def schedule_settings_config_specs() -> List[PipeConfigSpec]:
    """Sigma-schedule-shaping config knobs read by ``build_sigmas``
    (``sampling/flow_schedule.py``) via ``sampling_settings``: the beta/
    exponential schedule family switch, the manual-sigmas override, and the
    detail-daemon sigma warp."""
    return [
        PipeConfigSpec(
            "schedule", str, "",
            "Sigma schedule family override: '' (default, shift-based) | 'beta' | "
            "'exponential' | 'linear_quadratic' | 'manual' | 'ltx_dynamic'. Prefer "
            "'manual_sigmas' below over setting this to 'manual' directly -- it also "
            "fills schedule_options for you. 'ltx_dynamic' (LTX-2.5 resolution-aware "
            "shift) only functions on a pipe that feeds image_seq_len into the "
            "schedule builder -- currently the LTX generator pipes.",
            required=False, choices=["", "beta", "exponential", "linear_quadratic", "manual", "ltx_dynamic"],
        ),
        PipeConfigSpec(
            "schedule_options", dict, {},
            "Schedule-specific options: beta {'alpha': 0.6, 'beta': 0.6}, "
            "exponential {'sigma_min': 1e-3}, linear_quadratic "
            "{'threshold_noise': 0.025, 'linear_steps': <int, default steps // 2>}, or "
            "ltx_dynamic {'base_shift': 0.95, 'max_shift': 2.05, 'stretch': True, "
            "'terminal': 0.1} (LTX-2.5 defaults). Ignored when schedule is unset.",
            required=False,
        ),
        PipeConfigSpec(
            "manual_sigmas", str, "",
            "Explicit, comma-separated, descending sigma schedule ('1.0, 0.99375, "
            "0.9875, ..., 0.0'; ComfyUI 'ManualSigmas'-style, e.g. a distilled-LoRA "
            "refine tail -- see docs/models/ltx.md). Its length IS the step count: "
            "overrides 'steps' and any shift/schedule setting outright. Takes "
            "priority over 'schedule'/'schedule_options' when non-empty (the "
            "default '' is a no-op, byte-identical to not having this knob).",
            required=False,
        ),
        PipeConfigSpec(
            "detail_strength", float, None,
            "Detail-daemon sigma warp strength (unset = inherit the model's own "
            "sampling_settings, or 0 = off if neither sets it; expected range "
            "-0.3 to 0.3)",
            required=False, min_value=-0.3, max_value=0.3,
        ),
        PipeConfigSpec(
            "detail_start", float, None,
            "Detail-daemon warp window start (trajectory fraction; unset = inherit)",
            required=False, min_value=0.0, max_value=1.0,
        ),
        PipeConfigSpec(
            "detail_end", float, None,
            "Detail-daemon warp window end (trajectory fraction; unset = inherit)",
            required=False, min_value=0.0, max_value=1.0,
        ),
    ]


def schedule_settings_overrides(config: dict) -> dict:
    """Build the ``sampling_settings`` schedule overrides from pipe config.

    Only emits keys the preset actually set — see this module's docstring.
    ``schedule=""`` (the unset default) is treated the same as ``None``:
    ``build_sigmas`` raises on any value outside
    ``{None, "shift", "beta", "exponential", "linear_quadratic", "manual"}``,
    and an empty string is not one of those, so an explicit empty string
    would never be a meaningful preset choice anyway. ``manual_sigmas``
    (non-empty) wins over both ``schedule`` and ``schedule_options`` — see
    its own PipeConfigSpec docstring above.
    """
    overrides = {}
    manual_sigmas = config.get("manual_sigmas")
    if manual_sigmas:
        # Takes priority over schedule/schedule_options -- see this function's
        # PipeConfigSpec docstring. build_sigmas' "manual" mode reads
        # schedule_options['sigmas'] and ignores steps/denoise entirely.
        overrides["schedule"] = "manual"
        overrides["schedule_options"] = {"sigmas": manual_sigmas}
    else:
        if config.get("schedule"):
            overrides["schedule"] = config["schedule"]
        if config.get("schedule_options"):
            overrides["schedule_options"] = config["schedule_options"]
    if config.get("detail_strength") is not None:
        overrides["detail_strength"] = float(config["detail_strength"])
    if config.get("detail_start") is not None:
        overrides["detail_start"] = float(config["detail_start"])
    if config.get("detail_end") is not None:
        overrides["detail_end"] = float(config["detail_end"])
    return overrides


def parse_int_set(raw) -> set:
    """Comma-separated ``"0,2,5"`` -> ``{0, 2, 5}``; ``''``/``None``/``"None"``
    -> empty set. Also accepts an already-parsed list/tuple/set (config values
    can arrive pre-typed depending on the caller). Every element, in either
    form, is validated the same way: non-integer entries (including floats
    with a fractional part, e.g. ``1.9``) are warned-and-skipped rather than
    silently truncated or left to raise an uncaught ``ValueError``.
    """
    if raw in (None, "", "None"):
        return set()
    if isinstance(raw, (set, frozenset, list, tuple)):
        out = set()
        for v in raw:
            try:
                iv = int(v)
            except (TypeError, ValueError):
                logger.warning("guidance_options: ignoring non-integer layer index %r", v)
                continue
            if isinstance(v, float) and not v.is_integer():
                logger.warning("guidance_options: ignoring non-integer layer index %r", v)
                continue
            out.add(iv)
        return out
    out = set()
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            logger.warning("guidance_options: ignoring non-integer layer index %r", part)
    return out


def slg_settings_config_specs() -> List[PipeConfigSpec]:
    """Skip-Layer Guidance config knobs. Only meaningful for arch modules that
    actually honor a ``skip_layers`` forward kwarg (currently Wan only — see
    ``src/platform/runtime/native/arch/wan/model.py``); do not attach these to a pipe
    whose model_forward has nowhere to route ``skip_layers``."""
    return [
        PipeConfigSpec(
            "slg_scale", float, None,
            "Skip-Layer Guidance: push-away strength from a degraded (blocks-skipped) "
            "prediction (unset = inherit the model's own sampling_settings, or 0 = "
            "off if neither sets it)",
            required=False, min_value=0.0, max_value=10.0,
        ),
        PipeConfigSpec(
            "slg_layers", str, None,
            "Skip-Layer Guidance: comma-separated transformer block indices to skip "
            "in the degraded pass (unset = inherit; empty = off even if slg_scale > 0)",
            required=False,
        ),
        PipeConfigSpec(
            "slg_sigma_start", float, None,
            "Skip-Layer Guidance: window upper (earlier-trajectory) sigma bound "
            "(unset = inherit)",
            required=False, min_value=0.0, max_value=1.0,
        ),
        PipeConfigSpec(
            "slg_sigma_end", float, None,
            "Skip-Layer Guidance: window lower (later-trajectory) sigma bound "
            "(unset = inherit)",
            required=False, min_value=0.0, max_value=1.0,
        ),
    ]


def slg_settings_overrides(config: dict) -> dict:
    """Build the ``sampling_settings`` SLG overrides from pipe config.

    Only emits keys the preset actually set — see this module's docstring.
    """
    overrides = {}
    if config.get("slg_scale") is not None:
        overrides["slg_scale"] = float(config["slg_scale"])
    slg_layers = config.get("slg_layers")
    if slg_layers not in (None, "", "None"):
        overrides["slg_layers"] = parse_int_set(slg_layers)
    if config.get("slg_sigma_start") is not None:
        overrides["slg_sigma_start"] = float(config["slg_sigma_start"])
    if config.get("slg_sigma_end") is not None:
        overrides["slg_sigma_end"] = float(config["slg_sigma_end"])
    return overrides


def riflex_config_specs() -> List[PipeConfigSpec]:
    """RIFLEx (arXiv:2502.15894) video-length-extrapolation config knobs —
    Wan-only (see ``WanModel.rope_encode``'s ``riflex`` param)."""
    return [
        PipeConfigSpec(
            "riflex", bool, False,
            "RIFLEx: extrapolate video length beyond the model's trained frame count "
            "via intrinsic RoPE frequency clamping (default off = byte-identical schedule)",
            required=False,
        ),
        PipeConfigSpec(
            "riflex_trained_frames", int, None,
            "RIFLEx: override the trained latent-frame count (default: the family's "
            "known trained length; only used when riflex=true)",
            required=False, min_value=1,
        ),
    ]


def build_riflex(config: dict) -> dict | None:
    """``None`` (the default, ``riflex=false``) is a byte-identical no-op —
    the caller should only pass this through when truthy, never pass an
    explicit ``riflex={"enabled": False}`` dict (both behave the same at the
    arch layer, but omitting the kwarg entirely matches the pre-RIFLEx call
    shape exactly, which regression tests pin)."""
    if not bool(config.get("riflex", False)):
        return None
    riflex: dict = {"enabled": True}
    trained = config.get("riflex_trained_frames")
    if trained not in (None, "", "None"):
        try:
            riflex["latent_frames_trained"] = int(trained)
        except (TypeError, ValueError):
            logger.warning("riflex: ignoring non-numeric riflex_trained_frames %r", trained)
    return riflex


def multimodal_guider_config_specs() -> List[PipeConfigSpec]:
    """MultiModalGuider (LTX-2.3 quality recipe) config knobs.

    When ``quality_mode`` is true the generator pipe uses
    ``MultiModalGuidance`` instead of ``TrueCFG``, applying Lightricks'
    first-party combination formula per modality slice (CFG + STG +
    modality guidance + std-preserving rescale). See
    ``src/platform/runtime/native/sampling/multimodal_guider.py``.
    """
    return [
        PipeConfigSpec(
            "quality_mode", bool, False,
            "Quality recipe (full model, MultiModalGuider): overrides CFG/sampler "
            "with the Lightricks first-party single-pass recipe (cfg 3.0, stg 1.0, "
            "rescale 0.7, modality 3.0, 30 steps, euler). Mutually exclusive with "
            "Distilled recipe.",
            required=False,
        ),
        PipeConfigSpec(
            "quality_cfg", float, None,
            "Quality mode: CFG scale for the video modality (default 3.0)",
            required=False, min_value=1.0, max_value=20.0,
        ),
        PipeConfigSpec(
            "quality_stg", float, None,
            "Quality mode: STG (spatio-temporal guidance) scale for video (default 1.0; 0 = off, skips the perturbed forward)",
            required=False, min_value=0.0, max_value=10.0,
        ),
        PipeConfigSpec(
            "quality_rescale", float, None,
            "Quality mode: std-preserving rescale blend (default 0.7; 0 = off)",
            required=False, min_value=0.0, max_value=1.0,
        ),
        PipeConfigSpec(
            "quality_modality", float, None,
            "Quality mode: cross-modal guidance scale (default 3.0; 1.0 = off, skips the modality-off forward -- only active with an audio track)",
            required=False, min_value=1.0, max_value=20.0,
        ),
        PipeConfigSpec(
            "quality_stg_blocks", str, None,
            "Quality mode: comma-separated transformer block indices for STG perturbation (default '28' for LTX-2.3)",
            required=False,
        ),
        PipeConfigSpec(
            "quality_distilled_strength", float, None,
            "Quality mode with distilled LoRA: LoRA strength override (ComfyUI F-pass uses 0.2; default 0.2 when quality_mode is on and a LoRA is loaded). Not applied automatically -- just an ai_hint.",
            required=False, min_value=0.0, max_value=2.0,
        ),
    ]


def check_guider_mode_conflict(config: dict, pipe_id: str | None = None) -> None:
    """Reject a config with both ``quality_mode`` and ``distilled_mode`` truthy.

    Each is a full recipe override -- quality: ``MultiModalGuidance`` (cfg
    ~3.0, stg/rescale/modality, 30 steps); distilled: cfg ~1.0 + a
    distilled-LoRA manual sigma tail -- and preset templating resolves a
    conflict between the two form checkboxes by checking ``distilled_mode``
    FIRST for ``steps``/``cfg``/``sampler``/``manual_sigmas``. But
    :func:`build_multimodal_guider_params` here only ever looks at
    ``quality_mode``, so a desynced form with BOTH flags set would silently
    run the quality guider (discarding the passed ``guidance_scale`` -- see
    ``denoise_loop.denoise``'s ``guidance_override`` handling) while every
    other resolved field, and the UI, still shows the distilled recipe.
    Never silently pick a winner between two full-recipe overrides; raise.
    """
    if config.get("quality_mode") and config.get("distilled_mode"):
        prefix = f"{pipe_id}: " if pipe_id else ""
        raise ValueError(
            f"{prefix}'quality_mode' and 'distilled_mode' are mutually exclusive recipe "
            f"overrides but both are set -- pick one (quality_mode picks the MultiModalGuider "
            f"recipe: cfg~3.0/30 steps; distilled_mode picks the distilled recipe: cfg~1.0 + "
            f"manual sigma tail)"
        )


def build_multimodal_guider_params(config: dict) -> "tuple[MultiModalGuiderParams, MultiModalGuiderParams] | None":
    """Build video/audio ``MultiModalGuiderParams`` from pipe config.

    Returns ``None`` when ``quality_mode`` is off (the caller should fall
    back to its normal guidance strategy).
    """
    check_guider_mode_conflict(config)
    if not config.get("quality_mode", False):
        return None

    from src.platform.runtime.native.sampling.multimodal_guider import (
        LTX_23_AUDIO_PARAMS,
        LTX_23_VIDEO_PARAMS,
        MultiModalGuiderParams,
    )

    stg_blocks = config.get("quality_stg_blocks")
    if stg_blocks not in (None, "", "None"):
        stg_blocks_list = list(parse_int_set(stg_blocks))
    else:
        stg_blocks_list = list(LTX_23_VIDEO_PARAMS.stg_blocks)

    video = MultiModalGuiderParams(
        cfg_scale=float(config.get("quality_cfg") or LTX_23_VIDEO_PARAMS.cfg_scale),
        stg_scale=float(config.get("quality_stg") if config.get("quality_stg") is not None else LTX_23_VIDEO_PARAMS.stg_scale),
        rescale_scale=float(config.get("quality_rescale") if config.get("quality_rescale") is not None else LTX_23_VIDEO_PARAMS.rescale_scale),
        modality_scale=float(config.get("quality_modality") or LTX_23_VIDEO_PARAMS.modality_scale),
        stg_blocks=stg_blocks_list,
        skip_step=LTX_23_VIDEO_PARAMS.skip_step,
    )
    audio = MultiModalGuiderParams(
        cfg_scale=LTX_23_AUDIO_PARAMS.cfg_scale,
        stg_scale=LTX_23_AUDIO_PARAMS.stg_scale,
        rescale_scale=LTX_23_AUDIO_PARAMS.rescale_scale,
        modality_scale=LTX_23_AUDIO_PARAMS.modality_scale,
        stg_blocks=stg_blocks_list,
        skip_step=LTX_23_AUDIO_PARAMS.skip_step,
    )
    return video, audio


def sampler_step_cache_config_specs() -> List[PipeConfigSpec]:
    """``sampler_options`` / ``step_cache`` (FBCache) config
    knobs — both opaque dicts forwarded to ``denoise``/``denoise_prenoised``
    unmodified (see ``sampling/denoise_loop.py``'s own docstrings for their
    exact shapes). Empty/absent for either is a no-op: ``denoise()`` only
    switches sampler behaviour when the chosen sampler actually reads
    ``sampler_options`` (``euler``/``dpmpp_2m``/``unipc`` ignore it), and
    ``step_cache``'s ``rel_threshold <= 0`` (the default when the dict is
    empty) never wraps the guidance strategy."""
    return [
        PipeConfigSpec(
            "sampler_options", dict, {},
            "Opaque per-sampler options dict forwarded to the chosen sampler, e.g. "
            "{'eta': 0.5} for sampler=euler_sde or {'restart_count': 2, "
            "'restart_strength': 0.3} for sampler=euler_restart. Empty/absent = "
            "sampler defaults, no effect on euler/dpmpp_2m/unipc.",
            required=False,
        ),
        PipeConfigSpec(
            "step_cache", dict, {},
            "FBCache step-skipping options: {'rel_threshold': 0.12, 'warmup_steps': 4, "
            "'max_consecutive_skips': 3}. rel_threshold<=0 (default/absent) is off. "
            "Only arch forwards that honor the step_cache kwarg actually skip steps; "
            "others ignore it harmlessly. Takes priority over the flat "
            "step_cache_threshold/step_cache_warmup_steps/step_cache_max_skips knobs "
            "below when non-empty (back-compat with existing presets/callers).",
            required=False,
        ),
        PipeConfigSpec(
            "step_cache_threshold", float, 0.0,
            "FBCache step-skipping: relative-error threshold below which a step's "
            "residual is reused instead of recomputed. 0.0 (default) is off; ~0.08 is "
            "a conservative starting point, ~0.15 more aggressive. Only pays off on "
            "runs of 15+ steps -- warmup_steps alone already covers shorter runs. "
            "Ignored when the 'step_cache' dict above is also set.",
            required=False, min_value=0.0, max_value=1.0,
        ),
        PipeConfigSpec(
            "step_cache_warmup_steps", int, 4,
            "FBCache step-skipping: number of leading steps forced to fully compute "
            "(never skipped) before the cache is allowed to kick in. Ignored when "
            "the 'step_cache' dict above is also set.",
            required=False, min_value=0,
        ),
        PipeConfigSpec(
            "step_cache_max_skips", int, 3,
            "FBCache step-skipping: maximum number of consecutive steps the cache may "
            "skip before a forced recompute. Ignored when the 'step_cache' dict above "
            "is also set.",
            required=False, min_value=0,
        ),
    ]


def sampler_step_cache_kwargs(config: dict, *, sampler: str | None = None, generator=None) -> dict:
    """Resolve ``sampler_options``/``step_cache`` config into the
    ``denoise``/``denoise_prenoised`` kwargs (``sampler_options=``,
    ``step_cache_options=``). Empty dict (the default) resolves to ``None`` on
    both, matching those functions' own no-op default exactly — never pass an
    empty dict through, since ``denoise()`` treats a truthy-but-empty
    ``step_cache_options`` differently from ``None`` only by coincidence
    today; ``None`` is the contractually-documented off state.

    ``step_cache`` (an explicit, non-empty dict) wins outright over the flat
    ``step_cache_threshold``/``step_cache_warmup_steps``/``step_cache_max_skips``
    knobs -- this preserves the pre-existing dict-based contract byte-for-byte
    for any preset/caller still using it. Otherwise, a flat
    ``step_cache_threshold`` > 0 assembles the same dict shape from the flat
    keys; ``step_cache_threshold`` <= 0 (its default) resolves to ``None``,
    exactly like the dict path's ``rel_threshold <= 0``.

    ``sampler``/``generator`` (both optional, keyword-only): when ``sampler``
    is one of :data:`~src.platform.runtime.native.sampling.STOCHASTIC_SAMPLERS`
    (``euler_sde``/``euler_ancestral``/``euler_ancestral_cfg_pp``/
    ``dpmpp_2m_sde``/``lcm``) and the caller's
    ``sampler_options`` doesn't already carry an explicit ``"generator"``,
    populate it from the caller's own seeded ``generator`` (see
    :func:`~src.platform.runtime.native.sampling.ensure_sampler_generator` for the design
    reasoning — reuse the SAME per-seed generator object, never mint a second
    one from the same raw seed). Omit both kwargs (the default) to leave
    ``sampler_options`` exactly as configured — this is what callers that
    don't yet have a per-seed generator in scope should do."""
    sampler_options = config.get("sampler_options") or None
    step_cache_options = config.get("step_cache") or None
    if not step_cache_options:
        threshold = config.get("step_cache_threshold")
        try:
            threshold = float(threshold) if threshold not in (None, "", "None") else 0.0
        except (TypeError, ValueError):
            logger.warning("guidance_options: ignoring non-numeric step_cache_threshold %r", threshold)
            threshold = 0.0
        if threshold > 0:
            warmup_steps = config.get("step_cache_warmup_steps")
            try:
                warmup_steps = int(warmup_steps) if warmup_steps not in (None, "", "None") else 4
            except (TypeError, ValueError):
                logger.warning("guidance_options: ignoring non-numeric step_cache_warmup_steps %r", warmup_steps)
                warmup_steps = 4
            max_skips = config.get("step_cache_max_skips")
            try:
                max_skips = int(max_skips) if max_skips not in (None, "", "None") else 3
            except (TypeError, ValueError):
                logger.warning("guidance_options: ignoring non-numeric step_cache_max_skips %r", max_skips)
                max_skips = 3
            step_cache_options = {
                "rel_threshold": threshold,
                "warmup_steps": warmup_steps,
                "max_consecutive_skips": max_skips,
            }
    if sampler is not None:
        sampler_options = ensure_sampler_generator(sampler_options, sampler, generator)
    return {"sampler_options": sampler_options, "step_cache_options": step_cache_options}
