"""Flow-matching sigma schedules.

Every native target (Flux1/Flux2/Klein/Qwen-Image/Wan/LTX) is a flow-matching
model: the sampler walks a monotonically-decreasing sigma schedule from
``sigma_max`` (pure noise, 1.0 for these models) down to ``0.0`` (clean latent).
The only per-model difference is how the raw ``t in [1, 0]`` ramp is *shifted*.

Two shift modes are ported verbatim from ComfyUI:

* **constant-shift** — ``ModelSamplingDiscreteFlow`` (Flux2/Qwen/Wan/etc.):
  ``sigma = shift * t / (1 + (shift - 1) * t)`` (``time_snr_shift`` in
  ``comfy/model_sampling.py``). A single ``shift`` scalar per ModelSpec.

* **flux dynamic mu** — the ``ModelSamplingFlux`` node
  (``comfy_extras/nodes_model_advanced.py``): the shift value ``mu`` is
  interpolated from the image sequence length between ``base_shift`` and
  ``max_shift``, then applied through
  ``sigma = exp(mu) / (exp(mu) + (1/t - 1) ** 1.0)`` (``flux_time_shift`` in
  ``comfy/model_sampling.py``). This is Flux1's native schedule.

Both modes yield a descending tensor of length ``steps + 1`` whose first entry
is ``1.0`` and whose last entry is exactly ``0.0``.
"""

from __future__ import annotations

import logging
import math

import torch

logger = logging.getLogger(__name__)

Tensor = torch.Tensor

# ComfyUI ModelSamplingFlux node: the sequence-length interpolation endpoints
# for the base_shift -> max_shift mapping (comfy_extras/nodes_model_advanced.py).
_FLUX_SEQ_LEN_LO = 256
_FLUX_SEQ_LEN_HI = 4096


def _constant_shift_sigmas(t: Tensor, shift: float) -> Tensor:
    """ComfyUI ``time_snr_shift`` / ``ModelSamplingDiscreteFlow.sigma``.

    ``shift == 1.0`` is the identity (sigma == t).
    """
    if shift == 1.0:
        return t
    return shift * t / (1.0 + (shift - 1.0) * t)


def _flux_mu(
    base_shift: float,
    max_shift: float,
    image_seq_len: int,
    seq_len_lo: int = _FLUX_SEQ_LEN_LO,
    seq_len_hi: int = _FLUX_SEQ_LEN_HI,
) -> float:
    """ComfyUI ``ModelSamplingFlux.patch`` linear seq-len -> mu interpolation.

    ``mm``/``b`` reproduce the node exactly; at ``image_seq_len == seq_len_hi``
    this returns ``max_shift``, at ``image_seq_len == seq_len_lo`` it returns
    ``base_shift``. Extrapolates linearly outside ``[seq_len_lo, seq_len_hi]`` --
    no clamping, matching both the ComfyUI node and diffusers' identically-
    shaped ``calculate_shift`` (``modular_pipelines/ltx2/before_denoise.py``,
    Apache-2.0). ``seq_len_lo``/``seq_len_hi`` default to Flux1's anchors
    (256/4096); :func:`_ltx_dynamic_shift_sigmas` below passes LTX-2.5's own
    (1024/4096).
    """
    mm = (max_shift - base_shift) / (seq_len_hi - seq_len_lo)
    b = base_shift - mm * seq_len_lo
    return image_seq_len * mm + b


def _flux_time_shift_sigmas(t: Tensor, mu: float) -> Tensor:
    """ComfyUI ``flux_time_shift(mu, sigma=1.0, t)``.

    ``t`` may contain a 0.0 terminal entry; ``1/t`` there is +inf so the result
    is 0.0, which is the intended clean-latent endpoint. Guard the div-by-zero.
    """
    # exp(mu) / (exp(mu) + (1/t - 1) ** 1.0)
    exp_mu = torch.exp(torch.tensor(mu, dtype=t.dtype))
    out = torch.empty_like(t)
    nonzero = t != 0
    inv = 1.0 / t[nonzero] - 1.0
    out[nonzero] = exp_mu / (exp_mu + inv)
    out[~nonzero] = 0.0
    return out


# LTX-2.5 dynamic shift: token-count interpolation endpoints (diffusers
# ``FlowMatchEulerDiscreteScheduler`` config on the LTX-2 checkpoint --
# ``base_image_seq_len``/``max_image_seq_len`` -- and Lightricks'
# ``LTX2Scheduler.execute`` FACTS, ``BASE_SHIFT_ANCHOR``/``MAX_SHIFT_ANCHOR``;
# verified identical across both, no code consulted from the latter).
_LTX_DYNAMIC_SEQ_LEN_LO = 1024
_LTX_DYNAMIC_SEQ_LEN_HI = 4096


def _stretch_sigmas_to_terminal(sigmas: Tensor, terminal: float) -> Tensor:
    """Affine-rescale every NONZERO sigma so the last one lands exactly on
    ``terminal``, leaving ``sigma == 1.0`` (the head) and the exact-zero
    terminal entries untouched.

    ``1 - sigma`` is linear-rescaled by the ratio that maps the last nonzero
    entry's ``1 - sigma`` onto ``1 - terminal``:
    ``stretched = 1 - (1 - sigma) * (1 - terminal) / (1 - sigma_last)``.
    Algebraically identical to diffusers'
    ``FlowMatchEulerDiscreteScheduler.stretch_shift_to_terminal``
    (``schedulers/scheduling_flow_match_euler_discrete.py``, Apache-2.0), which
    is the reference to consult here and itself cites Lightricks' Apache-2.0
    LTX-Video ``schedulers/rf.py``; this implementation differs only in
    restricting the rescale to the nonzero entries. A degenerate schedule
    whose last nonzero sigma is already ``1.0`` (only possible at ``steps <=
    1``, guarded elsewhere) would divide by zero here and is passed through
    unstretched instead.
    """
    nonzero = sigmas != 0
    if not bool(nonzero.any()):
        return sigmas
    one_minus = 1.0 - sigmas[nonzero]
    last = float(one_minus[-1])
    if last <= 0.0:
        return sigmas
    scale = last / (1.0 - terminal)
    out = sigmas.clone()
    out[nonzero] = 1.0 - (one_minus / scale)
    return out


def _ltx_dynamic_shift_sigmas(
    n: int,
    image_seq_len: int,
    base_shift: float,
    max_shift: float,
    stretch: bool,
    terminal: float,
) -> Tensor:
    """LTX-2.5 resolution-dynamic shift: the same ``exp(mu)/(exp(mu)+(1/t-1))``
    curve as :func:`_flux_time_shift_sigmas`, with ``mu`` interpolated from the
    packed video token count between ``base_shift``/``max_shift`` over
    ``[_LTX_DYNAMIC_SEQ_LEN_LO, _LTX_DYNAMIC_SEQ_LEN_HI]`` (extrapolated
    outside, see :func:`_flux_mu`), then optionally stretched so the last
    sigma before the exact-zero terminal lands on ``terminal`` (see
    :func:`_stretch_sigmas_to_terminal`). Our current LTX ModelSpec pins a
    STATIC shift of ``exp(max_shift)`` instead (the max-anchor endpoint) --
    this mode is what actually varies the schedule with resolution.
    """
    t = torch.linspace(1.0, 0.0, n + 1, dtype=torch.float32)
    mu = _flux_mu(base_shift, max_shift, image_seq_len, _LTX_DYNAMIC_SEQ_LEN_LO, _LTX_DYNAMIC_SEQ_LEN_HI)
    sigmas = _flux_time_shift_sigmas(t, mu)
    if stretch:
        sigmas = _stretch_sigmas_to_terminal(sigmas, terminal)
    return sigmas


def _anchored_mu(dynamic_shift: dict, image_seq_len: int) -> float:
    """Resolution-dynamic mu with caller-supplied anchors (Krea-2 base/midtrain).

    The same two-point line as :func:`_flux_mu` — diffusers'
    ``calculate_shift(image_seq_len, base_seq_len, max_seq_len, base_shift,
    max_shift)`` (Apache-2.0) — with the sequence-length endpoints expressed in
    PIXELS instead of tokens: ``x = (px / align) ** 2``. ``mu = slope * seq_len +
    intercept`` through ``(x1, y1)`` and ``(x2, y2)``; the standard
    ``flux_time_shift`` then maps t -> sigma with this mu.

    Krea-2's own anchors are ``x1_px=256``/``x2_px=1280``/``align=16``,
    ``y1=0.5``/``y2=1.15``, i.e. exactly the ``base_image_seq_len=256``,
    ``max_image_seq_len=6400``, ``base_shift=0.5``, ``max_shift=1.15`` documented
    on diffusers' ``Krea2Pipeline`` for the base/midtrain checkpoint (the
    distilled turbo checkpoint pins ``mu=1.15`` instead — see the krea2 ModelSpec
    in ``detect/registry.py``).
    """
    align = dynamic_shift.get("align", 16)
    x1 = (dynamic_shift["x1_px"] / align) ** 2
    x2 = (dynamic_shift["x2_px"] / align) ** 2
    y1, y2 = dynamic_shift["y1"], dynamic_shift["y2"]
    slope = (y2 - y1) / (x2 - x1)
    return slope * image_seq_len + (y1 - slope * x1)


def _beta_sigmas(n: int, alpha: float, beta_param: float) -> Tensor:
    """Beta(alpha, beta)-CDF-spaced sigmas, descending, length ``n + 1``.

    ``n + 1`` quantile points are drawn from the Beta distribution's inverse
    CDF at ``linspace(0, 1, n + 1)`` (ascending, exactly spanning ``[0, 1]``
    since ``Beta.ppf(0) == 0`` and ``Beta.ppf(1) == 1``) and reversed into
    descending sigma order (``1 -> 0``). ``alpha, beta < 1`` concentrates
    density near both ends (ComfyUI's beta-scheduler convention defaults to
    ``0.6/0.6``); this is a from-scratch re-derivation, not a port.
    """
    import numpy as np
    from scipy.stats import beta as beta_dist

    probs = np.linspace(0.0, 1.0, n + 1)
    x = np.clip(beta_dist.ppf(probs, alpha, beta_param), 0.0, 1.0)
    sigmas = torch.from_numpy(x[::-1].copy()).to(torch.float32)
    sigmas[0] = 1.0
    sigmas[-1] = 0.0
    return sigmas


def _exponential_sigmas(n: int, sigma_min: float) -> Tensor:
    """Geometrically-spaced sigmas from ``1.0`` down to ``sigma_min``, length
    ``n + 1``, with the terminal snapped to exactly ``0.0``.

    ``sigma_min`` can't be 0 (log undefined); a small default keeps the
    geometric spacing meaningful while the true terminal is still exact 0.
    """
    if sigma_min <= 0.0:
        raise ValueError(f"sigma_min must be > 0, got {sigma_min}")
    t = torch.linspace(0.0, 1.0, n + 1, dtype=torch.float32)
    log_min = math.log(sigma_min)
    sigmas = torch.exp(log_min * t)  # exp(0)=1 at t=0, exp(log_min)=sigma_min at t=1
    sigmas[0] = 1.0
    sigmas[-1] = 0.0
    return sigmas


def _linear_quadratic_sigmas(n: int, threshold_noise: float, linear_steps: int | None) -> Tensor:
    """LTX-lineage linear-then-quadratic schedule, descending, length ``n + 1``.

    Adapted from diffusers' ``linear_quadratic_schedule`` (Apache-2.0,
    ``pipelines/mochi/pipeline_mochi.py``): the noise level ramps LINEARLY for the
    first ``linear_steps`` steps (small increments of ``threshold_noise / linear_steps``)
    then QUADRATICALLY for the tail, and the sigma schedule is ``1 - noise``. The
    reference emits ``n`` per-step starting sigmas from 1.0 downward; here we take
    those ``n`` values (built for ``n`` steps) and append an exact ``0.0`` terminal
    so the tensor is length ``n + 1`` like the other schedules. ``linear_steps``
    defaults to ``n // 2``.
    """
    if threshold_noise <= 0.0 or threshold_noise >= 1.0:
        raise ValueError(f"threshold_noise must be in (0, 1), got {threshold_noise}")
    linear = n // 2 if linear_steps is None else int(linear_steps)
    linear = max(1, min(linear, n))

    linear_noise = [i * threshold_noise / linear for i in range(linear)]
    quad_steps = n - linear
    if quad_steps > 0:
        step_diff = linear - threshold_noise * n
        quad_coef = step_diff / (linear * quad_steps ** 2)
        lin_coef = threshold_noise / linear - 2.0 * step_diff / (quad_steps ** 2)
        const = quad_coef * (linear ** 2)
        quad_noise = [quad_coef * (i ** 2) + lin_coef * i + const for i in range(linear, n)]
    else:
        quad_noise = []

    noise = linear_noise + quad_noise                       # length n, ascending in [0, ~1)
    sigmas = [1.0 - v for v in noise] + [0.0]               # descending 1.0 -> 0.0, length n + 1
    out = torch.tensor(sigmas, dtype=torch.float32)
    out[0] = 1.0
    out[-1] = 0.0
    return out


def _manual_sigmas(raw: str | list | tuple) -> Tensor:
    """Explicit, user-authored descending sigma schedule.

    Mirrors ComfyUI's ``ManualSigmas`` node (a literal list, e.g. a
    distilled-LoRA refine tail with a handful of hand-tuned, non-uniformly
    spaced steps -- see ``docs/models/ltx.md``): ``raw`` is either a
    comma-separated string (``"1.0, 0.99375, ..., 0.0"``, the same textbox
    convention the ComfyUI LTX-2.3 upscale mode already uses) or an
    already-parsed sequence of floats. The list's length dictates the actual
    step count directly -- unlike every other mode here, this ignores
    ``steps``/``denoise`` entirely (see :func:`build_sigmas`'s docstring).
    """
    if isinstance(raw, str):
        values = [float(v.strip()) for v in raw.split(",") if v.strip() != ""]
    else:
        values = [float(v) for v in raw]
    if len(values) < 2:
        raise ValueError(f"manual schedule needs at least 2 sigma values, got {len(values)}")
    for a, b in zip(values, values[1:]):
        if b > a:
            raise ValueError(f"manual schedule sigmas must be non-increasing, got {values}")
    sigmas = torch.tensor(values, dtype=torch.float32)
    # Guarantee the same head/tail contract as every other schedule:
    # a hand-typed list might drift a hair off 1.0/0.0 from copy-paste rounding.
    sigmas[0] = 1.0
    sigmas[-1] = 0.0
    return sigmas


def _detail_daemon_warp(
    sigmas: Tensor, strength: float, start: float, end: float
) -> Tensor:
    """Multiply mid-trajectory sigmas by ``1 + strength * bump(t)`` where
    ``bump`` is a half-sine hump over the ``[start, end]`` fraction-of-
    trajectory window (0 at the window edges, 1 at the window midpoint, 0
    outside it) — community "detail daemon" technique (muerrilla/
    sd-webui-detail-daemon; no paper, re-derived here). The first and last
    sigma (index 0 and -1) are never touched. The result is re-clamped to stay
    strictly decreasing and within ``(0, sigmas[0]]`` — a large ``strength``
    or narrow window could otherwise invert neighbouring steps.
    """
    n = sigmas.shape[0]
    if n < 3 or not (0.0 <= start < end <= 1.0):
        return sigmas

    idx = torch.arange(n, dtype=torch.float32)
    frac = idx / (n - 1)  # 0 at first index, 1 at last
    width = end - start
    local = ((frac - start) / width).clamp(0.0, 1.0)
    in_window = (frac > start) & (frac < end)
    bump = torch.where(in_window, torch.sin(math.pi * local), torch.zeros_like(local))
    bump[0] = 0.0
    bump[-1] = 0.0

    warped = sigmas * (1.0 + strength * bump)
    warped = warped.clamp(min=0.0, max=float(sigmas[0]))
    warped[0] = sigmas[0]
    warped[-1] = 0.0

    # Re-clamp for strict monotonic descent (the warp can push a lightly-bumped
    # step past a heavily-bumped neighbour near the window edges). The minimum gap
    # is RELATIVE (S13): the schedule is built in fp32 but consumed at the latent
    # dtype (bf16/fp16), where an absolute 1e-6 gap near 0.9 collapses to zero and
    # would make a multistep sampler's log(sigma/sigma_next) singular. A gap of
    # ~2**-6 of the value is ~2 bf16 ULPs, so adjacent sigmas stay distinct after
    # the down-cast; every interior pair is clamped, not just fp32 violations.
    rel = 2.0 ** -6
    out = warped.clone()
    for i in range(1, n - 1):  # never move the exact-0 terminal
        max_allowed = out[i - 1] - max(1e-6, abs(float(out[i - 1])) * rel)
        if out[i] >= max_allowed:
            out[i] = max(max_allowed, 0.0)
    out[-1] = 0.0
    return out


def build_sigmas(
    steps: int,
    *,
    shift: float | None = None,
    base_shift: float | None = None,
    max_shift: float | None = None,
    dynamic_shift: dict | None = None,
    fixed_mu: float | None = None,
    image_seq_len: int | None = None,
    denoise: float = 1.0,
    schedule: str | None = None,
    schedule_options: dict | None = None,
    detail_strength: float = 0.0,
    detail_start: float = 0.1,
    detail_end: float = 0.9,
) -> Tensor:
    """Build a descending flow-matching sigma schedule of length ``steps + 1``.

    Mode selection (``schedule`` beats the shift-based modes):

    * ``schedule="beta"`` — Beta-CDF spacing; ``schedule_options`` reads
      ``alpha``/``beta`` (default ``0.6``/``0.6``).
    * ``schedule="exponential"`` — geometric spacing; ``schedule_options``
      reads ``sigma_min`` (default ``1e-3``).
    * ``schedule="linear_quadratic"`` — LTX-lineage linear-then-quadratic ramp;
      ``schedule_options`` reads ``threshold_noise`` (default ``0.025``) and
      ``linear_steps`` (default ``steps // 2``).
    * ``schedule="manual"`` — an explicit, hand-authored sigma list (ComfyUI
      ``ManualSigmas``-style; see :func:`_manual_sigmas`); ``schedule_options``
      reads ``sigmas`` (a comma-separated string or a sequence of floats).
      The list's length dictates the step count directly -- ``steps`` and
      ``denoise`` are IGNORED for this mode (no truncation, no shift math);
      the head/tail are still forced to exactly ``1.0``/``0.0``.
    * ``schedule="ltx_dynamic"`` — LTX-2.5's resolution-dependent shift (see
      :func:`_ltx_dynamic_shift_sigmas`); requires ``image_seq_len`` (the
      packed video token count). ``schedule_options`` reads ``base_shift``/
      ``max_shift`` (default ``0.95``/``2.05``, LTX-2.5's own), ``stretch``
      (default ``True``) and ``terminal`` (default ``0.1``). Our LTX
      ModelSpec's own ``shift`` stays a STATIC ``exp(max_shift)`` unless a
      preset opts into this mode explicitly -- existing LTX presets are
      byte-identical until they do.
    * ``schedule=None`` (default) — the original shift-based selection, checked
      in this order: ``fixed_mu`` (a literal mu fed straight to
      :func:`_flux_time_shift_sigmas`, ignoring resolution entirely — Krea-2
      Turbo's distilled-at-fixed-mu=1.15 schedule; see the ModelSpec comment
      in ``detect/registry.py``); else, if ``base_shift``, ``max_shift`` and
      ``image_seq_len`` are all provided, the **flux dynamic mu** schedule
      (Flux1); else ``shift`` (constant-shift, Flux2/Qwen/Wan/...), defaulting
      to ``1.0`` (identity ramp) when omitted. ``dynamic_shift`` +
      ``image_seq_len`` (both provided) selects the resolution-anchored mu
      interpolation (see :func:`_anchored_mu`) when ``fixed_mu`` is absent.

    ``denoise < 1.0`` truncates the schedule the ComfyUI way: a longer schedule
    of ``round(steps / denoise)`` steps is built and only its trailing
    ``steps + 1`` sigmas are kept (so ``sigmas[0] < 1.0`` for img2img). This
    applies uniformly across every schedule mode EXCEPT ``"manual"``, which
    ignores ``steps``/``denoise`` entirely (see above).

    ``detail_strength`` (default ``0.0`` = off, byte-identical output) applies
    the detail-daemon sigma warp (see :func:`_detail_daemon_warp`) over the
    ``[detail_start, detail_end]`` trajectory-fraction window; expected range
    ``[-0.3, 0.3]``.

    The returned tensor is float32 on CPU; the caller moves/casts it.
    """
    if steps < 1:
        raise ValueError(f"steps must be >= 1, got {steps}")
    if not (0.0 < denoise <= 1.0):
        raise ValueError(f"denoise must be in (0, 1], got {denoise}")

    opts = schedule_options or {}

    # Manual mode bypasses everything else in this function (shift math,
    # image_seq_len, the denoise truncation below): the list itself IS the
    # schedule. Handled before `sched_steps`/`use_flux_mu` even look at
    # `steps`, since a manual list's length has no relation to it.
    if schedule == "manual":
        sigmas = _manual_sigmas(opts.get("sigmas"))
        if detail_strength:
            sigmas = _detail_daemon_warp(sigmas, float(detail_strength), float(detail_start), float(detail_end))
        return sigmas

    use_flux_mu = (
        base_shift is not None
        and max_shift is not None
        and image_seq_len is not None
    )
    use_anchored_mu = dynamic_shift is not None and image_seq_len is not None

    # Truncated-denoise: build for more steps, keep the tail (ComfyUI set_steps).
    sched_steps = steps if denoise > 0.9999 else int(round(steps / denoise))
    sched_steps = max(sched_steps, steps)

    if schedule == "beta":
        alpha = float(opts.get("alpha", 0.6))
        beta_param = float(opts.get("beta", 0.6))
        # S17: alpha/beta <= 0 makes the Beta ppf return NaN at interior points.
        if not (alpha > 0.0 and beta_param > 0.0):
            raise ValueError(f"beta schedule needs alpha>0 and beta>0, got {alpha}/{beta_param}")
        logger.debug("beta schedule: alpha=%.3f beta=%.3f steps=%d", alpha, beta_param, sched_steps)
        sigmas = _beta_sigmas(sched_steps, alpha, beta_param)
    elif schedule == "exponential":
        sigma_min = float(opts.get("sigma_min", 1e-3))
        # S17: sigma_min must sit strictly inside (0, 1) — >= 1 yields an
        # ASCENDING ramp (early Euler steps would move toward higher noise).
        if not (0.0 < sigma_min < 1.0):
            raise ValueError(f"exponential schedule needs sigma_min in (0, 1), got {sigma_min}")
        logger.debug("exponential schedule: sigma_min=%.5f steps=%d", sigma_min, sched_steps)
        sigmas = _exponential_sigmas(sched_steps, sigma_min)
    elif schedule == "linear_quadratic":
        threshold_noise = float(opts.get("threshold_noise", 0.025))
        linear_steps = opts.get("linear_steps")  # None -> sched_steps // 2
        logger.debug("linear_quadratic schedule: threshold=%.4f linear_steps=%s steps=%d",
                     threshold_noise, linear_steps, sched_steps)
        sigmas = _linear_quadratic_sigmas(sched_steps, threshold_noise, linear_steps)
    elif schedule == "ltx_dynamic":
        if image_seq_len is None:
            raise ValueError("ltx_dynamic schedule requires image_seq_len (packed video token count)")
        ltx_base_shift = float(opts.get("base_shift", 0.95))
        ltx_max_shift = float(opts.get("max_shift", 2.05))
        ltx_stretch = bool(opts.get("stretch", True))
        ltx_terminal = float(opts.get("terminal", 0.1))
        logger.debug(
            "ltx_dynamic schedule: tokens=%d base=%.3f max=%.3f stretch=%s terminal=%.3f steps=%d",
            image_seq_len, ltx_base_shift, ltx_max_shift, ltx_stretch, ltx_terminal, sched_steps,
        )
        sigmas = _ltx_dynamic_shift_sigmas(
            sched_steps, int(image_seq_len), ltx_base_shift, ltx_max_shift, ltx_stretch, ltx_terminal,
        )
    elif schedule not in (None, "shift"):
        raise ValueError(
            f"unknown schedule {schedule!r}; expected "
            "None/'shift'/'beta'/'exponential'/'linear_quadratic'/'manual'/'ltx_dynamic'"
        )
    else:
        t = torch.linspace(1.0, 0.0, sched_steps + 1, dtype=torch.float32)
        if fixed_mu is not None:
            logger.debug("fixed-mu schedule: mu=%.5f (resolution-independent)", fixed_mu)
            sigmas = _flux_time_shift_sigmas(t, float(fixed_mu))
        elif use_anchored_mu:
            mu = _anchored_mu(dynamic_shift, int(image_seq_len))
            logger.debug("anchored dynamic-mu schedule: seq_len=%d -> mu=%.5f", image_seq_len, mu)
            sigmas = _flux_time_shift_sigmas(t, mu)
        elif use_flux_mu:
            mu = _flux_mu(float(base_shift), float(max_shift), int(image_seq_len))
            logger.debug(
                "flux dynamic-mu schedule: seq_len=%d base=%.3f max=%.3f -> mu=%.5f",
                image_seq_len, base_shift, max_shift, mu,
            )
            sigmas = _flux_time_shift_sigmas(t, mu)
        else:
            s = 1.0 if shift is None else float(shift)
            sigmas = _constant_shift_sigmas(t, s)

    if sched_steps != steps:
        sigmas = sigmas[-(steps + 1):]

    # Guarantee an exact clean-latent terminal regardless of float drift.
    sigmas[-1] = 0.0

    if detail_strength:
        sigmas = _detail_daemon_warp(sigmas, float(detail_strength), float(detail_start), float(detail_end))

    return sigmas
