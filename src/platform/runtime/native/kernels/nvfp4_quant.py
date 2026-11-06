from __future__ import annotations

import logging

import torch

from src.platform.runtime.cuda_allocator import is_driver_oom

from ..io.quant_formats import NVFP4_BLOCK, nvfp4_activation_amax, quantize_nvfp4_dynamic
from .hardware import VENDOR_CUDA, capabilities

logger = logging.getLogger(__name__)

TRITON_DTYPES = (torch.bfloat16, torch.float16, torch.float32)

_disabled_reason: str | None = None
_kernels = None


def nvfp4_quant_disabled_reason() -> str | None:
    return _disabled_reason


def reset_nvfp4_quant_state() -> None:
    global _disabled_reason, _kernels
    _disabled_reason = None
    _kernels = None


def _triton_kernels():
    global _kernels
    if _kernels is None:
        from . import nvfp4_quant_triton

        _kernels = nvfp4_quant_triton
    return _kernels


def _disable(reason: str) -> None:
    global _disabled_reason
    _disabled_reason = reason
    logger.warning(
        "nvfp4 activation quantization: the Triton kernel is off for the rest of this process (%s); "
        "the eager quantizer takes over with identical output", reason,
    )


def _triton_route(x2d: torch.Tensor, tensor_amax: torch.Tensor | None) -> bool:
    if _disabled_reason is not None or x2d.dim() != 2 or x2d.dtype not in TRITON_DTYPES or x2d.numel() == 0:
        return False
    if x2d.shape[1] % NVFP4_BLOCK or x2d.stride(1) != 1:
        return False
    if tensor_amax is not None and tensor_amax.device != x2d.device:
        return False
    caps = capabilities()
    if not caps.triton:
        return False
    device = caps.device(x2d.device)
    return device.vendor == VENDOR_CUDA and device.at_least(8, 9)


def quantize_nvfp4_activation(
    x2d: torch.Tensor, tensor_amax: torch.Tensor | None = None,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    if _triton_route(x2d, tensor_amax):
        try:
            kernels = _triton_kernels()
            x = x2d.detach()
            amax = nvfp4_activation_amax(x) if tensor_amax is None else tensor_amax.detach()
            return kernels.quantize(x, amax)
        except Exception as exc:
            if isinstance(exc, torch.cuda.OutOfMemoryError) or is_driver_oom(exc):
                raise
            _disable(f"{type(exc).__name__}: {exc}"[:300])
    return quantize_nvfp4_dynamic(x2d, tensor_amax=tensor_amax)
