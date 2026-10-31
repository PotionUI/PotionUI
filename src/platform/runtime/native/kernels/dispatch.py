from __future__ import annotations

import logging

import torch
import torch.nn.functional as F

from src.platform.settings.runtime_flags import coerce_runtime_flag, runtime_flag, runtime_flag_values

from .hardware import FLASH, SAGE, SAGE2, SAGE3, SDPA, SPARGE, capabilities, refresh_capabilities

logger = logging.getLogger(__name__)

Tensor = torch.Tensor

BACKEND_PRIORITY: list[str] = [SAGE3, SAGE2, SAGE, FLASH, SDPA]

PIN_ONLY_BACKENDS: frozenset[str] = frozenset({SPARGE})

_SPARGE_MIN_SEQ_LEN = 128
_SPARGE_VALID_HEAD_DIMS: frozenset[int] = frozenset({64, 128})

_MAX_HEAD_DIM: dict[str, int] = {SAGE3: 128, SAGE2: 128, SAGE: 128, FLASH: 256}
_NO_CUSTOM_SCALE: frozenset[str] = frozenset({SAGE3})

_SAGE_V_SCALE = 256.0
_SAGE_V_SAFE_MAX = 1024.0
_SAGE_SYNC_ROWS = 32768

_MASK_ALIGN = 16
_MASKED_GROUPED_EXPAND_MIN_SCORES = 1 << 26

_warned_unavailable: set[str] = set()
_noted_dispatch: set[str] = set()
_grouped_capability: dict[str, bool] = {}


def set_backend_override(name: str | None) -> None:
    runtime_flag_values()["native_attention_backend"] = coerce_runtime_flag("native_attention_backend", name)


def get_backend_override() -> str | None:
    return runtime_flag("native_attention_backend") or None


def _auto_selectable(available: frozenset[str]) -> list[str]:
    return [b for b in BACKEND_PRIORITY if b in available]


def reset_backend_cache() -> None:
    refresh_capabilities()
    _warned_unavailable.clear()
    _noted_dispatch.clear()
    _grouped_capability.clear()


def _probe_grouped_sdpa() -> bool:
    q = torch.zeros(1, 2, 1, 8)
    kv = torch.zeros(1, 1, 1, 8)
    try:
        F.scaled_dot_product_attention(q, kv, kv, enable_gqa=True)
    except TypeError:
        return False
    return True


def supports_grouped_kv(backend: str) -> bool:
    cached = _grouped_capability.get(backend)
    if cached is None:
        cached = _probe_grouped_sdpa() if backend == SDPA else False
        _grouped_capability[backend] = cached
    return cached


def available_backends(device_index: int | None = None) -> list[str]:
    return _auto_selectable(capabilities().device(device_index).attention_backends)


def known_backends() -> frozenset[str]:
    return frozenset(BACKEND_PRIORITY) | PIN_ONLY_BACKENDS


def get_attention_backend(override: str | None = None, device_index: int | None = None) -> str:
    full_avail = capabilities().device(device_index).attention_backends
    auto_avail = _auto_selectable(full_avail)
    requested = override or runtime_flag("native_attention_backend") or None

    if requested is None:
        return auto_avail[0]

    requested = requested.strip().lower()
    if requested in full_avail:
        return requested

    if requested not in _warned_unavailable:
        _warned_unavailable.add(requested)
        known = list(BACKEND_PRIORITY) + sorted(PIN_ONLY_BACKENDS)
        if requested not in known:
            logger.warning(
                "unknown attention backend %r requested; using %r. Known: %s",
                requested, auto_avail[0], known,
            )
        else:
            logger.warning(
                "attention backend %r requested but unavailable; falling back to %r",
                requested, auto_avail[0],
            )
    return auto_avail[0]


def _note_dispatch(key: str, fmt: str, *args) -> None:
    if key in _noted_dispatch:
        logger.debug(fmt, *args)
        return
    _noted_dispatch.add(key)
    logger.info(fmt, *args)


def _aligned_mask(mask: Tensor | None) -> Tensor | None:
    if mask is None or mask.ndim < 2:
        return mask
    length = mask.shape[-1]
    pad = (-length) % _MASK_ALIGN
    if pad == 0 and mask.stride(-1) == 1:
        return mask
    buf = torch.empty(*mask.shape[:-1], length + pad, dtype=mask.dtype, device=mask.device)
    buf[..., :length] = mask
    return buf[..., :length]


def _masked_scores(q: Tensor, k: Tensor) -> int:
    return q.shape[0] * q.shape[1] * q.shape[-2] * k.shape[-2]


def _with_causal(mask: Tensor, q: Tensor, k: Tensor) -> Tensor:
    keep = torch.ones(q.shape[-2], k.shape[-2], dtype=torch.bool, device=q.device).tril()
    if mask.dtype == torch.bool:
        return mask & keep
    return torch.where(keep, mask, mask.new_full((), float("-inf")))


def _scale_kwargs(scale: float | None) -> dict[str, float]:
    return {} if scale is None else {"scale": scale}


def _sdpa(
    q: Tensor, k: Tensor, v: Tensor, mask: Tensor | None, grouped: bool = False, causal: bool = False,
    scale: float | None = None,
) -> Tensor:
    extra = _scale_kwargs(scale)
    if mask is not None:
        if causal:
            mask = _with_causal(mask, q, k)
            causal = False
        mask = _aligned_mask(mask)
        if grouped and _masked_scores(q, k) > _MASKED_GROUPED_EXPAND_MIN_SCORES:
            repeat = q.shape[1] // k.shape[1]
            if repeat > 1:
                k = k.repeat_interleave(repeat, dim=1)
                v = v.repeat_interleave(repeat, dim=1)
            grouped = False
    if grouped:
        return F.scaled_dot_product_attention(
            q, k, v, attn_mask=mask, dropout_p=0.0, is_causal=causal, enable_gqa=True, **extra,
        )
    return F.scaled_dot_product_attention(q, k, v, attn_mask=mask, dropout_p=0.0, is_causal=causal, **extra)


def _flash(q: Tensor, k: Tensor, v: Tensor, causal: bool = False, scale: float | None = None) -> Tensor:
    from flash_attn import flash_attn_func

    extra = {} if scale is None else {"softmax_scale": scale}
    out = flash_attn_func(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2), causal=causal, **extra)
    return out.transpose(1, 2)


def _absmax(t: Tensor) -> Tensor:
    return torch.maximum(t.amax(), t.amin().neg())


def _sage(q: Tensor, k: Tensor, v: Tensor, causal: bool = False, scale: float | None = None) -> Tensor:
    from sageattention import sageattn

    extra = {} if scale is None else {"sm_scale": scale}

    if v.shape[2] >= _SAGE_SYNC_ROWS:
        if float(_absmax(v)) > _SAGE_V_SAFE_MAX:
            out = sageattn(q, k, v / _SAGE_V_SCALE, tensor_layout="HND", is_causal=causal, **extra)
            out = out * _SAGE_V_SCALE
        else:
            out = sageattn(q, k, v, tensor_layout="HND", is_causal=causal, **extra)
        return torch.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    scale = torch.where(
        _absmax(v) > _SAGE_V_SAFE_MAX,
        v.new_tensor(_SAGE_V_SCALE),
        v.new_tensor(1.0),
    )
    out = sageattn(q, k, v / scale, tensor_layout="HND", is_causal=causal, **extra)
    out = out * scale
    return torch.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def _sage3(q: Tensor, k: Tensor, v: Tensor, causal: bool = False) -> Tensor:
    from sageattn3 import sageattn3_blackwell

    return sageattn3_blackwell(q, k, v, is_causal=causal)


def _sparge_shape_ok(q: Tensor) -> bool:
    return q.shape[2] >= _SPARGE_MIN_SEQ_LEN and q.shape[3] in _SPARGE_VALID_HEAD_DIMS


def _sparge(q: Tensor, k: Tensor, v: Tensor, causal: bool = False, scale: float | None = None) -> Tensor:
    from spas_sage_attn import spas_sage2_attn_meansim_topk_cuda

    extra = _scale_kwargs(scale)
    return spas_sage2_attn_meansim_topk_cuda(
        q, k, v, is_causal=causal, tensor_layout="HND", topk=0.5, output_dtype=q.dtype, **extra,
    )


def _resolve_dispatch(
    q: Tensor, k: Tensor, mask: Tensor | None, causal: bool, backend: str | None, scale: float | None = None
) -> str:
    device_index = q.device.index if q.is_cuda else None
    chosen = get_attention_backend(backend, device_index)

    accelerated = chosen in (SAGE3, SAGE2, SAGE, FLASH, SPARGE)
    if accelerated:
        if mask is not None:
            reason = "mask"
        elif q.dtype not in (torch.float16, torch.bfloat16):
            reason = "dtype"
        elif not q.is_cuda:
            reason = "device"
        elif chosen == SPARGE and not _sparge_shape_ok(q):
            reason = "shape"
        elif q.shape[-1] > _MAX_HEAD_DIM.get(chosen, q.shape[-1]):
            reason = "head_dim"
        elif scale is not None and chosen in _NO_CUSTOM_SCALE:
            reason = "scale"
        elif causal and q.shape[2] != k.shape[2]:
            reason = "causal"
        else:
            reason = None
    else:
        reason = None

    if reason is not None:
        _note_dispatch(f"{chosen}->sdpa:{reason}",
                       "attention: %s falls back to sdpa (%s-constrained call, shape %s)",
                       chosen, reason, tuple(q.shape))
        return SDPA
    if accelerated:
        _note_dispatch(chosen, "attention: %s kernels in use (shape %s)", chosen, tuple(q.shape))
    return chosen


def _run(
    chosen: str, q: Tensor, k: Tensor, v: Tensor, mask: Tensor | None, grouped: bool = False, causal: bool = False,
    scale: float | None = None,
) -> Tensor:
    extra = _scale_kwargs(scale)
    if chosen == FLASH:
        return _flash(q, k, v, causal, **extra)
    if chosen == SAGE3:
        return _sage3(q, k, v, causal)
    if chosen == SPARGE:
        return _sparge(q, k, v, causal, **extra)
    if chosen in (SAGE2, SAGE):
        return _sage(q, k, v, causal, **extra)
    return _sdpa(q, k, v, mask, grouped=grouped, causal=causal, **extra)


def _grouped(
    q: Tensor, k: Tensor, v: Tensor, mask: Tensor | None, causal: bool, backend: str | None,
    scale: float | None = None,
) -> Tensor:
    q_heads, kv_heads = q.shape[1], k.shape[1]
    if kv_heads == 0 or q_heads % kv_heads:
        raise ValueError(f"q heads={q_heads} is not a multiple of kv heads={kv_heads}")

    chosen = _resolve_dispatch(q, k, mask, causal, backend, scale)
    if supports_grouped_kv(chosen):
        return _run(chosen, q, k, v, mask, grouped=True, causal=causal, **_scale_kwargs(scale))

    repeat = q_heads // kv_heads
    return _run(
        chosen, q, k.repeat_interleave(repeat, dim=1), v.repeat_interleave(repeat, dim=1), mask, causal=causal, **_scale_kwargs(scale)
    )


def _empty_attention(q: Tensor, k: Tensor, v: Tensor) -> Tensor:
    if q.shape[-2] == 0:
        return q.new_empty(*q.shape[:-1], v.shape[-1])
    raise ValueError(
        f"attention over an empty key/value sequence is undefined: {q.shape[-2]} quer(ies) against 0 keys "
        f"(q {tuple(q.shape)}, k {tuple(k.shape)}, v {tuple(v.shape)}); the caller built a zero-length context"
    )


def attention(
    q: Tensor,
    k: Tensor,
    v: Tensor,
    *,
    heads: int | None = None,
    kvheads: int | None = None,
    mask: Tensor | None = None,
    causal: bool = False,
    backend: str | None = None,
    scale: float | None = None,
) -> Tensor:
    if q.shape[-2] == 0 or k.shape[-2] == 0:
        return _empty_attention(q, k, v)
    if q.ndim == 4:
        if heads is not None and heads != q.shape[1]:
            raise ValueError(f"heads={heads} disagrees with q.shape[1]={q.shape[1]}")
        if kvheads is not None and kvheads != k.shape[1]:
            raise ValueError(f"kvheads={kvheads} disagrees with k.shape[1]={k.shape[1]}")
        if q.shape[1] != k.shape[1]:
            return _grouped(q, k, v, mask, causal, backend, scale)

    return _run(_resolve_dispatch(q, k, mask, causal, backend, scale), q, k, v, mask, causal=causal, **_scale_kwargs(scale))
