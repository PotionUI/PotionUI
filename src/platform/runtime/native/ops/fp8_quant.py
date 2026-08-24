"""On-the-fly fp8 quantisation of a bf16/fp16 checkpoint at load time.

When a bf16 DiT (e.g. Krea-2 at ~24.5GB) will not fit resident but WOULD as fp8
(~12.5GB), the loader can quantise it before it ever reaches the GPU. Placement
then prefers a resident-fp8 DiT over a streamed-bf16 one — fp8 resident is both
faster and higher-throughput than streaming weights over PCIe (at a small,
grid-level precision cost noted in the tier table).

The output is exactly the per-tensor scaled-e4m3 format the runtime already
consumes (``vendor.gpl.comfyui.ops.Fp8ScaledLinear``): a quantised layer's ``weight``
becomes ``float8_e4m3fn`` and gains a ``<layer>.weight_scale`` scalar sidecar such
that ``weight.to(compute) * weight_scale`` recovers the original. No new runtime
path is introduced — quantise-at-load simply feeds the validated fp8
dequant-on-forward path, and ``detect_quant_format`` routes the result to
``fp8_ops`` because of the emitted ``*.weight_scale`` keys.

**Quality guard.** Only the big 2D Linear/matmul weights are quantised. Norms,
embeddings, modulation projections and every bias keep their original dtype —
matching which tensors ship as fp8 in real fp8 checkpoints
(``flux-2-klein-9b-fp8``) and keeping the precision-sensitive layers intact.
"""

from __future__ import annotations

import logging

import torch

logger = logging.getLogger(__name__)

# A bad fp8_quantize policy string doesn't change mid-process, so warn about it
# only once instead of on every single model load that consults the policy.
_warned_bad_fp8_policy = False

FP8_E4M3 = torch.float8_e4m3fn
# Largest finite magnitude representable in e4m3 (torch.finfo(...).max == 448.0).
_E4M3_MAX = 448.0

# Substrings marking a 2D weight that must stay at its original dtype. Norm
# weights are 1D (already skipped by the rank test) but listed for clarity; the
# load-bearing exclusions are embeddings and modulation projections.
_KEEP_ORIGINAL_TOKENS = ("norm", "embed", "emb.", "modulation", "mod_")

_WEIGHT_SUFFIX = ".weight"
_SCALE_SUFFIX = ".weight_scale"


def _should_quantize(key: str, tensor: torch.Tensor, *, min_numel: int) -> bool:
    """A big 2D Linear/matmul weight that is safe to quantise to fp8."""
    if not key.endswith(_WEIGHT_SUFFIX):
        return False
    if tensor.ndim != 2 or not tensor.is_floating_point():
        return False
    if tensor.dtype in (torch.float8_e4m3fn, torch.float8_e5m2):
        return False  # already fp8
    if tensor.numel() < min_numel:
        return False
    kl = key.lower()
    return not any(tok in kl for tok in _KEEP_ORIGINAL_TOKENS)


def quantize_tensor_to_fp8_scaled(w: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Per-tensor symmetric e4m3 quantise.

    Returns ``(fp8_weight, scale)`` where ``scale`` is a f32 0-dim tensor and
    ``fp8_weight.to(f32) * scale`` approximates ``w``. The scale maps the tensor's
    largest magnitude onto the e4m3 max (amax/448); an all-zero tensor is guarded
    to a tiny positive scale so the reconstruction stays exactly zero.
    """
    w32 = w.detach().to(torch.float32)
    amax = w32.abs().amax()
    scale = (amax / _E4M3_MAX).clamp_min(torch.finfo(torch.float32).tiny)
    q = (w32 / scale).clamp(-_E4M3_MAX, _E4M3_MAX).to(FP8_E4M3)
    return q, scale.reshape(())


def quantize_state_dict_to_fp8(
    sd: dict[str, torch.Tensor],
    *,
    min_numel: int = 0,
) -> tuple[dict[str, torch.Tensor], int]:
    """Quantise the big Linear weights of ``sd`` to scaled e4m3 in a new dict.

    Each quantised ``<layer>.weight`` is replaced by its fp8 tensor and gains a
    ``<layer>.weight_scale`` sidecar; every other tensor is passed through
    unchanged. The result is a *mixed* checkpoint that ``fp8_ops`` loads
    transparently — a layer with a ``weight_scale`` dequantises on forward, the
    rest manual-cast. Returns ``(new_sd, num_quantized)``.

    ``min_numel`` protects small Linears (projections, time embedders) by leaving
    any weight below that element count in its original dtype.
    """
    out: dict[str, torch.Tensor] = {}
    n = 0
    for k, v in sd.items():
        if _should_quantize(k, v, min_numel=min_numel):
            q, scale = quantize_tensor_to_fp8_scaled(v)
            out[k] = q
            out[k[: -len(_WEIGHT_SUFFIX)] + _SCALE_SUFFIX] = scale
            n += 1
        else:
            out[k] = v
    logger.info("fp8 quantise-at-load: %d/%d weights -> scaled e4m3", n, len(sd))
    return out, n


def should_quantize_fp8(
    policy: str,
    *,
    quant_format: str | None,
    sd_dtype: "torch.dtype | None",
    bf16_gb: float,
    fp8_gb: float,
    vram_gb: float | None,
    headroom_gb: float = 2.0,
) -> bool:
    """Decide whether to fp8-quantise a DiT at load, per the loader policy.

    ``off`` never quantises; ``force`` quantises any not-already-quantised
    bf16/fp16/fp32 checkpoint; ``auto`` quantises only when the bf16 model does NOT
    fit the budget resident but the fp8 model DOES — the window where fp8-resident
    beats bf16-streaming. Already-quantised (fp8/nvfp4) or non-quantisable-dtype
    checkpoints are never touched. ``auto`` with no known ``vram_gb`` keeps full
    precision (assume ample VRAM). An unknown policy is treated as ``auto``.

    ``headroom_gb`` is an ABSOLUTE working reserve (sampling activations), not a
    fraction of ``vram_gb``: a fractional reserve (e.g. ``vram_gb * 0.9``) shaves
    3GB off a 30GB card, and stacked on the callers' own discounts it would
    auto-quantise a 24.5GB model that fits fine.
    """
    if policy == "off":
        return False
    if quant_format is not None:
        return False  # already fp8/nvfp4
    if sd_dtype not in (torch.bfloat16, torch.float16, torch.float32):
        return False
    if policy == "force":
        return True
    if policy != "auto":
        global _warned_bad_fp8_policy
        if not _warned_bad_fp8_policy:
            _warned_bad_fp8_policy = True
            logger.warning("fp8 quant: unknown policy %r; treating as 'auto'", policy)
    if vram_gb is None:
        return False
    budget = max(0.0, vram_gb - headroom_gb)
    return bf16_gb > budget and fp8_gb <= budget


def estimate_fp8_gb(sd: dict[str, torch.Tensor], *, min_numel: int = 0) -> float:
    """Estimated resident GB of ``sd`` after :func:`quantize_state_dict_to_fp8`.

    Cheap enough to drive a placement decision without actually quantising: a
    quantised 2D weight goes from ``element_size`` bytes/elem to 1 (e4m3) plus a
    negligible per-tensor scalar; everything else is unchanged.
    """
    total = 0
    for k, v in sd.items():
        if _should_quantize(k, v, min_numel=min_numel):
            total += v.numel()  # 1 byte/elem in e4m3
        else:
            total += v.numel() * v.element_size()
    return total / (1024 ** 3)
