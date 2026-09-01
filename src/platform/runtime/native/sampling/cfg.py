"""Guidance strategies for the flow-matching sampler.

The sampler never branches on *how* guidance works; it holds one
``GuidanceStrategy`` object and calls it each step. Three implementations cover
every native target:

* :class:`EmbeddedGuidance` — Flux-style distilled guidance. A single forward
  pass; the guidance scale is fed into the model through the conditioning dict
  under the ``"guidance"`` key (see the conditioning-dict contract below).
* :class:`TrueCFG` — classic classifier-free guidance. Two forward passes
  (uncond + cond) combined as ``uncond + scale * (cond - uncond)``. Used by
  Wan (later). Scale may be a per-step list. Exposes ``last_uncond_v`` (the
  raw, un-combined uncond-branch velocity of the most recent step) as a CFG++
  anchor -- see :mod:`.algorithms.euler_ancestral_cfg_pp`.
* :class:`NoCFG` — a single forward pass, no guidance manipulation.

Conditioning-dict contract
---------------------------
``model_fn`` has the shape ``model_fn(x, sigma, conditioning) -> velocity``
where ``conditioning`` is an opaque ``dict`` the generator-side adapter knows
how to unpack into its arch ``forward(x, timestep, context, y, guidance)``.
The sampling core treats it as opaque with exactly one reserved key:

* :class:`EmbeddedGuidance` shallow-copies the ``cond`` dict and injects
  ``conditioning["guidance"] = torch.full((batch,), scale)`` (batch/device/
  dtype taken from ``x``) before the single forward. The adapter routes that
  tensor to the DiT's ``guidance_in`` embedding.

``TrueCFG`` and ``NoCFG`` never touch the dict; they pass ``cond`` (and
``uncond`` for CFG) straight through.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

import torch

Tensor = torch.Tensor

# A model_fn maps (x, sigma, conditioning_dict) -> predicted velocity.
ScaleLike = float | int | Sequence[float]


def _scale_at(scale: ScaleLike, step_index: int) -> float:
    """Resolve a scalar-or-per-step scale to a float for ``step_index``.

    A list shorter than the step count clamps to its last entry (so a partial
    schedule never indexes out of range).
    """
    if isinstance(scale, (int, float)):
        return float(scale)
    seq = list(scale)
    if not seq:
        raise ValueError("per-step guidance scale list is empty")
    idx = step_index if step_index < len(seq) else len(seq) - 1
    return float(seq[idx])


@runtime_checkable
class GuidanceStrategy(Protocol):
    """Combines model forward pass(es) into a single velocity for one step."""

    def __call__(
        self,
        model_fn,
        x: Tensor,
        sigma: Tensor,
        cond: dict,
        uncond: dict | None,
        step_index: int,
    ) -> Tensor:
        ...


class NoCFG:
    """Single forward pass, conditioning passed through untouched.

    ``last_cond_v``/``degraded_forward``/``zero_init_steps`` are the SLG hook
    surface (see :class:`SkipLayerGuidance`): the strategy exposes its last
    conditional prediction and knows how to run its own conditioning transform
    with a degraded (skip-layers) pass.
    """

    zero_init_steps = 0

    def __init__(self) -> None:
        self.last_cond_v: Tensor | None = None

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index) -> Tensor:
        self.last_cond_v = model_fn(x, sigma, cond)
        return self.last_cond_v

    def degraded_forward(self, model_fn, x, sigma, cond, step_index, skip_layers) -> Tensor:
        return model_fn(x, sigma, {**cond, "skip_layers": skip_layers})


class EmbeddedGuidance:
    """Flux distilled guidance: one forward, scale injected into conditioning.

    ``scale`` may be a scalar or a per-step sequence (LTX-style).
    """

    zero_init_steps = 0

    def __init__(self, scale: ScaleLike) -> None:
        self.scale = scale
        self.last_cond_v: Tensor | None = None

    def _guidance_tensor(self, x, step_index) -> Tensor:
        scale = _scale_at(self.scale, step_index)
        return torch.full((x.shape[0],), scale, device=x.device, dtype=x.dtype)

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index) -> Tensor:
        conditioning = {**cond, "guidance": self._guidance_tensor(x, step_index)}
        self.last_cond_v = model_fn(x, sigma, conditioning)
        return self.last_cond_v

    def degraded_forward(self, model_fn, x, sigma, cond, step_index, skip_layers) -> Tensor:
        # Replicate the guidance injection so the degraded pass differs from the
        # main pass ONLY by the skipped layers (SLG S10) — not by a dropped
        # "guidance" key that a distilled model would choke on.
        conditioning = {**cond, "guidance": self._guidance_tensor(x, step_index), "skip_layers": skip_layers}
        return model_fn(x, sigma, conditioning)


def _cfg_zero_star_alpha(cond: Tensor, uncond: Tensor, eps: float = 1e-8) -> Tensor:
    """Per-batch-element optimal rescale of ``uncond`` onto ``cond`` (CFG-Zero*).

    ``alpha = dot(cond_flat, uncond_flat) / (||uncond_flat||^2 + eps)``, computed
    in float32 over all non-batch dims flattened together (matches kijai's
    ``optimized_scale`` in WanVideoWrapper). Returned with ``cond``'s dtype,
    broadcastable against the original (unflattened) tensor shape.
    """
    batch = cond.shape[0]
    cond_flat = cond.reshape(batch, -1).float()
    uncond_flat = uncond.reshape(batch, -1).float()
    dot = (cond_flat * uncond_flat).sum(dim=-1, keepdim=True)
    norm_sq = (uncond_flat * uncond_flat).sum(dim=-1, keepdim=True)
    alpha = dot / (norm_sq + eps)
    return alpha.view(batch, *([1] * (cond.ndim - 1))).to(cond.dtype)


def _batch_flat_dot(a: Tensor, b: Tensor) -> Tensor:
    """Per-batch-element dot product over all non-batch dims, float32, shape (batch, 1)."""
    batch = a.shape[0]
    return (a.reshape(batch, -1).float() * b.reshape(batch, -1).float()).sum(dim=-1, keepdim=True)


def _project_parallel_orthogonal(delta: Tensor, ref: Tensor, eps: float = 1e-8) -> tuple[Tensor, Tensor]:
    """Decompose ``delta`` into components parallel/orthogonal to ``ref``, per batch element.

    APG (arXiv:2410.02416) eq.: ``delta_parallel = <delta, ref>/<ref, ref> * ref``,
    ``delta_orthogonal = delta - delta_parallel``. Computed in float32 over flattened
    non-batch dims (matches :func:`_cfg_zero_star_alpha`'s pattern); returned in
    ``delta``'s original shape/dtype.
    """
    batch = delta.shape[0]
    dot = _batch_flat_dot(delta, ref)
    norm_sq = _batch_flat_dot(ref, ref)
    coeff = dot / (norm_sq + eps)
    ref_flat = ref.reshape(batch, -1).float()
    delta_flat = delta.reshape(batch, -1).float()
    parallel_flat = coeff * ref_flat
    orthogonal_flat = delta_flat - parallel_flat
    parallel = parallel_flat.view(delta.shape).to(delta.dtype)
    orthogonal = orthogonal_flat.view(delta.shape).to(delta.dtype)
    return parallel, orthogonal


def _rescale_norm(delta: Tensor, threshold: float, eps: float = 1e-8) -> Tensor:
    """APG's norm-based rescale: ``delta * min(1, threshold / ||delta||)`` per batch element."""
    batch = delta.shape[0]
    flat = delta.reshape(batch, -1)
    norm = flat.norm(dim=-1, keepdim=True)
    factor = (threshold / (norm + eps)).clamp(max=1.0)
    return (flat * factor).view(delta.shape).to(delta.dtype)


class TrueCFG:
    """Classifier-free guidance: uncond + scale * (cond - uncond).

    ``scale`` may be a scalar or a per-step sequence. When the effective scale
    is ~1.0 the uncond pass is skipped (it would be a no-op), saving a forward.

    Two optional, paper-backed corrections (CFG-Zero*, see
    https://github.com/WeichenFan/CFG-Zero-star, used by kijai's WanVideoWrapper):

    * ``cfg_zero_star`` (default ``True``) — before combining, rescale
      ``uncond`` onto ``cond``'s direction via :func:`_cfg_zero_star_alpha`:
      ``out = uncond*alpha + scale*(cond - uncond*alpha)``. Free (no extra
      forward pass); disable via the kill-switch if it ever regresses a model.
    * ``zero_init_steps`` (default ``0`` = off) — for the first N steps, skip
      both forward passes and return a zero velocity prediction outright
      (kijai's zero-init trick for flow-matching CFG).

    A third, optional correction: **APG** (Adaptive Projected Guidance,
    arXiv:2410.02416, "Eliminating Oversaturation and Artifacts of High
    Guidance Scales in Diffusion Models"). Re-derived here from the paper's
    method section (not ported from any implementation). CFG's oversaturation
    comes from the component of the guidance delta that's *parallel* to the
    conditional prediction; APG down-weights that component, optionally caps
    the delta's norm, and optionally smooths it with a (negative, "reverse")
    momentum term across steps.

    The paper works in denoised-prediction (x0) space; our models predict flow
    velocity ``v``. Since ``x0 = x - sigma*v`` is an affine map, the DELTA maps
    linearly (``delta_x0 = -sigma * delta_v``), but the projection REFERENCE
    vector does not: ``x0_cond = x - sigma*cond_v`` is not parallel to
    ``cond_v`` (it carries the ``x`` offset), so projecting in x0-space is not
    equivalent to projecting ``delta_v`` against ``cond_v`` directly. This
    implementation therefore builds ``x0_cond``/``delta_x0`` (free — reuses the
    ``cond_v``/``uncond_v`` already computed, no extra forward pass), does the
    momentum/rescale/decomposition there, then maps the processed delta back to
    velocity space (``delta_v = -delta_x0 / sigma``) before combining exactly
    like the plain-CFG formula.

    APG parameters (all optional, all default to a no-op so the class is
    BIT-IDENTICAL to plain TrueCFG unless a preset opts in):

    * ``apg_eta`` (default ``1.0``) — parallel-component weight; ``1.0``
      recovers plain CFG exactly (the paper's degenerate case), lower values
      (paper explores ``~0.0-0.5``) suppress oversaturation.
    * ``apg_norm_threshold`` (default ``0.0`` = disabled) — caps
      ``||delta_x0||`` to this radius via :func:`_rescale_norm`.
    * ``apg_momentum`` (default ``0.0`` = disabled) — "reverse momentum"
      coefficient (paper uses a small *negative* value, e.g. ``-0.5``):
      ``delta_x0 <- delta_x0 + apg_momentum * running_average`` before
      rescale/decomposition, and the result becomes the new running average.
      The running average is instance state reset by :meth:`reset_momentum`
      (also called implicitly by ``__init__`` — safe by construction since
      ``_make_guidance`` builds a fresh strategy per ``denoise()`` call; call
      it explicitly if an instance is ever reused across generations).

    Processing order when any APG parameter is active: momentum -> norm
    rescale -> parallel/orthogonal decomposition -> ``eta``-weighted
    recombination -> map back to velocity space. APG is applied AFTER the
    CFG-Zero* rescale (i.e. on the already-alpha-corrected delta), matching
    the existing correction order.

    Two further optional, paper-backed corrections (TRELLIS.2, arXiv:2512.14692):

    * ``guidance_rescale`` (default ``0.0`` = off, a.k.a. "cfg rescale" /
      "Common Diffusion Noise Schedules and Sample Steps are Flawed" §3.4) —
      applied LAST, after whichever combination above produced ``guided``:
      rescale ``guided`` by the std ratio of the raw conditional prediction
      (``cond_v``, never the CFG-Zero*-adjusted branch) over ``guided``'s own
      std, both taken over all non-batch dims per batch element, then blend
      by the factor: ``out = f*rescaled + (1-f)*guided``. ``0.0`` is an exact
      no-op (no extra computation, byte-identical to plain ``guided``).
    * ``interval`` (default ``None`` = always-on, a.k.a. "guidance interval")
      — a ``(lo, hi)`` window on sigma (which runs ``1 -> 0`` like TRELLIS.2's
      own normalized ``t``). Outside the window the uncond forward pass is
      SKIPPED entirely (cond-only, single forward) exactly like the existing
      ``scale ~= 1.0`` short-circuit. ``None`` never gates, so behaviour is
      unchanged for every caller that doesn't pass it.
    """

    def __init__(
        self,
        scale: ScaleLike,
        cfg_zero_star: bool = True,
        zero_init_steps: int = 0,
        apg_eta: float = 1.0,
        apg_norm_threshold: float = 0.0,
        apg_momentum: float = 0.0,
        guidance_rescale: float = 0.0,
        interval: tuple[float, float] | None = None,
    ) -> None:
        self.scale = scale
        self.cfg_zero_star = cfg_zero_star
        self.zero_init_steps = zero_init_steps
        self.apg_eta = apg_eta
        self.apg_norm_threshold = apg_norm_threshold
        self.apg_momentum = apg_momentum
        self.guidance_rescale = guidance_rescale
        self.interval = interval
        self.reset_momentum()

    def reset_momentum(self) -> None:
        """Clear the APG momentum running average (see ``apg_momentum``)."""
        self._apg_momentum_buf: Tensor | None = None
        self.last_cond_v: Tensor | None = None
        # CFG++ anchor (see .algorithms.euler_ancestral_cfg_pp): the raw
        # uncond-branch velocity of the most recent step, AFTER the CFG-Zero*
        # rescale (that correction is a free, deterministic reshaping of the
        # same forward pass, not a second guidance term -- CFG++ wants the
        # branch's own prediction, whichever correction already applies to
        # it). ``None`` whenever no uncond forward ran this step (scale~1.0,
        # ``uncond is None``, or the zero-init window): callers must fall back
        # to the combined velocity, exactly like SkipLayerGuidance's
        # ``last_cond_v`` fallback below.
        self.last_uncond_v: Tensor | None = None

    def degraded_forward(self, model_fn, x, sigma, cond, step_index, skip_layers) -> Tensor:
        """SLG hook: a single conditional forward with the layers skipped."""
        return model_fn(x, sigma, {**cond, "skip_layers": skip_layers})

    @property
    def _apg_active(self) -> bool:
        return not (self.apg_eta == 1.0 and self.apg_norm_threshold == 0.0 and self.apg_momentum == 0.0)

    def _skip_uncond(self, sigma: Tensor, scale: float, uncond: dict | None) -> bool:
        if uncond is None or abs(scale - 1.0) < 1e-6:
            return True
        if self.interval is None:
            return False
        lo, hi = self.interval
        t = float(sigma.reshape(-1)[0])
        return not (lo <= t <= hi)

    def _apply_guidance_rescale(self, cond_v: Tensor, guided: Tensor) -> Tensor:
        """TRELLIS.2 CFG rescale (arXiv:2512.14692): pulls ``guided`` back toward
        ``cond_v``'s std to counter oversaturation from a large ``scale``.
        ``guidance_rescale <= 0.0`` is checked by the caller and never reaches
        here -- this method always does the (float32) std/blend work."""
        batch = guided.shape[0]
        std_pos = cond_v.reshape(batch, -1).float().std(dim=-1, keepdim=True)
        std_guided = guided.reshape(batch, -1).float().std(dim=-1, keepdim=True)
        factor = (std_pos / std_guided.clamp(min=1e-8)).view(batch, *([1] * (guided.ndim - 1)))
        rescaled = guided * factor.to(guided.dtype)
        f = self.guidance_rescale
        return (f * rescaled + (1.0 - f) * guided).to(guided.dtype)

    def _finish(self, cond_v: Tensor, guided: Tensor) -> Tensor:
        if self.guidance_rescale <= 0.0:
            return guided
        return self._apply_guidance_rescale(cond_v, guided)

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index) -> Tensor:
        if step_index < self.zero_init_steps:
            return torch.zeros_like(x)
        scale = _scale_at(self.scale, step_index)
        cond_v = model_fn(x, sigma, cond)
        self.last_cond_v = cond_v  # SLG anchor (see SkipLayerGuidance)
        if self._skip_uncond(sigma, scale, uncond):
            self.last_uncond_v = None  # no uncond branch ran -- see reset_momentum
            return cond_v
        uncond_v = model_fn(x, sigma, uncond)
        if self.cfg_zero_star:
            uncond_v = uncond_v * _cfg_zero_star_alpha(cond_v, uncond_v)
        self.last_uncond_v = uncond_v  # CFG++ anchor (see reset_momentum)

        delta_v = cond_v - uncond_v
        if not self._apg_active:
            return self._finish(cond_v, uncond_v + scale * delta_v)

        # The whole APG projection runs in fp32 (S1): ``sigma`` cast to the model
        # dtype underflows to 0.0 in fp16 at a small/zero sigma, which then feeds a
        # 0/0 into ``delta_v_final``. fp32 sigma never underflows, and the
        # projection helpers already promote to fp32 internally.
        sigma_view = sigma.reshape(x.shape[0], *([1] * (x.ndim - 1))).float()
        eps = 1e-8
        # At sigma == 0 the x0-space mapping (delta_x0 = -sigma*delta_v) collapses
        # to 0 and APG is a no-op (S1): fall back to plain CFG rather than divide
        # 0/0 (euler_restart can probe at sigma_low == 0).
        if float(sigma_view.max()) == 0.0:
            return self._finish(cond_v, uncond_v + scale * delta_v)

        x0_cond = x - sigma_view * cond_v
        delta_x0 = -sigma_view * delta_v  # linear image of delta_v under x0 = x - sigma*v

        if self.apg_momentum != 0.0:
            prev = self._apg_momentum_buf
            if prev is None or prev.shape != delta_x0.shape:
                prev = torch.zeros_like(delta_x0)
            delta_x0 = delta_x0 + self.apg_momentum * prev
            self._apg_momentum_buf = delta_x0

        if self.apg_norm_threshold > 0.0:
            delta_x0 = _rescale_norm(delta_x0, self.apg_norm_threshold, eps)

        parallel, orthogonal = _project_parallel_orthogonal(delta_x0, x0_cond, eps)
        delta_x0_final = orthogonal + self.apg_eta * parallel

        delta_v_final = (-delta_x0_final / sigma_view.clamp(min=eps)).to(cond_v.dtype)
        # Anchor at the CONDITIONAL prediction (S3): out = cond + (scale-1)*delta.
        # For plain CFG this equals uncond + scale*delta, but once the delta is
        # APG-processed the two anchors diverge and the conditional one is correct
        # (suppressing a wholly-parallel delta must retain x0_cond, not x0_uncond).
        return self._finish(cond_v, cond_v + (scale - 1.0) * delta_v_final)


class SkipLayerGuidance:
    """Skip-Layer Guidance (SLG): wraps another :class:`GuidanceStrategy` and,
    within a mid-trajectory sigma window, adds ONE extra forward pass with a
    chosen set of transformer blocks skipped (identity passthrough) as a
    deliberately-degraded prediction, then pushes the combined output away
    from it. This is the SD3.5 / ComfyUI ``SkipLayerGuidanceDiT`` CONCEPT —
    there is no paper, and the ComfyUI reference implementation is GPL, so
    this is re-derived from the public description only, not ported from any
    implementation.

    Composition, not inheritance: wraps ANY inner :class:`GuidanceStrategy`
    (:class:`EmbeddedGuidance`, :class:`TrueCFG` including its CFG-Zero*/APG
    corrections, or :class:`NoCFG`) — SLG doesn't care how the "normal"
    prediction was produced, it just perturbs it afterwards.

    Formula: within the window, ``final = out + slg_scale * (out - degraded)``
    where ``out`` is the inner strategy's own combined result, standing in for
    the "cond" reference direction so only ONE extra forward pass is needed
    (for a single-pass inner strategy ``out`` already IS the cond prediction;
    for CFG it's the guided combination, which SLG reinforces against the
    degraded pass). Outside the window, or ``slg_scale <= 0``, or an empty
    ``layers`` set, this is a pure passthrough to ``inner`` — zero extra
    forward passes, byte-identical to not wrapping at all.

    ``skip_layers`` delivery: ``model_fn``'s contract is fixed at
    ``(x, sigma, conditioning) -> velocity`` with no extra kwargs (see the
    module docstring's conditioning-dict contract), so the degraded pass
    injects a reserved ``conditioning["skip_layers"]`` key into a shallow copy
    of ``cond`` — the SAME seam :class:`EmbeddedGuidance` already uses for
    ``"guidance"``. The generator-side ``model_forward`` adapter is expected to
    pop this key and route it to the arch module's ``skip_layers=`` kwarg
    (e.g. ``arch/wan/model.py``'s ``WanModel.forward``); wiring that adapter
    for the Wan generator pipes is a follow-up, out of this class's scope.

    ``sigma_start``/``sigma_end`` bound the window INCLUSIVELY:
    ``sigma_end <= sigma <= sigma_start``. Sigma descends ``1 -> 0`` across a
    generation, so ``sigma_start`` is the earlier/higher bound and
    ``sigma_end`` the later/lower one.
    """

    def __init__(
        self,
        inner: GuidanceStrategy,
        slg_scale: float,
        layers: "set[int] | frozenset[int]",
        sigma_start: float,
        sigma_end: float,
    ) -> None:
        self.inner = inner
        self.slg_scale = slg_scale
        self.layers = set(layers) if layers else set()
        self.sigma_start = sigma_start
        self.sigma_end = sigma_end

    def _in_window(self, sigma_val: float, eps: float = 1e-6) -> bool:
        # sigma arrives as a float32 (or lower) tensor value; round-tripping a
        # python-float bound like 0.8 through float32 can land a hair off
        # either side, so the inclusive edges need slack to actually include
        # the boundary sigma a caller asked for.
        return self.sigma_end - eps <= sigma_val <= self.sigma_start + eps

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index) -> Tensor:
        out = self.inner(model_fn, x, sigma, cond, uncond, step_index)
        # CFG++ anchor passthrough (see .algorithms.euler_ancestral_cfg_pp):
        # SLG wraps an inner strategy without inheriting from it, so a caller
        # reading ``last_uncond_v`` off THIS object (the one denoise_loop
        # actually holds) would otherwise never see the inner TrueCFG's value.
        self.last_uncond_v = getattr(self.inner, "last_uncond_v", None)
        if self.slg_scale <= 0.0 or not self.layers:
            return out

        # S9: within the inner strategy's zero-init window ``out`` is the
        # deliberate zero velocity; perturbing it would defeat zero-init.
        if step_index < getattr(self.inner, "zero_init_steps", 0):
            return out

        sigma_val = float(sigma.reshape(-1)[0])
        if not self._in_window(sigma_val):
            return out

        # S5: push from the CONDITIONAL prediction, not the CFG-amplified ``out``
        # (using ``out`` double-applies the CFG scale to the SLG delta). The inner
        # strategy records its last conditional prediction; NoCFG/Embedded ``out``
        # already IS that, so the fallback is exact.
        v_cond = getattr(self.inner, "last_cond_v", None)
        if v_cond is None:
            v_cond = out
        degraded_v = self._degraded_forward(model_fn, x, sigma, cond, step_index)
        return out + self.slg_scale * (v_cond - degraded_v)

    def _degraded_forward(self, model_fn, x, sigma, cond, step_index) -> Tensor:
        # S10: replicate the inner strategy's own conditioning transform for the
        # degraded pass (e.g. EmbeddedGuidance's injected "guidance"), so the pass
        # differs from the main one ONLY by the skipped layers.
        fn = getattr(self.inner, "degraded_forward", None)
        if fn is not None:
            return fn(model_fn, x, sigma, cond, step_index, self.layers)
        return model_fn(x, sigma, {**cond, "skip_layers": self.layers})
