"""Storage / compute dtype selection from checkpoint dtype and hardware.

Kept deliberately small: given the dtype the weights are stored in plus the
device's capability, decide what to keep on disk-in-memory (``storage_dtype``)
and what to run matmuls in (``compute_dtype``).

Rules:
  * fp8 storage stays fp8 in memory (it is dequantised per-forward by the ops
    layer), compute runs in bf16/fp16.
  * bf16 is the compute dtype on Ampere+ (SM80+); older cards fall back to fp16.
  * fp32 checkpoints downcast to the compute dtype (no reason to run DiTs fp32).
"""

from __future__ import annotations

import logging

import torch

logger = logging.getLogger(__name__)

_FP8_DTYPES = {torch.float8_e4m3fn, torch.float8_e5m2}

# Per-tensor quant scale sidecars — legitimately f32 alongside fp8/bf16 weights,
# so they must NOT count toward the mixed-precision test.
_SCALE_SUFFIXES = (".weight_scale", ".input_scale", ".scale_weight", ".weight_scale_2")


def is_mixed_precision(sd: dict[str, torch.Tensor]) -> bool:
    """True when the checkpoint's real floating weights span more than one dtype.

    Krea-2's bf16 checkpoint is mixed: bf16 block Linears + f32 norm/peripheral
    weights. Majority-dtype ops selection would pick a no-cast namespace and a
    plain ``F.linear`` would then crash on bf16 activations x f32 weights, so the
    loader forces ``manual_cast`` when this returns True. Quant scale sidecars
    (``*.weight_scale`` etc.) and the ``scaled_fp8`` marker are excluded — those
    are handled by the fp8 path, not this one.
    """
    dtypes: set[torch.dtype] = set()
    for k, v in sd.items():
        if not v.is_floating_point():
            continue
        if k == "scaled_fp8" or k.endswith(_SCALE_SUFFIXES):
            continue
        dtypes.add(v.dtype)
    return len(dtypes) > 1


def _supports_bf16(device: torch.device) -> bool:
    if device.type != "cuda":
        # CPU bf16 works for correctness (slow); treat as supported.
        return True
    if not torch.cuda.is_available():
        return True
    major, _ = torch.cuda.get_device_capability(device)
    return major >= 8  # Ampere and newer


def pick_dtypes(
    sd_dtype: torch.dtype | None,
    device: str | torch.device,
    vram_gb: float | None = None,
) -> tuple[torch.dtype, torch.dtype]:
    """Return ``(storage_dtype, compute_dtype)``.

    ``sd_dtype`` is the majority floating dtype of the checkpoint
    (``state_dict_utils.weight_dtype``). ``vram_gb`` is accepted for future
    tier-aware decisions; unused in v1.
    """
    device = torch.device(device)
    compute_dtype = torch.bfloat16 if _supports_bf16(device) else torch.float16

    if sd_dtype in _FP8_DTYPES:
        storage_dtype = sd_dtype  # keep fp8, dequant on forward
    elif sd_dtype in (torch.bfloat16, torch.float16):
        storage_dtype = sd_dtype
    else:
        # fp32 or unknown -> store in compute dtype.
        storage_dtype = compute_dtype

    logger.debug(
        "pick_dtypes(sd=%s, device=%s) -> storage=%s compute=%s",
        sd_dtype, device, storage_dtype, compute_dtype,
    )
    return storage_dtype, compute_dtype
