"""Apply / remove mapped LoRA deltas onto a native arch module at runtime.

Two application modes, chosen per-Linear by whether its weight *storage* can be
safely patched in place — this is what "composes with the ops layer" means:

  * **in-place** (storage dtype is fp32/fp16/bf16): the delta is *materialised
    into the weight in place* (``W += scale * alpha/rank * up @ down``, computed
    in fp32 and cast back to the storage dtype). No per-forward cost. This
    applies REGARDLESS of ``comfy_cast_weights`` — a mixed-precision checkpoint
    (e.g. Krea-2: bf16/f32 weights alongside a few fp8 ones) puts every Linear
    under ``manual_cast``/``fp8_ops`` even though most of their individual
    weights are perfectly patchable float storage. Routing those through the
    runtime-delta path was the bug: every forward re-cloned the full weight and
    recomputed the delta (a full-size ``torch.kron`` for LoKr) on every layer,
    every step — the actual root cause of a user-reported 93%-of-sampling-time
    CPU spike on a Krea-2 + LoKr generation.

  * **runtime / quantized** (storage cannot be losslessly patched: fp8_*, or
    nvfp4's packed uint8 with no float weight tensor at all): the deltas are
    attached to the Linear's ``lora_deltas`` list and applied to the
    *dequantised, compute-dtype* weight each forward
    (:func:`vendor.gpl.comfyui.ops.apply_lora_deltas`). Quantized storage is never
    touched.

Both modes share the same delta math, so a quantized forward and an in-place
forward with the same LoRA agree to within dequant/storage-rounding tolerance.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Dict, Iterator, List, Tuple

import torch
import torch.nn as nn

from vendor.gpl.comfyui.ops import apply_lora_deltas
from .key_mapping import LoraDelta, map_lora_keys

logger = logging.getLogger(__name__)

# Attribute stashing the applied LoraDelta specs (tiny) per-Linear, for exact
# removal — NOT a full weight-shaped copy (see module docstring point 3: that
# would double the resident memory of every patched weight, GBs on Krea-2).
_INPLACE_ATTR = "_native_lora_inplace"

# Storage dtypes that can be patched in place at fp32-compute precision. Any
# other float storage (float8_e4m3fn, float8_e5m2, ...) must keep runtime
# deltas — patching quantized storage would corrupt it.
_PATCHABLE_DTYPES = (torch.float32, torch.float16, torch.bfloat16)


class _ScratchPool:
    """Reusable flat buffers for LoRA delta math, scoped to ONE call.

    ``_compute_delta``/``_apply_inplace`` used to allocate one or more fresh
    ``weight_shape``-sized tensors PER Linear (the fp32 delta/accumulator, plus
    the final device+dtype transfer to the weight's own storage). Across a
    whole DiT that's thousands of large alloc/free cycles; measured on a real
    LoRA-swap repro, that churn fragments the host allocator
    badly enough that ``malloc_trim`` cannot reclaim it while the model stays
    cache-resident — RSS stays permanently elevated after the FIRST LoRA-
    bearing load.

    A pool built once per :func:`apply_loras`/:func:`remove_loras` call and
    threaded through every Linear it touches turns that into a handful of
    buffers, each grown (reallocated) only the first time a larger one is
    needed and reused via ``torch.Tensor.copy_``/``out=`` for every Linear
    after that — no per-Linear alloc/free at all in the common (single
    unsliced LoRA) case. Never shared across calls: an ``apply_loras()`` call
    always closes with either a live LoRA on the module or none, so there is
    nothing worth keeping the scratch memory around for once it returns
    (unlike the model cache itself, which stays resident on purpose).
    """

    def __init__(self) -> None:
        self._buffers: dict[tuple[str, torch.device, torch.dtype], torch.Tensor] = {}

    def get(self, slot: str, device: torch.device, dtype: torch.dtype, numel: int) -> torch.Tensor:
        """A flat 1-D tensor of at least ``numel`` elements on ``(slot, device,
        dtype)``. ``slot`` namespaces independent concurrent uses (e.g. the
        running accumulator vs. the current delta term) so they never alias
        the same physical buffer even though they share a device/dtype.

        May be larger than requested (grow-only) - callers must ``.narrow(0, 0,
        numel)`` before viewing it as a shape.
        """
        key = (slot, device, dtype)
        buf = self._buffers.get(key)
        if buf is None or buf.numel() < numel:
            buf = torch.empty(numel, dtype=dtype, device=device)
            self._buffers[key] = buf
        return buf

    def view(self, slot: str, device: torch.device, dtype: torch.dtype, shape: torch.Size) -> torch.Tensor:
        """A buffer of ``dtype``/``device`` reshaped to exactly ``shape``."""
        numel = 1
        for s in shape:
            numel *= s
        return self.get(slot, device, dtype, numel).narrow(0, 0, numel).view(shape)


def _resolve_linear(module: nn.Module, param_name: str) -> tuple[nn.Module, str] | None:
    """Return (owning_module, 'weight') for a ``...weight`` param name."""
    if not param_name.endswith(".weight"):
        return None
    owner_path = param_name[: -len(".weight")]
    sub = module
    for part in owner_path.split("."):
        if not hasattr(sub, part):
            return None
        sub = getattr(sub, part)
    if not isinstance(sub, nn.Linear):
        return None
    return sub, "weight"


def _needs_runtime_deltas(linear: nn.Module) -> bool:
    """True iff ``linear``'s weight storage cannot be patched in place.

    Storage dtype is the authoritative criterion (fp32/fp16/bf16 = patchable;
    float8_* = not, regardless of whether a per-tensor scale happens to be set
    — patching float8 storage with an fp32-computed delta cast back to float8
    is lossy in a way plain float storage isn't). nvfp4 is checked via the
    explicit ``_is_nvfp4`` marker (``Nvfp4Linear``) rather than dtype-sniffing:
    a quantized nvfp4 layer clears ``self.weight`` entirely (packed uint8 codes
    live in separate buffers, dequantised fresh every forward already), so
    there is no weight tensor whose dtype would even mean "storage dtype".

    ConvRot is likewise checked by marker, and for a reason dtype cannot express:
    its stored weight is in a ROTATED basis while a LoRA delta is expressed in
    the layer's original one, so adding the two in place is wrong at any storage
    width. Only the dequantised compute weight — which the forward path has
    already un-rotated — is in the basis the delta belongs to.
    """
    if getattr(linear, "_is_nvfp4", False):
        return True
    if getattr(linear, "convrot_hadamard", None) is not None:
        return True
    weight = getattr(linear, "weight", None)
    if weight is None:
        return True  # defensive: nothing to patch
    return weight.dtype not in _PATCHABLE_DTYPES


def apply_loras(
    module: nn.Module,
    loras: list[tuple[dict[str, torch.Tensor], float]],
) -> tuple[int, list[str]]:
    """Apply a stack of LoRAs to ``module``.

    ``loras`` is a list of ``(lora_state_dict, strength)``. Each is mapped to
    native params and applied additively (multiple LoRAs stack). Returns
    ``(num_params_patched, unmatched_keys)`` aggregated across the stack.
    """
    all_unmatched: list[str] = []
    patched_params: set[str] = set()
    # One scratch pool for the whole call (every Linear this stack touches) -
    # see _ScratchPool's docstring for why this, not per-Linear allocation,
    # is what actually fixes the host-RAM fragmentation.
    pool = _ScratchPool()

    for lora_sd, strength in loras:
        mapped, unmatched = map_lora_keys(lora_sd, module)
        all_unmatched.extend(unmatched)
        for param_name, deltas in mapped.items():
            resolved = _resolve_linear(module, param_name)
            if resolved is None:
                all_unmatched.append(param_name)
                continue
            linear, _ = resolved
            scaled = [
                LoraDelta(down=d.down, up=d.up, alpha=d.alpha,
                          scale=d.scale * float(strength), target_slice=d.target_slice,
                          kron=d.kron)
                for d in deltas
            ]
            if _needs_runtime_deltas(linear):
                if linear.lora_deltas is None:
                    linear.lora_deltas = []
                linear.lora_deltas.extend(scaled)
            else:
                _apply_inplace(linear, scaled, pool)
            patched_params.add(param_name)

    return len(patched_params), all_unmatched


def _slice_of(target_slice: tuple) -> tuple:
    """``target_slice`` is ``(dim, start, length)`` on dim 0 today (see
    ``LoraDelta``) - build the matching ``tensor[...]`` index tuple."""
    _dim, start, length = target_slice
    return (slice(start, start + length),)


def _compute_delta_into(
    out: torch.Tensor,
    deltas: list[LoraDelta],
    compute_device: torch.device,
    pool: _ScratchPool,
) -> None:
    """Sum ``deltas`` (fp32, on ``compute_device``) INTO ``out`` (fp32, same
    shape/device, already zeroed by the caller) - the delta math is unchanged
    from the original ``_compute_delta`` (same fp32 accumulation, same LoKr
    ``torch.kron`` / plain-LoRA rank expansion, same slice handling); only the
    destination is now a reused buffer instead of a fresh allocation per call.

    Each term (the ``torch.kron``/matmul rank expansion, ``weight_shape``-sized)
    is written directly into a pool-provided "term" scratch tensor via ``out=``
    instead of allocating a new one, then scaled in place and accumulated into
    ``out``. ``out`` uses a DIFFERENT pool slot than "term" so the running
    accumulator and the current term never alias the same physical buffer.
    """
    for d in deltas:
        up = d.up.to(device=compute_device, dtype=torch.float32)
        down = d.down.to(device=compute_device, dtype=torch.float32)
        dest = out[_slice_of(d.target_slice)] if d.target_slice is not None else out
        if d.kron:
            # LoKr targets the whole weight (see LoraDelta docstring - never
            # sliced); torch.kron's own natural output shape is
            # (up.rows*down.rows, up.cols*down.cols), which the original code
            # reshaped to weight_shape - do the same here via a same-storage
            # .view() (no copy) once torch.kron has filled the scratch buffer.
            natural_shape = torch.Size((up.shape[0] * down.shape[0], up.shape[1] * down.shape[1]))
            term = pool.view("term", compute_device, torch.float32, natural_shape)
            torch.kron(up, down, out=term)
            term = term.view(dest.shape)
            term.mul_(float(d.scale) * float(d.alpha))
        else:
            rank = d.down.shape[0]
            term = pool.view("term", compute_device, torch.float32, dest.shape)
            torch.matmul(up, down, out=term)
            term.mul_(float(d.scale) * float(d.alpha) / rank)
        dest += term


def _apply_inplace(linear: nn.Module, deltas: list[LoraDelta], pool: "_ScratchPool | None" = None) -> None:
    """Materialise ``deltas`` into ``linear.weight``'s EXISTING storage.

    Computes the delta on CUDA when available, regardless of which device the
    weight itself lives on, then adds it into the weight tensor's own storage
    via ``add_`` — never rebinds ``linear.weight.data`` to a new tensor, so
    pinned-ness (partial-residency streaming pins CPU-resident weights) and
    any external views/registrations of that storage survive.

    Both the fp32 accumulator (on ``compute_device``) and the final
    device+dtype transfer onto ``weight``'s own storage come from ``pool`` -
    a persistent, call-scoped buffer reused across every Linear instead of a
    fresh ``weight_shape``-sized allocation per Linear (see ``_ScratchPool``
    and ``_compute_delta_into``). ``pool`` defaults to a fresh one-Linear-only
    pool when omitted (e.g. a direct/unit-test call on a single Linear, where
    there is nothing to amortize a shared pool across).
    """
    if pool is None:
        pool = _ScratchPool()
    weight = linear.weight.data
    compute_device = torch.device("cuda") if torch.cuda.is_available() else weight.device
    with torch.no_grad():
        total = pool.view("accum", compute_device, torch.float32, weight.shape)
        total.zero_()
        _compute_delta_into(total, deltas, compute_device, pool)
        host = pool.view("host", weight.device, weight.dtype, weight.shape)
        host.copy_(total)  # single call does both the dtype cast and the device transfer
        weight.add_(host)

    record = getattr(linear, _INPLACE_ATTR, None)
    if record is None:
        record = []
        setattr(linear, _INPLACE_ATTR, record)
    record.extend(deltas)


def remove_loras(module: nn.Module) -> None:
    """Undo every LoRA applied by :func:`apply_loras` (both modes).

    The in-place path recomputes each applied delta (negated scale) through
    the same :func:`_compute_delta` path and subtracts it back out, rather than
    keeping a full weight-shaped "undo" copy — restoration is therefore exact
    only to about 1 ulp of storage-dtype rounding (two independent
    fp32-compute-then-cast-to-storage-dtype roundings), not bit-identical. This
    is acceptable: nothing on a hot path calls ``remove_loras`` today — every
    native model loader keys its weight cache on a fingerprint that includes
    the LoRA stack (e.g. ``model_loader/krea2``'s ``dit_fp = f"{dit_path}|
    {dtype}|{lora_fp}"``), so a LoRA change busts the cache and reloads the
    weights from disk fresh rather than incrementally add/remove-ing on a live
    module. ``remove_loras`` exists for tests (and any future incremental-
    reload path) to fully undo an apply on the same in-memory module.
    """
    pool = _ScratchPool()  # shared across every Linear this call touches - see apply_loras
    for _, sub in module.named_modules():
        if getattr(sub, "lora_deltas", None):
            sub.lora_deltas = None

        record = getattr(sub, _INPLACE_ATTR, None)
        if record:
            weight = sub.weight.data
            compute_device = torch.device("cuda") if torch.cuda.is_available() else weight.device
            negated = [
                LoraDelta(down=d.down, up=d.up, alpha=d.alpha, scale=-d.scale,
                          target_slice=d.target_slice, kron=d.kron)
                for d in record
            ]
            with torch.no_grad():
                total = pool.view("accum", compute_device, torch.float32, weight.shape)
                total.zero_()
                _compute_delta_into(total, negated, compute_device, pool)
                host = pool.view("host", weight.device, weight.dtype, weight.shape)
                host.copy_(total)
                weight.add_(host)
        if record is not None:
            delattr(sub, _INPLACE_ATTR)


def _snapshot_linear_states(module: nn.Module) -> List[Tuple[nn.Module, "int | None", "int | None"]]:
    """Per-``Linear`` ``(sub, resident_len, inplace_record_len)`` before an
    :func:`apply_loras` call, so :func:`_restore_linear_states` can undo only
    what THAT call adds (``None`` = the attribute was unset/empty beforehand)."""
    states: List[Tuple[nn.Module, "int | None", "int | None"]] = []
    for _, sub in module.named_modules():
        if not isinstance(sub, nn.Linear):
            continue
        deltas = getattr(sub, "lora_deltas", None)
        record = getattr(sub, _INPLACE_ATTR, None)
        states.append((sub, len(deltas) if deltas else None, len(record) if record else None))
    return states


def _restore_linear_states(
    states: List[Tuple[nn.Module, "int | None", "int | None"]],
) -> None:
    """Undo exactly the tail entries a snapshotted :func:`apply_loras` call
    added, per ``sub`` -- resident ``lora_deltas`` are truncated back (list
    identity is not preserved, only content), baked weights get the newly
    added entries subtracted back out via the same negate-and-add math as
    :func:`remove_loras` (~1 ulp of storage-dtype rounding, not bit-exact),
    scoped to those entries alone so any earlier (e.g. generation-stage)
    LoRA already resident/baked on ``sub`` before the snapshot survives
    untouched."""
    pool = _ScratchPool()
    for sub, deltas_len, record_len in states:
        deltas = getattr(sub, "lora_deltas", None)
        if deltas is not None:
            sub.lora_deltas = deltas[:deltas_len] if deltas_len else None

        record = getattr(sub, _INPLACE_ATTR, None)
        if record is None:
            continue
        added = record[record_len:] if record_len else record
        if added:
            negated = [
                LoraDelta(down=d.down, up=d.up, alpha=d.alpha, scale=-d.scale,
                          target_slice=d.target_slice, kron=d.kron)
                for d in added
            ]
            weight = sub.weight.data
            compute_device = torch.device("cuda") if torch.cuda.is_available() else weight.device
            with torch.no_grad():
                total = pool.view("accum", compute_device, torch.float32, weight.shape)
                total.zero_()
                _compute_delta_into(total, negated, compute_device, pool)
                host = pool.view("host", weight.device, weight.dtype, weight.shape)
                host.copy_(total)
                weight.add_(host)
        if record_len:
            setattr(sub, _INPLACE_ATTR, record[:record_len])
        else:
            delattr(sub, _INPLACE_ATTR)


@contextmanager
def temporarily_applied_loras(
    module: nn.Module,
    loras: List[Tuple[Dict[str, torch.Tensor], float]],
) -> Iterator[None]:
    """Apply ``loras`` onto ``module`` for the ``with`` block, restoring the
    exact prior per-``Linear`` state on exit -- including on an exception
    raised inside the block (``finally``).

    Scoped, not module-wide: unlike :func:`remove_loras` (which undoes EVERY
    LoRA ever applied to ``module``), this only reverses what THIS call adds,
    by snapshotting each ``Linear``'s resident-delta list length / baked
    in-place record length before applying and truncating/subtracting back to
    exactly that afterward. A LoRA stack already resident/baked on ``module``
    before entry (e.g. the generation-stage stack a native model loader
    applied at load time) is additive with, and survives, this call intact --
    the whole point of a stage-2-only LoRA addition (project rule: refinement
    shares the generation LoRA chain, this only ADDS to it for the wrapped
    span).

    ``loras`` empty -> no-op (``module`` is never touched, not even walked).

    Non-reentrant: do not nest two overlapping ``temporarily_applied_loras``
    calls on the SAME module (the inner call's snapshot would capture the
    outer's already-applied deltas as "pre-existing" state, so the outer's
    own exit would then fail to fully undo its own application) -- callers
    must fully exit one call before entering another on the same module.
    """
    if not loras:
        yield
        return
    states = _snapshot_linear_states(module)
    apply_loras(module, loras)
    try:
        yield
    finally:
        _restore_linear_states(states)
