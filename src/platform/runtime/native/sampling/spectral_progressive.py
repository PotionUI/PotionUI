"""Spectral Progressive Diffusion — grow latent resolution along the denoise path.

Reference: "Spectral Progressive Diffusion for Efficient Image and Video
Generation" (arXiv:2605.18736) + the authors' MIT-licensed reference
(github.com/howardhx/speed). Re-derived here from the paper's equations and that
MIT source (both permissive) into our flow-matching sigma convention.

Idea (training-free): diffusion models fill the frequency domain coarse-to-fine —
low frequencies emerge early, high frequencies late. So run the EARLY (high-sigma)
steps on a REDUCED-resolution latent (cheap), and only grow to full resolution as
the high-frequency bands stop being noise-dominated. At each growth a *spectral
noise expansion* embeds the low-res spectrum into a larger one and fills the new
high-frequency band with sigma-scaled Gaussian noise, keeping the state on the
flow-matching path ``x = (1 - sigma) * x0 + sigma * noise``.

Convention note: the paper's timestep ``t`` is our ``sigma`` (both parametrise the
same flow ``x_t = (1-t) x0 + t eps``). All ``t`` below are sigmas in [0, 1].

Schedule (Propositions 1-2): the transition sigma from scale ``s_i`` to ``s_{i+1}``
is the δ-optimal activation time of the frequency at ``s_i``'s Nyquist limit,
under a radial power-law spectrum ``P(ω) = A |ω|^{-β}``. One tolerance ``δ`` sets
them all. Transitions come out DESCENDING (smaller scale → lower Nyquist frequency
→ higher power → later/higher activation sigma).

Alignment (Eq. 6): after filling the new band at level ``sigma``, both the latent
and the sigma are scaled by ``kappa(sigma, r) = r / (1 + (r-1) sigma)`` (``r`` the
linear upscale ratio), which restores a valid on-path noise level. ``kappa >= 1``
for ``r >= 1``, so the sigma JUMPS UP at a transition and the next (higher-res)
stage re-descends from there — like a restart that also grows resolution.

This module is the math + a staged orchestrator over the existing samplers; it is
opt-in and GPU-validation is pending (CPU-tested here). FBCache composes for free
(its probe shape-mismatch resets the cache at each transition); trajectory
warm-start is mutually exclusive (a different-resolution trajectory) and the
engine gates them apart.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import torch

logger = logging.getLogger(__name__)

Tensor = torch.Tensor


# --- schedule derivation --------------------------------------------------

def power_spectrum(omega: float, amplitude: float, beta: float) -> float:
    """Radial power-law spectrum ``A |ω|^{-β}`` (natural-image statistics)."""
    return amplitude * abs(omega) ** (-beta)


def activation_time(power: float, delta: float) -> float:
    """δ-optimal activation sigma for a band of expected ``power`` (Prop. 1).

    Below this sigma the band's velocity-prediction error exceeds ``δ`` (it is
    still noise-dominated), so growing resolution earlier wastes compute on
    frequencies that aren't informative yet.
    """
    return 1.0 / (1.0 + math.sqrt(delta / (power * (1.0 + power - delta))))


def kappa(sigma: float, ratio: float) -> float:
    """Amplitude / timestep alignment factor (Eq. 6): ``r / (1 + (r-1) sigma)``."""
    return ratio / (1.0 + (ratio - 1.0) * sigma)


@dataclass(frozen=True)
class SpectralProgressiveConfig:
    """Opt-in spectral-progressive settings.

    ``scales``: strictly-increasing resolution fractions ENDING at ``1.0`` (e.g.
    ``[0.5, 1.0]`` = one growth). ``delta``: single error tolerance driving the
    derived transitions. ``power_beta``/``power_amplitude``: the assumed radial
    power-law. ``basis``: spectral basis for the expansion (``"fft"`` is native
    torch / GPU-ready; ``"dct"`` matches the paper default via scipy on CPU).
    ``transitions``: optional explicit transition sigmas (DESCENDING, one per
    growth) overriding the derived schedule.
    """

    scales: tuple[float, ...] = (0.5, 1.0)
    delta: float = 0.01
    power_beta: float = 2.5
    power_amplitude: float = 1.0
    basis: str = "fft"
    transitions: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        s = self.scales
        if len(s) < 2 or any(b <= a for a, b in zip(s, s[1:])) or abs(s[-1] - 1.0) > 1e-9:
            raise ValueError(f"scales must be strictly increasing and end at 1.0, got {s}")
        if not (0.0 < self.delta < 1.0):
            raise ValueError(f"delta must be in (0, 1), got {self.delta}")
        if self.basis not in ("fft", "dct"):
            raise ValueError(f"unknown spectral basis {self.basis!r} (expected 'fft'/'dct')")
        if self.transitions is not None and len(self.transitions) != len(s) - 1:
            raise ValueError(
                f"transitions must have {len(s) - 1} entries (one per growth), got {self.transitions}"
            )


def derive_transitions(cfg: SpectralProgressiveConfig, height: int, width: int) -> list[float]:
    """Transition sigmas (descending) for growing across ``cfg.scales``.

    ``height``/``width`` are the FULL-resolution latent spatial dims; the Nyquist
    limit is ``min(H, W) / 2``. Returns one sigma per growth. An explicit
    ``cfg.transitions`` short-circuits the derivation.
    """
    if cfg.transitions is not None:
        return list(cfg.transitions)
    omega_max = min(height, width) / 2.0
    out = []
    for s_i in cfg.scales[:-1]:
        omega_i = max(s_i * omega_max, 1e-6)
        p = power_spectrum(omega_i, cfg.power_amplitude, cfg.power_beta)
        out.append(activation_time(p, cfg.delta))
    return out


# --- spectral noise expansion ---------------------------------------------

def _fft_expand(x: Tensor, target_hw: tuple[int, int], sigma: float,
                generator: torch.Generator | None) -> Tensor:
    """FFT spectral expansion: embed ``x``'s centred spectrum into the target and
    fill the new high-frequency band with ``sigma``-scaled complex Gaussian noise
    (orthonormal FFT, matching the reference)."""
    b, c, h, w = x.shape
    ht, wt = target_hw
    xs = torch.fft.fftshift(torch.fft.fft2(x, norm="ortho"), dim=(-2, -1))
    nr = _randn((b, c, ht, wt), x, generator)
    ni = _randn((b, c, ht, wt), x, generator)
    big = sigma * torch.complex(nr, ni) / math.sqrt(2.0)
    ph, pw = (ht - h) // 2, (wt - w) // 2
    big[..., ph:ph + h, pw:pw + w] = xs
    return torch.fft.ifft2(torch.fft.ifftshift(big, dim=(-2, -1)), norm="ortho").real.to(x.dtype)


def _dct_expand(x: Tensor, target_hw: tuple[int, int], sigma: float,
                generator: torch.Generator | None) -> Tensor:
    """DCT spectral expansion (paper default): low-res coefficients in the top-left,
    new band filled with ``sigma``-scaled noise (orthonormal DCT-II via scipy —
    CPU; a torch-native DCT is the GPU follow-up)."""
    from scipy.fft import dctn, idctn  # local import: only when the DCT basis is used
    import numpy as np

    b, c, h, w = x.shape
    ht, wt = target_hw
    noise = _randn((b, c, ht, wt), x, generator).cpu().numpy()
    src = x.detach().float().cpu().numpy()
    out = np.empty((b, c, ht, wt), dtype=np.float32)
    for bi in range(b):
        for ci in range(c):
            coeffs = dctn(src[bi, ci], type=2, norm="ortho")
            big = (sigma * noise[bi, ci]).astype(np.float32)
            big[:h, :w] = coeffs
            out[bi, ci] = idctn(big, type=2, norm="ortho")
    return torch.from_numpy(out).to(device=x.device, dtype=x.dtype)


def _randn(shape, ref: Tensor, generator: torch.Generator | None) -> Tensor:
    if generator is None:
        return torch.randn(shape, device=ref.device, dtype=torch.float32)
    return torch.randn(shape, generator=generator, device=ref.device, dtype=torch.float32)


def expand_and_align(x: Tensor, sigma: float, target_hw: tuple[int, int],
                     cfg: SpectralProgressiveConfig,
                     generator: torch.Generator | None = None) -> tuple[Tensor, float]:
    """Grow ``x`` (at noise level ``sigma``) to ``target_hw`` and align.

    Returns ``(x_aligned, sigma_aligned)``. ``ratio`` is the linear upscale
    ``sqrt(new_pixels / old_pixels)`` — the reference derives it from the linear
    dim; we use the height ratio (square-preserving) which equals it for the usual
    isotropic growth. Both the latent and sigma are scaled by ``kappa`` (Eq. 6).
    """
    _, _, h, w = x.shape
    ht, wt = target_hw
    if (ht, wt) == (h, w):
        return x, sigma
    ratio = ht / h  # isotropic growth; == wt / w for square scaling
    expand = _fft_expand if cfg.basis == "fft" else _dct_expand
    grown = expand(x, target_hw, sigma, generator)
    k = kappa(sigma, ratio)
    return grown * k, k * sigma


def stage_shape(latent_shape: tuple[int, ...], scale: float, multiple: int = 2) -> tuple[int, ...]:
    """Latent shape at a resolution ``scale``, H/W snapped DOWN to a ``multiple``.

    Keeps batch/channel (and any leading temporal axes) intact; only the trailing
    two spatial dims scale. Snapping to a patch multiple keeps the arch's
    patchify/pack valid at every stage.
    """
    *lead, h, w = latent_shape
    if scale >= 1.0:
        return tuple(latent_shape)
    sh = max(multiple, (int(round(h * scale)) // multiple) * multiple)
    sw = max(multiple, (int(round(w * scale)) // multiple) * multiple)
    return (*lead, sh, sw)


# --- staged orchestrator --------------------------------------------------

def _shifted_subschedule(start_sigma: float, end_sigma: float, n: int, shift: float,
                         device, dtype) -> Tensor:
    """A constant-shift sigma ramp of ``n`` steps from ``start_sigma`` down to
    ``end_sigma`` (length ``n + 1``).

    Uses the ``ModelSamplingDiscreteFlow`` map ``σ(τ)=shift·τ/(1+(shift-1)τ)`` and
    its inverse so a stage's sub-schedule follows the model's shift within its
    sigma window (rather than a naive linear ramp). ``shift == 1`` is the identity.
    """
    def inv(sig):  # τ(σ)
        return sig / (shift - (shift - 1.0) * sig) if shift != 1.0 else sig

    def fwd(tau):  # σ(τ)
        return shift * tau / (1.0 + (shift - 1.0) * tau) if shift != 1.0 else tau

    tau = torch.linspace(inv(start_sigma), inv(end_sigma), n + 1, dtype=torch.float64)
    sig = torch.tensor([fwd(float(t)) for t in tau], dtype=torch.float32)
    sig[0] = start_sigma
    sig[-1] = end_sigma
    return sig.to(device=device, dtype=dtype)


def _allocate_steps(stage_ranges: list[tuple[float, float]], total_steps: int) -> list[int]:
    """Split ``total_steps`` across stages proportional to each sigma range (>=1 each)."""
    spans = [max(a - b, 1e-6) for a, b in stage_ranges]
    total_span = sum(spans)
    raw = [max(1, round(total_steps * s / total_span)) for s in spans]
    # nudge the final stage so the total matches exactly (cosmetic — sub-schedules
    # are per-stage regardless).
    diff = total_steps - sum(raw)
    raw[-1] = max(1, raw[-1] + diff)
    return raw


def denoise_spectral_progressive(
    model_forward,
    latents_full: Tensor,
    cond: dict,
    uncond: dict | None,
    *,
    steps: int,
    sampler,
    sampler_name: str,
    guidance,
    shift: float,
    cfg: SpectralProgressiveConfig,
    seed_noise: Tensor,
    hooks=(),
    is_cancelled=None,
    sampler_options: dict | None = None,
    generator: torch.Generator | None = None,
    patch_multiple: int = 2,
) -> Tensor:
    """Run a progressive-resolution denoise and return the FULL-res clean latent.

    ``latents_full`` carries the target (full-resolution) shape/device/dtype (its
    values are unused — txt2img starts from noise). ``seed_noise`` is the
    full-resolution seed noise; the first (reduced) stage is seeded by spectrally
    downsampling it so a given seed stays reproducible. ``sampler``/``guidance``
    are the already-built step algorithm + guidance strategy; ``shift`` is the
    model's constant sigma shift (dynamic-mu families are a follow-up — see
    module docstring). Everything else mirrors :func:`~.denoise_loop.denoise`.
    """
    device, dtype = latents_full.device, latents_full.dtype
    full_shape = tuple(latents_full.shape)
    *_, full_h, full_w = full_shape
    transitions = derive_transitions(cfg, full_h, full_w)  # descending, one per growth

    # Per-stage (start_sigma, end_sigma): stage 0 starts at 1.0; a growth at sigma
    # t_i ends stage i and the aligned sigma kappa*t_i starts stage i+1.
    stage_ranges: list[tuple[float, float]] = []
    start = 1.0
    for i, s_i in enumerate(cfg.scales):
        end = transitions[i] if i < len(transitions) else 0.0
        stage_ranges.append((start, end))
        if i < len(transitions):
            ratio = cfg.scales[i + 1] / s_i
            start = kappa(transitions[i], ratio) * transitions[i]  # sigma jumps up post-expansion
            start = min(start, 1.0)
    step_alloc = _allocate_steps(stage_ranges, steps)

    # Seed the first (reduced) stage: spectrally downsample the full-res seed noise
    # so the same seed gives a stable low-res start. Every stage's H/W is snapped
    # to the DiT's patch multiple so patchify stays legal at each resolution.
    stage0_shape = stage_shape(full_shape, cfg.scales[0], patch_multiple)
    x = _seed_stage0(seed_noise, stage0_shape)
    x = (stage_ranges[0][0]) * x  # pure noise scaled to sigma0 (== 1.0 * x here)

    total_span_steps = sum(step_alloc)
    logger.debug(
        "spectral-progressive: scales=%s transitions=%s steps/stage=%s basis=%s",
        list(cfg.scales), [round(t, 4) for t in transitions], step_alloc, cfg.basis,
    )

    # The NaN/Inf watchdog rides each stage (a bad backend still fails loudly);
    # FBCache is intentionally not stacked here (its shape-reset would handle the
    # transitions, but the two speedups are composed in a follow-up).
    from .hooks import with_numerics_watchdog
    watched = with_numerics_watchdog(hooks, sampler_name, sampler_options)

    step_offset = 0
    for i, s_i in enumerate(cfg.scales):
        start_sigma, end_sigma = stage_ranges[i]
        n = step_alloc[i]
        sigmas = _shifted_subschedule(start_sigma, end_sigma, n, shift, device, dtype)
        # Guidance step_index is offset so per-step schedules stay globally indexed.
        x = _run_stage(sampler, model_forward, x, sigmas, guidance, cond, uncond,
                       watched, is_cancelled, sampler_options, step_offset, total_span_steps)
        step_offset += n
        if i < len(transitions):
            target = stage_shape(full_shape, cfg.scales[i + 1], patch_multiple)[-2:]
            x, _ = expand_and_align(x, end_sigma, target, cfg, generator)
    return x


def _seed_stage0(seed_noise: Tensor, stage0_shape: tuple[int, ...]) -> Tensor:
    """Downsample full-res seed noise to the first stage's shape (spectral crop)."""
    if tuple(seed_noise.shape) == tuple(stage0_shape):
        return seed_noise
    *_, h, w = stage0_shape
    xs = torch.fft.fftshift(torch.fft.fft2(seed_noise, norm="ortho"), dim=(-2, -1))
    _, _, H, W = seed_noise.shape
    ph, pw = (H - h) // 2, (W - w) // 2
    cropped = xs[..., ph:ph + h, pw:pw + w]
    return torch.fft.ifft2(torch.fft.ifftshift(cropped, dim=(-2, -1)), norm="ortho").real.to(seed_noise.dtype)


class _OffsetGuidance:
    """Wrap a guidance strategy to add a fixed step-index offset (keeps per-step
    guidance schedules globally indexed across stages)."""

    def __init__(self, inner, offset: int) -> None:
        self.inner = inner
        self.offset = offset

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index) -> Tensor:
        return self.inner(model_fn, x, sigma, cond, uncond, step_index + self.offset)


def _run_stage(sampler, model_forward, x, sigmas, guidance, cond, uncond,
               hooks, is_cancelled, sampler_options, step_offset, total_steps) -> Tensor:
    offset_guidance = _OffsetGuidance(guidance, step_offset) if step_offset else guidance
    return sampler(
        model_forward, x, sigmas, offset_guidance, cond, uncond,
        hooks=hooks, is_cancelled=is_cancelled, sampler_options=sampler_options,
    )
