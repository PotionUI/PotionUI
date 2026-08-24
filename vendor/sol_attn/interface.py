# Vendored from ComfyUI_sol-attn_Blackwell —
# https://github.com/KingGore/ComfyUI_sol-attn_Blackwell
# Source file: sol_attn/interface.py at commit
# a8a9584e1ed700f2ce3b7569048cab0071bbf58a.
# License: Apache-2.0 (see LICENSE in this directory).
# Local modifications: the SM120 fast path imports `.triton_ref.fwd`
# relatively (upstream's `from .triton_ref.fwd import ...` already was, and is
# unchanged) — nothing else. The `cutlass`/`cuda.bindings` imports that the
# SM90/SM100 kernels need were ALREADY function-local upstream (`_stream`,
# `_compile_sm90`, `_compile_sm100`) and are left that way deliberately: it is
# what lets a machine with neither package import this module and still reach
# the SM120 path. The `.common`, `.sm90` and `.sm100` modules those three
# functions import are NOT vendored (they are the CuTe DSL kernels, useless
# without the DSL), so on SM90/SM100 this module raises ImportError — which
# the caller in `src/platform/runtime/native/sol_attn.py` treats as "backend
# unavailable" and falls back from, exactly like a missing triton.

"""Public Sol-Attn interface.

Execution backends by architecture:
  SM90  (H100)         — CuTe DSL warp-level MMA kernel
  SM100 (B200)         — CuTe DSL tcgen05 kernel
  SM120 (RTX 5090)     — native Triton JIT kernel (triton_ref)
                         CuTe DSL binaries cannot run on SM120 hardware,
                         and the old SM120→SM100 workaround just caused
                         Triton to compile SM100 ISA that also fails at
                         runtime.  Triton compiles directly for SM120.
"""

from __future__ import annotations

import torch

from .preprocess import BLOCK_SIZE, prepare


_compiled = {}


def _validate(
    q,
    k,
    v,
    kv_splits,
    thresh_type,
    sink_tokens=0,
    sink_start=None,
):
    if q.ndim != 4 or q.shape != k.shape or q.shape != v.shape:
        raise ValueError("q, k, and v must share shape [B, T, H, 128]")
    if q.shape[1] == 0 or q.shape[3] != 128:
        raise ValueError("Sol-Attn requires T > 0 and head dimension 128")
    if any(x.dtype != torch.bfloat16 for x in (q, k, v)):
        raise TypeError("q, k, and v must use torch.bfloat16")
    if q.device.type != "cuda" or k.device != q.device or v.device != q.device:
        raise ValueError("q, k, and v must be on the same CUDA device")
    if not (q.is_contiguous() and k.is_contiguous() and v.is_contiguous()):
        raise ValueError("q, k, and v must be contiguous BTHD tensors")
    if kv_splits not in (1, 2, 4):
        raise ValueError("kv_splits must be 1, 2, or 4")
    if thresh_type not in ("diag", "exact"):
        raise ValueError("thresh_type must be 'diag' or 'exact'")
    if not isinstance(sink_tokens, int):
        raise TypeError("sink_tokens must be an integer")
    if not 0 <= sink_tokens <= q.shape[1]:
        raise ValueError("sink_tokens must be in [0, T]")
    if sink_start is not None:
        if not isinstance(sink_start, int):
            raise TypeError("sink_start must be an integer or None")
        if not 0 <= sink_start <= q.shape[1]:
            raise ValueError("sink_start must be in [0, T]")
        if sink_start + sink_tokens > q.shape[1]:
            raise ValueError("sink_start + sink_tokens must be <= T")

    arch = torch.cuda.get_device_capability(q.device)
    # SM120 is handled separately via the Triton path; remap so the shared
    # validation logic below still accepts it.
    if arch == (12, 0):
        arch = (9, 0)
    if arch not in ((9, 0), (10, 0)):
        raise RuntimeError(
            f"Sol-Attn supports H100 (SM90), B200 (SM100), RTX 5090 (SM120 via Triton); "
            f"got SM{arch[0]*10+arch[1]}"
        )
    if arch == (10, 0) and kv_splits != 1:
        raise ValueError("kv_splits=2/4 is currently available on SM90 only")
    route_groups = ((q.shape[1] + 63) // 64 + 63) // 64
    if kv_splits > route_groups:
        raise ValueError("each KV split must contain at least one N64 route group")
    return arch


def _stream(device):
    # Lazy import: only needed for SM90/SM100 CuTe DSL path
    import cuda.bindings.driver as cuda_driver
    return cuda_driver.CUstream(torch.cuda.current_stream(device).cuda_stream)


def _sink_block_range(tokens, sink_start, sink_tokens):
    blocks = (tokens + BLOCK_SIZE - 1) // BLOCK_SIZE
    if not sink_tokens:
        return blocks, blocks
    start = tokens - sink_tokens if sink_start is None else sink_start
    return (
        start // BLOCK_SIZE,
        (start + sink_tokens + BLOCK_SIZE - 1) // BLOCK_SIZE,
    )


def _compile_sm90(key, tensors, scale, tokens, kv_splits, sink_range, stream):
    import cutlass.cute as cute
    from .common import to_cute_tensor
    from .sm90 import make_kernel

    operator = make_kernel(tokens, kv_splits)
    args = [to_cute_tensor(x) for x in tensors]
    compiled = cute.compile(operator, *args, scale, sink_range, stream=stream)
    _compiled[key] = compiled
    return compiled, args


def _compile_sm100(key, tensors, scale, sink_start_block, sink_end_block, stream):
    import cutlass.cute as cute
    from .common import to_cute_tensor
    from .sm100 import forward

    args = [to_cute_tensor(x) for x in tensors]
    compiled = cute.compile(
        forward, *args, scale, sink_start_block, sink_end_block, stream=stream
    )
    _compiled[key] = compiled
    return compiled, args


def sol_attn(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    scale: float | None = None,
    tau: float = 1.0,
    thresh_type: str = "diag",
    kv_splits: int = 1,
    sink_tokens: int = 0,
    sink_start: int | None = None,
) -> torch.Tensor:
    """Compute noncausal Sol-Attn for contiguous BF16 BTHD tensors.

    SM120 (RTX 5090) is served by the pure-Triton path in triton_ref.fwd
    which Triton JIT-compiles directly for the actual SM120 hardware.
    SM90/SM100 use the CuTe DSL kernels as before.
    """
    # ------------------------------------------------------------------
    # SM120 fast-path: bypass CuTe DSL and use native Triton kernel.
    # Do this BEFORE _validate so we never touch the cuda.bindings path.
    # ------------------------------------------------------------------
    raw_arch = torch.cuda.get_device_capability(q.device)
    if raw_arch == (12, 0):
        from .triton_ref.fwd import sol_attn as _triton_sol_attn
        # triton_ref supports kv_splits=1; for kv_splits>1 we silently
        # fall back to 1 — the correctness is unaffected, only potential
        # perf on very long sequences is slightly lower.
        return _triton_sol_attn(q, k, v, scale=scale, tau=tau)

    # ------------------------------------------------------------------
    # SM90 / SM100: original CuTe DSL path
    # ------------------------------------------------------------------
    arch = _validate(q, k, v, kv_splits, thresh_type, sink_tokens, sink_start)
    scale = q.shape[-1] ** -0.5 if scale is None else float(scale)
    tau = float(tau)
    batch, tokens, heads, _ = q.shape

    with torch.cuda.device(q.device):
        kc, vc, threshold = prepare(
            q, k, v, scale=scale, tau=tau, thresh_type=thresh_type,
        )
        output = torch.empty_like(v)
        lse = torch.empty(
            (batch, tokens, heads), device=q.device, dtype=torch.float32,
        )
        stream = _stream(q.device)
        key = (q.device.index, arch, batch, tokens, heads, kv_splits)

        if arch == (9, 0):
            if sink_tokens:
                sink_start_block, sink_end_block = _sink_block_range(
                    tokens, sink_start, sink_tokens,
                )
                sink_range = sink_start_block | (sink_end_block << 16)
            else:
                sink_range = 0
            tensors = [q, k, v, output, kc, vc, threshold, lse]
            if kv_splits > 1:
                tensors.extend([
                    torch.empty(
                        (batch, tokens, kv_splits * heads, 128),
                        device=q.device, dtype=torch.bfloat16,
                    ),
                    torch.empty(
                        (batch, tokens, kv_splits * heads),
                        device=q.device, dtype=torch.float32,
                    ),
                ])
            compiled = _compiled.get(key)
            if compiled is None:
                compiled, args = _compile_sm90(
                    key, tensors, scale, tokens, kv_splits, sink_range, stream,
                )
            else:
                from .common import to_cute_tensor
                args = [to_cute_tensor(x) for x in tensors]
            compiled(*args, scale, sink_range, stream=stream)
        else:
            sink_start_block, sink_end_block = _sink_block_range(
                tokens, sink_start, sink_tokens,
            )
            tensors = [q, k, v, output, kc, vc, threshold, lse]
            compiled = _compiled.get(key)
            if compiled is None:
                compiled, args = _compile_sm100(
                    key, tensors, scale, sink_start_block, sink_end_block, stream,
                )
            else:
                from .common import to_cute_tensor
                args = [to_cute_tensor(x) for x in tensors]
            compiled(*args, scale, sink_start_block, sink_end_block, stream=stream)
    return output


__all__ = ["sol_attn"]
