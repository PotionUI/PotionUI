"""FreeInit (arXiv:2312.07537, TianxingWu/FreeInit) for video pipes.

Video diffusion models are trained on i.i.d. Gaussian noise, but at inference
the FULL clip must share temporal coherence from step 1 — there is an
"initialization gap" between the noise distribution seen in training (no
temporal structure) and what a coherent video's noised-forward process
actually looks like (strong low-frequency temporal correlation). Starting
from plain i.i.d. noise under-supplies that low-frequency structure, which
shows up as flicker.

FreeInit closes the gap iteratively, with no retraining: run a full denoise;
take the resulting clean latent; re-noise it back up to the starting noise
level; in 3D frequency space (over the T/H/W axes) keep that re-noised
latent's LOW-frequency band (it now carries real temporal structure from an
actual clean video) and replace its HIGH-frequency band with a fresh i.i.d.
draw (a denoised latent's high frequencies are itself biased/oversmoothed, so
reusing them would compound rather than correct); run another full denoise
from that blend. Each iteration re-pays the full sampling cost, so the paper
explores 3-5 iterations but this module defaults callers toward 1-2.

Re-noising uses the flow-matching interpolation the rest of this codebase
already uses (``x = (1-sigma)*x0 + sigma*eps``, see
``sampling/denoise_loop.py``'s noise-scaling docstring), not FreeInit's
original DDPM forward process — same purpose (walk the clean latent back up
to the model's starting noise level), just in our sampler's own noise
convention so the blended result is a valid ``sigma_max`` init for another
``denoise()`` call.
"""

from __future__ import annotations

from typing import List, Tuple

import torch

from src.pipelines.contracts import PipeConfigSpec

Tensor = torch.Tensor


def freeinit_config_specs() -> List[PipeConfigSpec]:
    """Flat config knobs (matching this codebase's established flat-key style
    for per-feature video-pipe options — apg_eta/slg_scale/riflex/etc. — rather
    than a nested dict) for FreeInit."""
    return [
        PipeConfigSpec(
            "freeinit_iterations", int, 0,
            "FreeInit: extra full denoise passes that re-noise the previous pass's "
            "clean result in 3D frequency space (keep its low frequencies, replace "
            "its high frequencies with fresh noise) to reduce video temporal flicker "
            "from the noise-init gap. 0 = off (byte-identical single pass). Each "
            "iteration re-pays the full sampling cost, so 1-2 is the practical "
            "default even though the paper explores 3-5.",
            required=False, min_value=0, max_value=5,
        ),
        PipeConfigSpec(
            "freeinit_cutoff", float, 0.25,
            "FreeInit: normalized Butterworth HALF-POWER cutoff frequency (paper "
            "default ~0.25) -- at d == cutoff the mask value is exactly 0.5, not a "
            "hard boundary, so even cutoff=1.0 still attenuates (roughly halves) "
            "the cardinal-axis Nyquist bin and attenuates diagonal/high-frequency "
            "bins much further; only cutoff -> 0 keeps solely the DC (mean) "
            "component as 'low' and cutoff -> infinity keeps everything.",
            required=False, min_value=0.01, max_value=1.0,
        ),
        PipeConfigSpec(
            "freeinit_order", int, 4,
            "FreeInit: Butterworth filter order — higher is a steeper cutoff "
            "(closer to a hard low-pass), lower is a gentler roll-off.",
            required=False, min_value=1, max_value=8,
        ),
    ]


def resolve_freeinit(config: dict) -> Tuple[int, float, int]:
    """Read ``freeinit_iterations``/``freeinit_cutoff``/``freeinit_order`` off a
    pipe's config -> ``(iterations, cutoff, order)``. ``iterations <= 0`` is the
    off state callers should treat as "run the single plain pass"."""
    iterations = int(config.get("freeinit_iterations", 0))
    cutoff = float(config.get("freeinit_cutoff", 0.25))
    order = int(config.get("freeinit_order", 4))
    return iterations, cutoff, order


def butterworth_lowpass_mask(
    shape: Tuple[int, int, int],
    cutoff: float,
    order: int,
    *,
    device=None,
    dtype: torch.dtype = torch.float32,
) -> Tensor:
    """A 3D Butterworth low-pass mask over an FFT-shifted-origin frequency grid
    of ``shape`` (T, H, W), values in ``(0, 1]``.

    ``H(d) = 1 / (1 + (d / cutoff) ** (2 * order))`` where ``d`` is the
    Euclidean distance from the zero-frequency (DC) bin, normalized so the
    grid's cardinal Nyquist distance is ``1.0``. ``d == 0`` (DC) always maps to
    ``H == 1`` regardless of ``cutoff``/``order`` — the mean/lowest-frequency
    component is always kept from the low-frequency source. Built directly in
    ``torch.fft.fftn``'s (unshifted) bin order, so it can be multiplied
    straight against an ``fftn`` output with no ``fftshift``/``ifftshift``.
    """
    t, h, w = shape
    # torch.fft.fftfreq gives cycles-per-sample in [-0.5, 0.5) in fftn's own
    # (unshifted) bin ordering -- exactly what we need to multiply an fftn
    # output directly. Normalize each axis by its own Nyquist (0.5) so the
    # combined distance's cardinal-axis Nyquist point is at d == 1.0.
    ft = torch.fft.fftfreq(t, device=device).to(dtype) / 0.5
    fh = torch.fft.fftfreq(h, device=device).to(dtype) / 0.5
    fw = torch.fft.fftfreq(w, device=device).to(dtype) / 0.5
    gt, gh, gw = torch.meshgrid(ft, fh, fw, indexing="ij")
    d = torch.sqrt(gt * gt + gh * gh + gw * gw)
    return 1.0 / (1.0 + (d / cutoff) ** (2 * order))


def freeinit_blend(
    clean_latent: Tensor,
    renoise_noise: Tensor,
    fresh_noise: Tensor,
    *,
    sigma_max: float = 0.98,
    cutoff: float = 0.25,
    order: int = 4,
) -> Tensor:
    """Build the next iteration's init noise from this iteration's clean result.

    ``clean_latent``/``renoise_noise``/``fresh_noise`` all share shape
    ``(..., T, H, W)`` (the last 3 dims are the FFT axes — for a native 5D
    video latent that's ``(B, C, T, H, W)``); the FFT/blend/inverse run over
    exactly those 3 dims, unaffected by whatever batch/channel dims precede
    them.

    1. Re-noise ``clean_latent`` up to ``sigma_max`` via the flow interpolation
       (``x = (1-sigma)*x0 + sigma*eps``, ``eps = renoise_noise``) -- the same
       convention ``denoise()`` uses to mix its own initial noise, so the
       result is a valid ``seed_noise`` for another full ``denoise()`` call at
       ``sigma_max``.
    2. FFT both the re-noised latent and ``fresh_noise`` over ``(T, H, W)``.
    3. Combine: ``low(renoised) + high(fresh)`` via a Butterworth mask
       (:func:`butterworth_lowpass_mask`) — keep the re-noised latent's low
       frequencies (real temporal structure from an actual clean video) and
       the fresh draw's high frequencies (a denoised latent's own high
       frequencies are the biased/oversmoothed part; reusing them would
       compound rather than correct).
    4. Inverse FFT and take the real part (the blend of two real-valued
       signals via a real-valued mask is mathematically real; ``.real`` only
       discards float rounding noise in the imaginary part).

    Computed in float32 regardless of the input dtype (FFT numerics), cast
    back to ``clean_latent``'s dtype on return.

    ``sigma_max`` MUST be strictly below ``1.0`` — this is the fix for a real
    degeneracy in the flow-matching adaptation of the DDPM original: at
    ``sigma_max == 1.0`` the interpolation ``x = (1-sigma)*x0 + sigma*eps``
    collapses to ``x == eps``, i.e. ``renoised`` carries ZERO information from
    ``clean_latent`` and the whole blend (any cutoff/order) becomes a function
    of ``renoise_noise``/``fresh_noise`` alone — the extra pass would be an
    unrelated regeneration, not a refinement. The DDPM paper avoids this
    because its forward process's terminal signal coefficient
    (``sqrt(alpha_bar_T)``) is small but never exactly zero. The flow default
    here (``0.98``) mirrors that: at ``sigma_max=0.98`` the ``(1-sigma_max)``
    signal coefficient is only ``0.02``, but that's enough — natural
    video/latent signals concentrate their energy in LOW frequencies (a
    ``1/f``-like spectrum) while i.i.d. noise has a FLAT spectrum, so the low-
    frequency BAND of ``renoised`` still has a materially higher clean-signal
    fraction than the raw ``0.02`` coefficient suggests, while the overall
    variance stays close to ``Var(eps)`` (``0.02² * Var(clean) + 0.98² *
    Var(eps) ≈ Var(eps)`` for comparable-scale ``clean``/``eps``), keeping the
    re-noised init statistically close to the ``N(0,1)`` the model expects.
    """
    if not (0.0 <= sigma_max < 1.0):
        raise ValueError(
            f"sigma_max must be in [0, 1) -- strictly below 1.0, since at "
            f"sigma_max==1.0 the flow interpolation collapses to pure noise "
            f"and the blend loses all dependence on clean_latent (see this "
            f"function's docstring); got {sigma_max}"
        )

    in_dtype = clean_latent.dtype
    clean32 = clean_latent.float()
    renoised = (1.0 - sigma_max) * clean32 + sigma_max * renoise_noise.float()
    fresh32 = fresh_noise.float()

    mask = butterworth_lowpass_mask(
        tuple(renoised.shape[-3:]), cutoff, order, device=renoised.device, dtype=torch.float32,
    )

    f_renoised = torch.fft.fftn(renoised, dim=(-3, -2, -1))
    f_fresh = torch.fft.fftn(fresh32, dim=(-3, -2, -1))
    f_blend = f_renoised * mask + f_fresh * (1.0 - mask)
    blended = torch.fft.ifftn(f_blend, dim=(-3, -2, -1)).real

    return blended.to(in_dtype)
