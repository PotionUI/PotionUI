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

from src.platform.runtime.native.io.quant_formats import FP8_DTYPES
from src.platform.runtime.native.kernels import capabilities

logger = logging.getLogger(__name__)

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
        return True
    snapshot = capabilities()
    if not snapshot.devices:
        return True
    return snapshot.device(device).at_least(8)


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

    if sd_dtype in FP8_DTYPES:
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


_DTYPES_BY_NAME = {
    "bfloat16": torch.bfloat16, "bf16": torch.bfloat16,
    "float16": torch.float16, "fp16": torch.float16, "half": torch.float16,
    "float32": torch.float32, "fp32": torch.float32,
}


def parse_dtype(value: str | torch.dtype | None) -> torch.dtype | None:
    if value is None or isinstance(value, torch.dtype):
        return value
    name = str(value).strip().lower().removeprefix("torch.")
    if name not in _DTYPES_BY_NAME:
        raise ValueError(f"unknown dtype {value!r}; expected one of {sorted(_DTYPES_BY_NAME)}")
    return _DTYPES_BY_NAME[name]


def align_half_storage(
    sd: dict[str, torch.Tensor],
    compute_dtype: torch.dtype,
    reference_dtype: torch.dtype | None,
) -> int:
    if reference_dtype is not torch.bfloat16 or compute_dtype is not torch.bfloat16:
        return 0
    half = {v.dtype for v in sd.values() if v.dtype in (torch.float16, torch.bfloat16)}
    if half != {torch.float16}:
        return 0
    cast = 0
    for key in list(sd):
        tensor = sd[key]
        if tensor.dtype is torch.float16 and not key.endswith(_SCALE_SUFFIXES):
            sd[key] = tensor.to(compute_dtype)
            cast += 1
    if cast:
        logger.info(
            "fp16 storage in a %s-reference checkpoint: %d tensor(s) cast to %s at load",
            reference_dtype, cast, compute_dtype,
        )
    return cast


def warn_below_reference(label: str, reference_dtype: torch.dtype | None, compute_dtype: torch.dtype) -> bool:
    if reference_dtype is not torch.bfloat16 or compute_dtype is not torch.float16:
        return False
    logger.warning(
        "%s will run float16 activations but its reference dtype is torch.bfloat16; "
        "the residual stream can overflow to inf/NaN (black output)",
        label,
    )
    return True
