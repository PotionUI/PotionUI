# Derived from: diffusers `UniPCMultistepScheduler`
# (schedulers/scheduling_unipc_multistep.py), Apache-2.0, Copyright TSAIL Team
# and The HuggingFace Team. Algorithm: UniPC, arXiv:2302.04867 (Zhao et al.),
# reference implementation wl-zhao/UniPC.
"""UniPC (multistep predictor-corrector) for flow-matching models.

Reshaped from the reference's stateful scheduler class into a single functional
loop matching :func:`sample_euler`'s signature. All the multistep state (history
of x0 predictions + their sigmas, the corrector's previous sample, and the
warmup/lower-order bookkeeping) is encapsulated in the loop's local scope.

Configuration matches the reference's own defaults: ``solver_order=2``,
``predict_x0=True`` (data-prediction), ``solver_type="bh2"``,
``lower_order_final=True``, plus its flow branch (``use_flow_sigmas=True``,
``prediction_type="flow_prediction"``) — the flow noise map
``alpha_t = 1 - sigma``, ``sigma_t = sigma`` (so ``lambda = log((1-sigma)/sigma)``
is the flow half-log-SNR — NOT the ``-log(sigma)`` used by dpmpp_2m).
``tests/platform/runtime/native/sampling/test_unipc_reference_equivalence.py``
pins this loop to that scheduler numerically.

x0 prediction for a flow/CONST model: ``x0 = x - sigma*v`` (``convert_model_output``
with ``prediction_type="flow_prediction"``). For a constant-velocity model the
x0 estimate is exact and constant, so every predictor difference ``D1`` and the
corrector term vanish and the first-order update reduces exactly to Euler
(the identity ``sigma_t/sigma_s0 - alpha_t*(e^hh - 1) == 1`` holds for the flow
noise map) — this integrates a linear flow ODE identically to :func:`sample_euler`.
"""

from __future__ import annotations

import math

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled, SamplingNumericsError

Tensor = torch.Tensor

# Reference default; order-2 pairs with dpmpp_2m. Extensible via the sampler kwarg.
UNIPC_SOLVER_ORDER = 2
_SOLVER_TYPE = "bh2"


def _lambda(sigma: float) -> float:
    """Flow half-log-SNR ``log(alpha/sigma)`` with ``alpha = 1 - sigma``.

    Returns ``-inf`` at ``sigma == 1`` (alpha 0, only the txt2img start sigma)
    and ``+inf`` at ``sigma == 0`` (the terminal); both are consumed only in
    branches where the result is finite.
    """
    sigma = float(sigma)
    alpha = 1.0 - sigma
    if alpha <= 0.0:
        return -math.inf
    if sigma <= 0.0:
        return math.inf
    return math.log(alpha) - math.log(sigma)


def _build_R_b(rks: list[float], hh: float, order: int, solver_type: str,
               device=None, dtype=None) -> tuple[Tensor, Tensor, float, float]:
    """Build the UniPC linear system (R, b) plus ``h_phi_1`` and ``B_h``.

    Verbatim from the reference's ``multistep_uni_*_bh_update`` inner loop.
    R/b are built from Python floats and solved back to Python floats, so they
    stay fp32 on CPU regardless of the model's device/dtype — cusolver has no
    bf16 LU factorization, and a CUDA round-trip for an order<=3 system is
    pure overhead. ``device``/``dtype`` are accepted-and-ignored.
    """
    h_phi_1 = math.expm1(hh)  # e^hh - 1
    h_phi_k = h_phi_1 / hh - 1.0
    B_h = hh if solver_type == "bh1" else math.expm1(hh)

    R_rows: list[list[float]] = []
    b_vals: list[float] = []
    factorial_i = 1
    for i in range(1, order + 1):
        R_rows.append([rk ** (i - 1) for rk in rks])
        b_vals.append(h_phi_k * factorial_i / B_h)
        factorial_i *= i + 1
        h_phi_k = h_phi_k / hh - 1.0 / factorial_i

    R = torch.tensor(R_rows, dtype=torch.float32)
    b = torch.tensor(b_vals, dtype=torch.float32)
    return R, b, h_phi_1, B_h


def _predictor(x, m0, x0_prev, sigma_prev, s0, sigma_target, order, solver_type):
    """UniP B(h) update (predict_x0). Returns x at ``sigma_target``.

    ``x0_prev``/``sigma_prev`` are the x0 predictions/sigmas BEFORE the current
    step, most-recent-last.
    """
    alpha_t, sigma_t = 1.0 - float(sigma_target), float(sigma_target)
    alpha_s0, sigma_s0 = 1.0 - float(s0), float(s0)
    lam_t, lam_s0 = _lambda(sigma_target), _lambda(s0)
    h = lam_t - lam_s0

    rks: list[float] = []
    D1s: list[Tensor] = []
    for i in range(1, order):
        lam_si = _lambda(sigma_prev[-i])
        rk = (lam_si - lam_s0) / h
        rks.append(rk)
        D1s.append((x0_prev[-i] - m0) / rk)
    rks.append(1.0)

    hh = -h  # predict_x0
    R, b, h_phi_1, B_h = _build_R_b(rks, hh, order, solver_type, x.device, x.dtype)

    if D1s:
        if order == 2:
            rhos_p = [0.5]
        else:
            rhos_p = torch.linalg.solve(R[:-1, :-1], b[:-1]).tolist()
    else:
        rhos_p = []

    x_t = (sigma_t / sigma_s0) * x - alpha_t * h_phi_1 * m0
    for k, d in enumerate(D1s):
        x_t = x_t - alpha_t * B_h * (rhos_p[k] * d)
    return x_t


def _corrector(last_sample, m0, m_this, x0_prev2, sigma_prev2, s0, s_this,
               order, solver_type):
    """UniC B(h) update (predict_x0). Refines the current sample in place.

    ``m0`` is the previous step's x0 prediction, ``m_this`` the current step's;
    ``x0_prev2``/``sigma_prev2`` are the x0 preds/sigmas BEFORE the previous
    step, most-recent-last. ``s_this`` is the current sigma, ``s0`` the previous.
    """
    alpha_t, sigma_t = 1.0 - float(s_this), float(s_this)
    alpha_s0, sigma_s0 = 1.0 - float(s0), float(s0)
    lam_t, lam_s0 = _lambda(s_this), _lambda(s0)
    h = lam_t - lam_s0

    rks: list[float] = []
    D1s: list[Tensor] = []
    for i in range(1, order):
        lam_si = _lambda(sigma_prev2[-i])
        rk = (lam_si - lam_s0) / h
        rks.append(rk)
        D1s.append((x0_prev2[-i] - m0) / rk)
    rks.append(1.0)

    hh = -h  # predict_x0
    R, b, h_phi_1, B_h = _build_R_b(rks, hh, order, solver_type,
                                    last_sample.device, last_sample.dtype)

    if order == 1:
        rhos_c = [0.5]
    else:
        rhos_c = torch.linalg.solve(R, b).tolist()

    x_t = (sigma_t / sigma_s0) * last_sample - alpha_t * h_phi_1 * m0
    for k, d in enumerate(D1s):  # rhos_c[:-1] pair with D1s
        x_t = x_t - alpha_t * B_h * (rhos_c[k] * d)
    D1_t = m_this - m0
    x_t = x_t - alpha_t * B_h * (rhos_c[-1] * D1_t)
    return x_t


@torch.no_grad()
def sample_unipc(
    model_fn,
    x: Tensor,
    sigmas: Tensor,
    guidance: GuidanceStrategy,
    cond: dict,
    uncond: dict | None = None,
    hooks=(),
    is_cancelled=None,
    *,
    solver_order: int = UNIPC_SOLVER_ORDER,
    solver_type: str = _SOLVER_TYPE,
    sampler_options: dict | None = None,
) -> Tensor:
    """Multistep UniPC loop. Same signature/semantics as :func:`sample_euler`.

    Hooks fire once per step (after the step, with the x0 estimate);
    ``is_cancelled()`` is polled each step and raises :class:`SamplingCancelled`.

    ``sampler_options['discontinuity_steps']`` (an iterable of step indices,
    set by :func:`~..denoise_loop.denoise` from its ``expert_boundary`` param):
    at each listed step, the predictor/corrector history is reset before that
    step runs, as if it were a fresh start. A multi-expert ``model_fn`` (e.g.
    Wan's ``_ExpertRouter``) calls a DIFFERENT network at and after its switch
    step than before it; without a reset, x0_hist/sigma_hist there mix x0
    estimates from two different models across the corrector's linear
    extrapolation and the predictor's rk-ratio history, which is not a
    continuous trajectory the solver's math has any reason to stay stable
    across. Otherwise this solver has no options of its own.
    """
    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)
    discontinuity_steps = frozenset((sampler_options or {}).get("discontinuity_steps") or ())

    s_in = x.new_ones((x.shape[0],))
    x0_hist: list[Tensor] = []
    sigma_hist: list[float] = []
    last_sample: Tensor | None = None
    lower_order_nums = 0
    carried_order = 0  # this_order from the previous step, used by the corrector

    try:
        for i in range(total_steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=i)

            if i in discontinuity_steps:
                last_sample = None
                x0_hist = []
                sigma_hist = []
                lower_order_nums = 0

            sigma = sigmas[i]
            sigma_next = sigmas[i + 1]

            v = guidance(model_fn, x, sigma * s_in, cond, uncond, i)
            x0 = x - sigma * v  # convert_model_output (predict_x0, flow)

            # Corrector: refine the current sample using this step's evaluation.
            if i > 0 and last_sample is not None:
                x = _corrector(
                    last_sample,
                    m0=x0_hist[-1],
                    m_this=x0,
                    x0_prev2=x0_hist[:-1],
                    sigma_prev2=sigma_hist[:-1],
                    s0=sigma_hist[-1],
                    s_this=float(sigma),
                    order=carried_order,
                    solver_type=solver_type,
                )

            x0_hist.append(x0)
            sigma_hist.append(float(sigma))

            # lower_order_final + multistep warmup.
            this_order = min(solver_order, total_steps - i)
            this_order = min(this_order, lower_order_nums + 1)
            carried_order = this_order

            last_sample = x
            if sigma_next == 0:
                # Terminal: the order-1 predictor to sigma 0 collapses to the x0
                # prediction (avoids the +inf lambda at sigma == 0).
                x_next = x0
            else:
                x_next = _predictor(
                    x,
                    m0=x0,
                    x0_prev=x0_hist[:-1],
                    sigma_prev=sigma_hist[:-1],
                    s0=float(sigma),
                    sigma_target=float(sigma_next),
                    order=this_order,
                    solver_type=solver_type,
                )

            if lower_order_nums < solver_order:
                lower_order_nums += 1

            x = x_next
            try:
                run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0)
            except SamplingNumericsError as err:
                # Enrich with THIS solver's own state at the moment of
                # failure -- generic to run_hooks/NumericsWatchdog, so it has
                # no way to know a multistep sampler's order/history depth.
                err.solver_order = carried_order
                err.history_depth = len(x0_hist)
                raise
    finally:
        run_hooks(hooks, "on_end")

    return x
