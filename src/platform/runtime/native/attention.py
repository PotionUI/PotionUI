"""Attention backend dispatch for native arch modules.

A single ``attention(q, k, v)`` entry point that routes to the fastest attention
kernel available on the current machine, with a uniform tensor contract so every
arch module (Flux, Qwen-Image, Wan, ...) can share it.

Locally written, no upstream lineage: the probe/priority/pin machinery is this
module's own, and each ``_<backend>`` function is a two-to-four-line adapter onto
the kernel package's own documented public entry point
(``torch.nn.functional.scaled_dot_product_attention``,
``flash_attn.flash_attn_func``, ``sageattention.sageattn``,
``sageattn3.sageattn3_blackwell``,
``spas_sage_attn.spas_sage2_attn_meansim_topk_cuda``) — the call signatures are
those packages' public API, not transliterated code. The one piece of
non-mechanical logic, ``_sage``'s conditional V rescale, comes from production
failures observed here and is documented at its definition.

Tensor contract
---------------
``q``, ``k``, ``v`` are **head-split**: shape ``(B, H, L, D)`` (batch, heads,
sequence, head-dim) — exactly the layout ``torch.nn.functional.
scaled_dot_product_attention`` consumes and the layout the Flux DiT already
produces after RoPE. The return is also ``(B, H, L, D)`` (SDPA's output, before
the caller merges heads). ``heads`` is optional metadata (inferred from
``q.shape[1]`` when omitted); ``mask`` is an additive/boolean attention mask
already broadcast to a shape SDPA accepts.

Backends (priority high -> low): ``sage3`` > ``sage2`` > ``sage`` > ``flash`` >
``sdpa``. ``sdpa`` is always available and is the numerical reference. The
accelerated kernels are used only for the plain (no mask, fp16/bf16) path; a
mask or an fp32 input transparently falls back to ``sdpa`` (those kernels
don't support dense masks / fp32), so correctness never depends on the
selected backend.

``sage3`` (SageAttention3, arXiv:2505.11594, thu-ml/SageAttention's
``sageattention3_blackwell`` — a *separate* source-only package, module name
``sageattn3``, entry point ``sageattn3_blackwell(q, k, v, is_causal=False)``,
head-split ``(B, H, L, D)`` fp16/bf16 in — same contract as ``sageattn``, no
``tensor_layout`` kwarg) uses hardware FP4 tensor cores that only exist on
specific Blackwell dies. Its CUDA extension is compiled with the
architecture-specific ``compute_XXXa``/``sm_XXXa`` flags (the trailing ``a``
means "family-specific PTX", not forward/backward compatible the way plain
``sm_90``-style flags are) for exactly three capabilities: ``(10, 0)``
(datacenter Blackwell, B100/B200), ``(12, 0)`` (consumer Blackwell, RTX 50
series — the user's dev GPU is a 5090), and ``(12, 1)`` (Blackwell Ultra/GB20x
variants). So the gate is exact-capability membership in that set, never a
``>=`` range like sage2's sm80+. The upstream build also hard-requires CUDA
runtime >= 12.8 (checked separately, since a stale nvcc/driver combo would
otherwise crash instead of falling back).

Selection: an explicit ``override`` or the ``NATIVE_ATTENTION`` env var wins if
that backend is available, otherwise a warning is logged and the highest-
priority available backend is used. Probe results are cached at import; call
:func:`reset_backend_cache` to re-probe (tests).

``sparge`` (SpargeAttention, arXiv:2502.18137, ICML2025; thu-ml/SpargeAttn,
pip/import name ``spas_sage_attn``) is training-free SPARSE attention built on
top of SageAttention2's kernels — a two-stage online filter predicts and skips
low-contribution attention blocks. Unlike every other backend here, it is
**approximate, not numerically near-lossless**: output quality depends on
content and the sparsity/accuracy tradeoff (its ``topk`` parameter). For that
reason ``sparge`` is a **pin-only** backend (:data:`PIN_ONLY_BACKENDS`): it is
never chosen automatically no matter how available it is, only ever selected
by an explicit ``$NATIVE_ATTENTION=sparge``, an explicit ``backend="sparge"``
call arg, or an explicit admin pin. Entry point
``spas_sage2_attn_meansim_topk_cuda`` — same head-split ``(B, H, L, D)``
contract as ``sageattn`` (``tensor_layout="HND"``, its default) and a
documented tune-free default (``topk=0.5``, no per-model calibration
required per the upstream README). Its kernel additionally requires
``seq_len >= 128`` and ``head_dim`` in ``{64, 128}`` — checked per call
(``_sparge_shape_ok``) since no other backend has this constraint; a call
that doesn't qualify falls back to ``sdpa`` like a mask/fp32/CPU call would.
Its compiled kernel only targets Ampere/Ada/Hopper (compute capability major
8 or 9, per ``thu-ml/SpargeAttn``'s ``setup.py`` ``SUPPORTED_ARCHS`` — it does
NOT list Blackwell, so this is unavailable on Blackwell today, unlike sage2/
sage3).
"""

from __future__ import annotations

import importlib.util
import logging
import os

import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

Tensor = torch.Tensor

SDPA = "sdpa"
FLASH = "flash"
SAGE = "sage"
SAGE2 = "sage2"
SAGE3 = "sage3"
SPARGE = "sparge"

# High -> low priority. sdpa is the always-present floor. PIN_ONLY backends
# (below) are deliberately excluded — never auto-selected regardless of order.
BACKEND_PRIORITY: list[str] = [SAGE3, SAGE2, SAGE, FLASH, SDPA]

# Backends that are valid EXPLICIT pins (name, env var, or admin panel) but are
# never chosen by auto-selection even when available — see the module
# docstring's ``sparge`` section for why (approximate, content-dependent
# quality, unlike the numerically near-lossless backends in BACKEND_PRIORITY).
PIN_ONLY_BACKENDS: frozenset[str] = frozenset({SPARGE})

# sage3's CUDA extension is built with family-specific (`...a`-suffixed) arch
# flags for exactly these three capabilities — see the module docstring. Not a
# `>=` range: an sm130 (future) card would need its own rebuild, not "just work".
_SAGE3_CAPABILITIES: frozenset[tuple[int, int]] = frozenset({(10, 0), (12, 0), (12, 1)})
_SAGE3_MIN_CUDA_RUNTIME: tuple[int, int] = (12, 8)

# sparge (SpargeAttn) compiles per-local-GPU at install time (unlike sage3's
# prebuilt family-specific kernel), so any minor within a supported major is
# fine — but thu-ml/SpargeAttn's setup.py SUPPORTED_ARCHS only lists 8.0/8.6/
# 8.7/8.9/9.0, i.e. major 8 or 9 (Ampere/Ada/Hopper); Blackwell (10/12) is not
# built at all as of this writing.
_SPARGE_CAPABILITY_MAJORS: frozenset[int] = frozenset({8, 9})
# spas_sage2_attn_meansim_topk_cuda's own hard requirements (its assertions):
# "seq_len should be not less than 128", "headdim should be in [64, 128]".
_SPARGE_MIN_SEQ_LEN = 128
_SPARGE_VALID_HEAD_DIMS: frozenset[int] = frozenset({64, 128})

ENV_VAR = "NATIVE_ATTENTION"

# Module-level probe cache, keyed by CUDA device INDEX (availability only;
# selection re-reads env each call). Per-device because compute capability is
# a per-device property: a multi-GPU box (e.g. a text encoder spilled to a
# second card while the DiT runs on cuda:0) can legitimately have a
# Blackwell-only backend (sage3) available on one device and not the other —
# a single global probe would report whichever device happened to be
# ``torch.cuda.current_device()`` at first-probe time for every device
# thereafter, silently dispatching a kernel built for the wrong GPU.
_availability: dict[int, dict[str, bool]] = {}
_warned_unavailable: set[str] = set()

# In-memory pin set via the admin UI (Admin -> Backends -> Optimizations). Seeded
# from the `native_attention_backend` setting at app startup and updated live by
# the pin endpoint; never read from the DB inside the hot dispatch path.
_backend_override: str | None = None


def set_backend_override(name: str | None) -> None:
    """Pin (or clear) the attention backend from outside a single call.

    Normalizes ``""``/``"auto"``/``None`` to ``None`` (= no pin, fall through to
    ``$NATIVE_ATTENTION`` or the best available backend).
    """
    global _backend_override
    if name is None:
        _backend_override = None
        return
    normalized = name.strip().lower()
    _backend_override = None if normalized in ("", "auto") else normalized


def get_backend_override() -> str | None:
    return _backend_override


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _cuda_capability_major(device_index: int | None = None) -> int:
    if not torch.cuda.is_available():
        return 0
    try:
        return torch.cuda.get_device_capability(device_index)[0]
    except Exception:  # noqa: BLE001 — a driver hiccup must not break probing
        return 0


def _cuda_capability(device_index: int | None = None) -> tuple[int, int]:
    """Full (major, minor) compute capability of ``device_index`` (``None`` =
    ``torch.cuda.current_device()``, matching ``torch.cuda.get_device_capability``'s
    own default) — sage3's exact-match gate needs the minor digit too (12.0 vs
    12.1 are different physical dies)."""
    if not torch.cuda.is_available():
        return (0, 0)
    try:
        return torch.cuda.get_device_capability(device_index)
    except Exception:  # noqa: BLE001 — a driver hiccup must not break probing
        return (0, 0)


def _cuda_runtime_version() -> tuple[int, int]:
    """torch's CUDA runtime version as (major, minor), or (0, 0) if unknown
    (CPU-only torch build, or a driver/parsing hiccup)."""
    raw = getattr(torch.version, "cuda", None)
    if not raw:
        return (0, 0)
    parts = raw.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except (ValueError, IndexError):
        return (0, 0)


def _sageattention_is_v2() -> bool:
    try:
        from importlib.metadata import version

        return version("sageattention").startswith("2")
    except Exception:  # noqa: BLE001
        return False


def _probe(device_index: int | None = None) -> dict[str, bool]:
    """Probe imports + hardware for each backend on ``device_index`` (``None`` =
    ``torch.cuda.current_device()``). Cached per-device; see reset_backend_cache."""
    cuda = torch.cuda.is_available()
    cap = _cuda_capability_major(device_index)
    has_triton = _has_module("triton")
    has_sage = _has_module("sageattention")
    has_flash = _has_module("flash_attn")
    has_sage3 = _has_module("sageattn3")
    has_sparge = _has_module("spas_sage_attn")

    # sage needs Triton + a CUDA GPU (sm70+); sage2 additionally wants Ampere+
    # (sm80+) and a 2.x sageattention build.
    sage_ok = has_sage and has_triton and cuda and cap >= 7
    sage2_ok = sage_ok and cap >= 8 and _sageattention_is_v2()
    # sage3: exact-capability Blackwell dies only (no Triton needed — separate
    # package, pure CUDA extension) + CUDA runtime >= 12.8 (the upstream
    # build's own hard requirement). See module docstring for why this is an
    # exact-membership check, not `cap >= (12, 0)`.
    sage3_ok = (
        has_sage3 and cuda
        and _cuda_capability(device_index) in _SAGE3_CAPABILITIES
        and _cuda_runtime_version() >= _SAGE3_MIN_CUDA_RUNTIME
    )
    # sparge: module present + CUDA + compute capability major 8 or 9 (see
    # _SPARGE_CAPABILITY_MAJORS' docstring — no Triton needed, pure CUDA
    # extension like sage3). Availability here is independent of PIN_ONLY
    # status: "available" just means the probe found a working install; being
    # pin-only only affects whether auto-selection may pick it.
    sparge_ok = has_sparge and cuda and cap in _SPARGE_CAPABILITY_MAJORS

    avail = {
        SDPA: True,  # torch SDPA is always present
        FLASH: has_flash and cuda,
        SAGE: sage_ok,
        SAGE2: sage2_ok,
        SAGE3: sage3_ok,
        SPARGE: sparge_ok,
    }
    logger.debug(
        "attention backend availability: %s (cuda=%s device=%s cap=sm%d)",
        avail, cuda, device_index, cap * 10,
    )
    return avail


def _resolve_device_key(device_index: int | None) -> int:
    """Pin ``None`` (\"current device\") to a concrete index for cache-keying
    purposes — caching under a literal ``None`` key would reuse whichever
    device happened to be current at the FIRST probe for every call
    thereafter, defeating the whole point of a per-device cache."""
    if device_index is not None:
        return device_index
    if not torch.cuda.is_available():
        return -1
    try:
        return torch.cuda.current_device()
    except Exception:  # noqa: BLE001 — a driver hiccup must not break probing
        return -1


def _get_availability(device_index: int | None = None) -> dict[str, bool]:
    key = _resolve_device_key(device_index)
    cached = _availability.get(key)
    if cached is None:
        cached = _probe(key)
        _availability[key] = cached
    return cached


def reset_backend_cache() -> None:
    """Drop the cached probe (and one-shot warnings) for EVERY device. Re-probes
    each device again on its next use."""
    _availability.clear()
    _warned_unavailable.clear()
    _noted_dispatch.clear()


def available_backends(device_index: int | None = None) -> list[str]:
    """AUTO-selectable backends on ``device_index`` (``None`` = current device),
    in priority order (high -> low). Always includes sdpa. Excludes
    :data:`PIN_ONLY_BACKENDS` (e.g. ``sparge``) even when available — those
    only ever come back from :func:`get_attention_backend` on an explicit
    request, never as "the best available backend"."""
    avail = _get_availability(device_index)
    return [b for b in BACKEND_PRIORITY if avail.get(b, False)]


def known_backends() -> frozenset[str]:
    """Every backend NAME this dispatcher recognizes as a valid explicit pin —
    :data:`BACKEND_PRIORITY` (auto-selectable) UNION :data:`PIN_ONLY_BACKENDS`
    (e.g. ``sparge``), regardless of whether either is actually installed on
    this machine. This is the single source of truth for "is this a real
    backend name" — callers validating a user-supplied pin (e.g. the admin API)
    should check against this instead of duplicating a name list, or a
    pin-only addition here would silently need a second edit elsewhere to stay
    pinnable end-to-end (see ``api/controllers/backend_controller.py``'s
    ``set_attention_backend``, which does exactly this)."""
    return frozenset(BACKEND_PRIORITY) | PIN_ONLY_BACKENDS


def get_attention_backend(override: str | None = None, device_index: int | None = None) -> str:
    """Resolve the backend to use on ``device_index`` (``None`` = current device).

    Precedence: ``override`` arg, then ``$NATIVE_ATTENTION``, then the in-memory
    pin set via :func:`set_backend_override` (Admin -> Backends -> Optimizations),
    then the highest-priority auto-selectable backend (:func:`available_backends`).
    An EXPLICIT request (any of the first three) is honored if the backend is
    available at all — including a :data:`PIN_ONLY_BACKENDS` member, which is
    otherwise never returned when ``requested`` is ``None``. A requested-but-
    unavailable or unknown choice logs a warning (once per name) and falls back
    to the best auto-selectable backend. No DB read happens here - this runs
    per forward call. ``device_index`` matters because availability (esp.
    sage3's exact-capability gate) is per-device — see the ``_availability``
    cache's module-level comment.
    """
    full_avail = _get_availability(device_index)  # every probed backend, incl. pin-only
    auto_avail = available_backends(device_index)  # non-empty (sdpa floor); excludes pin-only
    requested = override or os.environ.get(ENV_VAR) or _backend_override or None

    if requested is None:
        return auto_avail[0]

    requested = requested.strip().lower()
    if full_avail.get(requested, False):
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


# One-shot dispatch notes: the first call of each kind (kernel actually used /
# fallback + why) logs at INFO so a single generation's log shows definitively
# which attention path ran; repeats stay at DEBUG. Cleared by reset_backend_cache.
_noted_dispatch: set[str] = set()


def _note_dispatch(key: str, fmt: str, *args) -> None:
    if key in _noted_dispatch:
        logger.debug(fmt, *args)
        return
    _noted_dispatch.add(key)
    logger.info(fmt, *args)


def _sdpa(q: Tensor, k: Tensor, v: Tensor, mask: Tensor | None) -> Tensor:
    return F.scaled_dot_product_attention(q, k, v, attn_mask=mask, dropout_p=0.0, is_causal=False)


def _flash(q: Tensor, k: Tensor, v: Tensor) -> Tensor:
    # flash_attn_func wants (B, L, H, D) and fp16/bf16; caller guarantees no mask.
    from flash_attn import flash_attn_func

    out = flash_attn_func(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2), causal=False)
    return out.transpose(1, 2)


# sage v1 internally downcasts V to fp16 (``v = v.to(torch.float16)``) with
# fp16 PV accumulation and no configurable accumulator dtype.
#
# Two competing failure modes, both observed in production:
#
# * Large-V overflow (Qwen-Image, V ~ N(0, 5000^2) at its real joint-attention
#   shape): the kernel's fp16 internals overflow to +/-inf, the softmax-weighted
#   sum turns NaN, and the DiT propagates a black image.  Unconditionally
#   pre-dividing V by 256 (and multiplying the output back) fixed this — the
#   division by a power of two is exact on the input.
# * Small-V quantization noise (LTX 2.3, V magnitudes ~1e2): dividing by 256
#   pushes small V elements toward fp16's subnormal range inside the kernel,
#   and the amplified rounding error (cos=0.999996, max|d|=0.03 per call vs
#   raw V) compounds across ~1500 attention calls (48 blocks x 4 attn ops x
#   8 denoise steps) into a spatially uniform film-grain artifact. ComfyUI
#   passes V raw and shows no grain.
#
# So the rescale is applied CONDITIONALLY: raw pass-through (bit-identical to
# ComfyUI) while ``max|V|`` is comfortably below fp16 territory, and the exact
# production-validated /256 path once V is hot enough to threaten the kernel's
# fp16 internals.  The branch is computed on-GPU via ``torch.where`` (a 0-dim
# tensor scale; ``v / 1.0`` is IEEE-exact), so there is still no per-call host
# sync — an ``isfinite()``-check-and-fallback approach was previously measured
# to erase sage's entire speed advantage (~1.4ms forced sync per call).
# ``nan_to_num`` on the output remains the zero-sync safety net for the
# pathological remainder, so this call can never hand a NaN/Inf back.
_SAGE_V_SCALE = 256.0
# Raw pass-through below this max|V|; above it, the /256 path engages. LTX 2.3
# sits ~1e2, the Qwen-Image overflow repro ~2.6e4 — the threshold splits the
# two regimes with an order of magnitude of margin on each side.
_SAGE_V_SAFE_MAX = 1024.0


# Above this row count, `_sage` trades its zero-sync `torch.where` scale for
# ONE host sync per call: at e.g. the H3 upscale refine's ~78k rows the two
# full-V copies (`v / scale`, `out * scale`) cost ~2.3GB of transient VRAM in
# a regime that is exactly where OOMs happen, while the call count is a few
# hundred per refine — so a ~1ms sync is the cheaper side of the trade there.
# Below it, the production-validated sync-free path is untouched (the sync
# variant was previously measured to erase sage's speed edge on hot paths).
_SAGE_SYNC_ROWS = 32768


def _absmax(t: Tensor) -> Tensor:
    """``t.abs().amax()`` without materializing a full-size ``|t|`` temp --
    two scalar reductions instead. On a 78k-row H3 refine V that temp is
    ~2.8GB, and it was the allocation that OOM'd a real 5090 run inside the
    prescale CHECK itself."""
    return torch.maximum(t.amax(), t.amin().neg())


def _sage(q: Tensor, k: Tensor, v: Tensor) -> Tensor:
    # sageattn accepts head-split (B, H, L, D) via tensor_layout="HND".
    from sageattention import sageattn

    if v.shape[2] >= _SAGE_SYNC_ROWS:
        if float(_absmax(v)) > _SAGE_V_SAFE_MAX:
            out = sageattn(q, k, v / _SAGE_V_SCALE, tensor_layout="HND", is_causal=False)
            out = out * _SAGE_V_SCALE
        else:
            # Raw pass-through with NO copy of V -- numerically identical to
            # the scale=1.0 branch below (`v / 1.0` is IEEE-exact), minus the
            # two full-size allocations.
            out = sageattn(q, k, v, tensor_layout="HND", is_causal=False)
        return torch.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    scale = torch.where(
        _absmax(v) > _SAGE_V_SAFE_MAX,
        v.new_tensor(_SAGE_V_SCALE),
        v.new_tensor(1.0),
    )
    out = sageattn(q, k, v / scale, tensor_layout="HND", is_causal=False)
    out = out * scale
    return torch.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def _sage3(q: Tensor, k: Tensor, v: Tensor) -> Tensor:
    # sageattn3_blackwell takes head-split (B, H, L, D) directly — no
    # tensor_layout kwarg (unlike sageattn/sageattn2's "HND" default), per the
    # sageattention3_blackwell README.
    from sageattn3 import sageattn3_blackwell

    return sageattn3_blackwell(q, k, v, is_causal=False)


def _sparge_shape_ok(q: Tensor) -> bool:
    """SpargeAttn's kernel hard-requires ``seq_len >= 128`` and ``head_dim`` in
    ``{64, 128}`` (thu-ml/SpargeAttn's own assertions) — no other backend here
    has this constraint, so it's checked only when ``sparge`` is chosen."""
    return q.shape[2] >= _SPARGE_MIN_SEQ_LEN and q.shape[3] in _SPARGE_VALID_HEAD_DIMS


def _sparge(q: Tensor, k: Tensor, v: Tensor) -> Tensor:
    # spas_sage2_attn_meansim_topk_cuda: head-split (B, H, L, D) via
    # tensor_layout="HND" (its default — same contract as sageattn/sageattn2).
    # topk=0.5 is the upstream README's documented tune-free default (no
    # per-model calibration needed); output_dtype pinned to q's own dtype so
    # the kernel's own fp16-only default can't silently narrow a bf16 caller.
    from spas_sage_attn import spas_sage2_attn_meansim_topk_cuda

    return spas_sage2_attn_meansim_topk_cuda(
        q, k, v, is_causal=False, tensor_layout="HND", topk=0.5, output_dtype=q.dtype,
    )


def attention(
    q: Tensor,
    k: Tensor,
    v: Tensor,
    *,
    heads: int | None = None,
    mask: Tensor | None = None,
    backend: str | None = None,
) -> Tensor:
    """Dispatch scaled-dot-product attention to the selected backend.

    ``q``/``k``/``v``: ``(B, H, L, D)``. Returns ``(B, H, L, D)``. See the module
    docstring for the full contract. A mask or an fp32 input forces the ``sdpa``
    path (the accelerated kernels support neither), so the result is backend-
    independent up to kernel precision.
    """
    if heads is not None and q.ndim == 4 and heads != q.shape[1]:
        raise ValueError(f"heads={heads} disagrees with q.shape[1]={q.shape[1]}")

    # Validate availability against the OPERAND's device, not whatever CUDA
    # device happens to be "current" on this thread — a multi-GPU box (e.g. a
    # text encoder spilled to cuda:1 while the DiT runs on cuda:0) can have a
    # Blackwell-only backend available on one card and not the other.
    device_index = q.device.index if q.is_cuda else None
    chosen = get_attention_backend(backend, device_index)

    # Accelerated kernels: CUDA tensors, fp16/bf16 only, and no dense mask.
    # Otherwise sdpa. The device check matters: CPU-side attention calls exist
    # (e.g. LTX's conditioning connectors run on CPU) and sage/flash assert on
    # non-CUDA inputs rather than falling back. ``sparge`` additionally needs
    # seq_len/head_dim within its kernel's hard limits (_sparge_shape_ok) — no
    # other backend has that constraint.
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
        else:
            reason = None
    else:
        reason = None

    if reason is not None:
        _note_dispatch(f"{chosen}->sdpa:{reason}",
                       "attention: %s falls back to sdpa (%s-constrained call, shape %s)",
                       chosen, reason, tuple(q.shape))
        chosen = SDPA
    elif accelerated:
        _note_dispatch(chosen, "attention: %s kernels in use (shape %s)", chosen, tuple(q.shape))

    if chosen == FLASH:
        return _flash(q, k, v)
    if chosen == SAGE3:
        return _sage3(q, k, v)
    if chosen == SPARGE:
        return _sparge(q, k, v)
    if chosen in (SAGE2, SAGE):
        return _sage(q, k, v)
    return _sdpa(q, k, v, mask)
