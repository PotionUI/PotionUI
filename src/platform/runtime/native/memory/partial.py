"""Partial layer residency — ComfyUI-style lowvram, through the ops seam.

The all-or-nothing alternative is: either a component fits fully on the GPU
(fast) or it is streamed *entirely* from the CPU per forward (``manual_cast`` on
a module whose weights never left RAM — slow, and only correct if the whole
module was moved to the GPU first, which is exactly the OOM this avoids). This
module does what ComfyUI's ``model_patcher`` lowvram path does: keep **as many
leaf modules resident on the GPU as fit** in a weights budget, and stream only
the remainder from *pinned* CPU RAM with ``non_blocking=True`` H2D copies.

It needs **no arch-module changes**. Every parameterised layer an arch is built
from is an ops-namespace class (``CastWeightBiasOp`` subclass) whose ``forward``
already dispatches to ``forward_comfy_cast_weights`` when ``comfy_cast_weights``
is set. That path casts the weight to ``input.device`` per forward — i.e. it
*streams* a CPU-resident weight to the GPU on demand. So partial residency is
purely a placement decision:

  * **resident leaf** — its weights live on the GPU; ``comfy_cast_weights`` is
    left at the namespace default (``False`` for plain bf16, ``True`` for
    cast/fp8, both cheap once the weight is already on-device).
  * **streamed leaf** — its weights stay on *pinned* CPU RAM and
    ``comfy_cast_weights`` is forced ``True`` so each forward copies them to the
    activation's device and back-frees them.

Streaming is restricted to ``Linear`` / ``Conv`` / norm leaves, whose
``forward_comfy_cast_weights`` casts to ``input.device``. ``Embedding`` is never
streamed (its forward casts to ``self.weight.device``, so a CPU-resident
embedding would not follow the activation to the GPU) — embeddings are small and
always kept resident. Bare ``nn.Parameter`` tensors and non-ops modules are
likewise never streamed and stay resident.

The split is **estimate-driven and deterministic** (leaves in ``named_modules``
order, greedy prefix resident) so it is unit-testable without a GPU.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterator, Sequence

import torch
import torch.nn as nn

from src.platform.runtime.native.kernels import CastWeightBiasOp, stream_wait
from src.platform.observability.profiling import add_pinned_bytes, get_profiler
from src.platform.settings.runtime_flags import runtime_flag

from ..base import release_derived_caches
from .pinned_arena import PinnedArena, caching_host_block_bytes

logger = logging.getLogger(__name__)

# These three module-level flags each guard a warning whose underlying cause
# (a bad env value, or a persistent prefetch failure) doesn't change mid-process
# - without them, a streamed generation would re-log the same message every
# single run instead of once per process.
_warned_prefetch_setup_failed = False
_warned_prefetch_failed = False
_warned_arena_failed = False
_warned_pin_fallback = False

_BYTES_PER_GB = 1024 ** 3

# How much of the vacated pinned pool teardown may leave un-released while its
# sweep is mid-flight — the cap on the fresh-copies-plus-cached-pool double-hold
# (see ``ModuleStreamer.teardown``). 2GB matches the engine's
# ``_PINNED_RELEASE_FLOOR_GB``: pools smaller than this are worth keeping warm,
# so they are also not worth draining incrementally.
_TEARDOWN_RELEASE_CHUNK_GB = 2.0


class PinnedMemoryUnavailableError(RuntimeError):
    pass


def stream_prefetch_enabled() -> bool:
    return bool(runtime_flag("native_stream_prefetch"))


# CUDA-primitive seams. Indirected through module-level functions so the
# prefetcher is unit-testable on a CPU box: tests monkeypatch these with fakes;
# production forwards them straight to ``torch.cuda``. Keeping them tiny and pure
# is the whole reason the (GPU-only) stream/event choreography can be asserted
# without a device.
def _new_cuda_stream(device=None):
    return torch.cuda.Stream(device=device)


def _new_cuda_event():
    return torch.cuda.Event()


def _current_cuda_stream(device=None):
    return torch.cuda.current_stream(device)


def _cuda_stream_ctx(stream):
    return torch.cuda.stream(stream)


def _release_idle_vram() -> None:
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _register_forward_hook_always(module: nn.Module, hook):
    """``register_forward_hook`` with ``always_call=True`` when the torch build
    supports it, so a restore hook still fires if the wrapped forward raises
    (leaving no weight swapped to the GPU). Falls back to a plain registration on
    older torch that lacks the kwarg."""
    try:
        return module.register_forward_hook(hook, always_call=True)
    except TypeError:  # pragma: no cover - torch < 2.1 has no always_call
        return module.register_forward_hook(hook)


def _stage_copy(tensor: torch.Tensor, device: str, *, non_blocking: bool) -> torch.Tensor:
    """Issue the H2D copy of a streamed leaf's weight (pinned CPU -> ``device``)."""
    return tensor.to(device=device, non_blocking=non_blocking)


def _record_stream_on(tensor: torch.Tensor, stream) -> None:
    """Tell the caching allocator ``stream`` will use ``tensor`` (defer its reuse).

    A no-op if the tensor has no ``record_stream`` (CPU tensors in tests)."""
    rec = getattr(tensor, "record_stream", None)
    if callable(rec):
        rec(stream)


def _pin(data: torch.Tensor) -> torch.Tensor:
    try:
        pinned = data.pin_memory()
    except Exception as exc:
        raise PinnedMemoryUnavailableError(
            f"pin_memory() failed for {data.numel() * data.element_size()} bytes ({type(exc).__name__}: {exc})"
        ) from exc
    add_pinned_bytes(caching_host_block_bytes(pinned.numel() * pinned.element_size()))
    return pinned


def _unpinned_copy(data: torch.Tensor) -> torch.Tensor:
    plain = torch.empty_like(data, pin_memory=False)
    plain.copy_(data)
    return plain


def _note_pin_fallback(exc: BaseException) -> None:
    global _warned_pin_fallback
    if _warned_pin_fallback:
        logger.info("pinned host memory unavailable (%s); placing without page-locked copies", exc)
        return
    _warned_pin_fallback = True
    logger.warning(
        "pinned host memory unavailable (%s); streaming from pageable host memory with synchronous "
        "host-to-device copies, slower than pinned streaming (page-locked memory can be capped, "
        "e.g. under WSL2 or Docker Desktop)",
        exc,
    )


def _own_tensor_bytes(module: nn.Module) -> int:
    """Bytes of a module's OWN params + buffers (not its children)."""
    total = 0
    for p in module.parameters(recurse=False):
        total += p.numel() * p.element_size()
    for b in module.buffers(recurse=False):
        if b is not None:
            total += b.numel() * b.element_size()
    return total


def is_nvfp4_leaf(module: nn.Module) -> bool:
    return (
        isinstance(module, CastWeightBiasOp)
        and not isinstance(module, nn.Embedding)
        and bool(getattr(module, "_is_nvfp4", False))
        and getattr(module, "nvfp4_packed", None) is not None
    )


def is_streamable_leaf(module: nn.Module, *, include_nvfp4: bool = False) -> bool:
    """A leaf whose forward streams its weight to ``input.device`` on demand.

    True for ops-namespace ``Linear`` / ``Conv`` / norm layers carrying a weight;
    False for ``Embedding`` (casts to ``self.weight.device``, not the activation's)
    and for anything that is not a ``CastWeightBiasOp``.
    """
    if not isinstance(module, CastWeightBiasOp):
        return False
    if isinstance(module, nn.Embedding):
        return False
    if getattr(module, "weight", None) is not None:
        return True
    return include_nvfp4 and is_nvfp4_leaf(module)


def iter_streamable_leaves(root: nn.Module, *, include_nvfp4: bool = False) -> Iterator[tuple[str, nn.Module]]:
    """Yield ``(qualified_name, module)`` for every streamable leaf, in order."""
    for name, module in root.named_modules():
        if is_streamable_leaf(module, include_nvfp4=include_nvfp4):
            yield name, module


@dataclass(frozen=True)
class PartialResidencyPlan:
    """Which leaves stay resident on the GPU vs stream from pinned CPU RAM."""

    resident_names: tuple[str, ...]
    streamed_names: tuple[str, ...]
    resident_bytes: int
    streamed_bytes: int
    fixed_bytes: int  # non-streamable tensors (always resident): norms', embeddings, bare params

    @property
    def resident_gb(self) -> float:
        return (self.resident_bytes + self.fixed_bytes) / _BYTES_PER_GB

    @property
    def streamed_gb(self) -> float:
        return self.streamed_bytes / _BYTES_PER_GB

    @property
    def fully_resident(self) -> bool:
        return not self.streamed_names


def plan_residency_split(
    root: nn.Module, resident_budget_gb: float, *, stream_nvfp4: bool = False,
) -> PartialResidencyPlan:
    """Greedily keep a prefix of streamable leaves resident within the budget.

    ``resident_budget_gb`` is the VRAM (in GB) available for *weights* — the
    caller subtracts activation headroom and the min-inference reserve first.
    Non-streamable tensors (norms folded elsewhere, embeddings, bare params) are
    always resident and counted first (``fixed_bytes``); the remaining budget is
    spent on streamable leaves in ``named_modules`` order, and everything that
    doesn't fit streams. A non-positive / tiny budget streams every leaf (only
    the fixed tensors stay resident).
    """
    budget_bytes = max(0, int(resident_budget_gb * _BYTES_PER_GB))

    total_bytes = _module_total_bytes(root)
    leaves = list(iter_streamable_leaves(root, include_nvfp4=stream_nvfp4))
    streamable_bytes = sum(_own_tensor_bytes(m) for _, m in leaves)
    fixed_bytes = total_bytes - streamable_bytes

    remaining = budget_bytes - fixed_bytes
    resident_names: list[str] = []
    streamed_names: list[str] = []
    resident_bytes = 0
    streamed_bytes = 0
    for name, module in leaves:
        size = _own_tensor_bytes(module)
        if size <= remaining:
            resident_names.append(name)
            resident_bytes += size
            remaining -= size
        else:
            streamed_names.append(name)
            streamed_bytes += size

    plan = PartialResidencyPlan(
        resident_names=tuple(resident_names),
        streamed_names=tuple(streamed_names),
        resident_bytes=resident_bytes,
        streamed_bytes=streamed_bytes,
        fixed_bytes=fixed_bytes,
    )
    logger.debug(
        "partial residency: %d/%d leaves resident (%.2fGB resident incl. %.2fGB fixed, "
        "%.2fGB streamed) for budget %.2fGB",
        len(resident_names), len(leaves), plan.resident_gb, fixed_bytes / _BYTES_PER_GB,
        plan.streamed_gb, resident_budget_gb,
    )
    return plan


def _module_total_bytes(root: nn.Module) -> int:
    total = 0
    seen: set[int] = set()
    for t in list(root.parameters()) + list(root.buffers()):
        if t is None or id(t) in seen:
            continue
        seen.add(id(t))
        total += t.numel() * t.element_size()
    return total


class ModuleStreamer:
    """Applies / tears down a :class:`PartialResidencyPlan` on one module.

    Owns the reversible mutation of a component: it moves resident leaves + the
    fixed tensors to the GPU, pins the streamed leaves in CPU RAM, and flips
    ``comfy_cast_weights`` on the streamed leaves so their forward streams. Teardown
    restores every leaf to CPU and reverts the flags to their namespace defaults,
    so a later fully-resident run is unaffected.
    """

    def __init__(
        self,
        root: nn.Module,
        *,
        prefetch: bool | None = None,
        prefetch_depth: int = 1,
        stream_nvfp4: bool = False,
    ) -> None:
        self.root = root
        self._streamed: list[nn.Module] = []
        self._active = False
        self._stream_nvfp4 = stream_nvfp4
        self._staged_leaves: list[nn.Module] = []
        self._stage_handles: list = []
        # OWN-tensor bytes of the streamed leaves and whether they were actually
        # page-locked (``can_pin``). The engine reads ``pinned_gb`` after teardown
        # to decide whether the vacated pinned pool is large enough to be worth
        # releasing back to the OS (the co-tenant-OOM degrade pins ~the whole DiT).
        self._streamed_bytes = 0
        self._pinned = False
        self._pinned_bytes = 0
        self._arenas: list[PinnedArena] = []
        # Prefetch toggle: ``None`` -> read ``$NATIVE_STREAM_PREFETCH`` (default off);
        # an explicit bool overrides (used by tests). ``_prefetcher`` is only built
        # when streaming on a real CUDA device with the toggle on.
        self._prefetch_override = prefetch
        self._prefetch_depth = prefetch_depth
        self._prefetcher: LayerPrefetcher | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def streamed_gb(self) -> float:
        """GB of streamed-leaf weights the last :meth:`apply` placed on the CPU."""
        return self._streamed_bytes / _BYTES_PER_GB

    @property
    def pinned_gb(self) -> float:
        """GB of page-locked host RAM the last :meth:`apply` pinned.

        ``0`` when pinning was unavailable/skipped (no CUDA, ``pin=False``) so
        callers gate a pinned-pool release on a genuine page-locked footprint.
        """
        return self._pinned_bytes / _BYTES_PER_GB if self._pinned else 0.0

    @property
    def prefetcher(self) -> "LayerPrefetcher | None":
        return self._prefetcher

    def _prefetch_enabled(self) -> bool:
        return self._prefetch_override if self._prefetch_override is not None else stream_prefetch_enabled()

    def apply(
        self,
        device: str | torch.device,
        plan: PartialResidencyPlan,
        *,
        pin: bool = True,
        non_blocking: bool = True,
    ) -> None:
        """Place ``root`` for ``plan`` on ``device`` (streamed leaves stay pinned/CPU)."""
        try:
            self._place(device, plan, pin=pin, non_blocking=non_blocking)
            return
        except PinnedMemoryUnavailableError as exc:
            _note_pin_fallback(exc)
        except BaseException:
            self._abort_placement()
            raise
        pinned_before_failure = sum(_own_tensor_bytes(m) for m in self._streamed)
        try:
            self._place(device, plan, pin=False, non_blocking=False)
        except BaseException:
            self._abort_placement()
            raise
        if pinned_before_failure:
            self._release_vacated_pinned(pinned_before_failure)

    def _place(
        self,
        device: str | torch.device,
        plan: PartialResidencyPlan,
        *,
        pin: bool,
        non_blocking: bool,
    ) -> None:
        streamed_ids = {id_ for id_ in map(_module_id_by_name(self.root), plan.streamed_names)}
        can_pin = pin and torch.cuda.is_available()
        self._remove_stagers()
        self._streamed = []
        streamed_bytes = 0
        modules = list(self.root.named_modules())
        if can_pin:
            self._pin_into_arena(
                [m for _, m in modules if id(m) in streamed_ids and not self._stages_buffers(m)], device,
            )
        else:
            self._drop_arenas()
        self._pinned = can_pin
        self._active = True

        for name, module in modules:
            if id(module) in streamed_ids and self._stages_buffers(module):
                _place_streamed_nvfp4(module, device, pin=can_pin)
                module.comfy_cast_weights = True
                module.stream_non_blocking = non_blocking
                self._streamed.append(module)
                self._install_stager(module, non_blocking)
                streamed_bytes += _own_tensor_bytes(module)
            elif id(module) in streamed_ids:
                # Streamed: keep weights on (pinned) CPU RAM; force the cast-on-
                # forward path so each forward copies them to the activation device.
                _move_own_tensors(module, "cpu", pin=can_pin, arenas=self._arenas)
                module.comfy_cast_weights = True
                module.stream_non_blocking = non_blocking
                self._streamed.append(module)
                streamed_bytes += _own_tensor_bytes(module)
            else:
                # Resident (incl. the fixed non-streamable tensors): move to GPU.
                _move_own_tensors(module, device, pin=False)
        # The moves above touch each leaf's own tensors directly, never `root`'s
        # `_apply` -- so a device-derived cache the root (or a submodule) owns
        # in plain attributes (e.g. LTX's RoPE tables) never sees its own
        # `_apply` override fire here and would otherwise carry a stale
        # placement signature into this residency.
        release_derived_caches(self.root)
        self._streamed_bytes = streamed_bytes
        for arena in self._arenas:
            arena.release_unused()
        self._arenas = [arena for arena in self._arenas if arena.registered_segments]
        self._pinned_bytes = self._pinned_footprint() if can_pin else 0
        get_profiler().mark(
            "streamer.apply", streamed_gb=self.streamed_gb, pinned_gb=self.pinned_gb,
            arena_segments=sum(arena.registered_segments for arena in self._arenas),
        )
        self._maybe_setup_prefetch(device, non_blocking)
        logger.debug(
            "streamer: %d streamed leaves on pinned CPU, rest resident on %s "
            "(pin=%s, non_blocking=%s, prefetch=%s)",
            len(self._streamed), device, can_pin, non_blocking, self._prefetcher is not None,
        )

    def _stages_buffers(self, module: nn.Module) -> bool:
        return self._stream_nvfp4 and is_nvfp4_leaf(module)

    def _abort_placement(self) -> None:
        self._streamed_bytes = sum(_own_tensor_bytes(m) for m in self._streamed)
        self._active = True
        try:
            self.teardown()
        except Exception:
            logger.debug("streamer: placement rollback failed", exc_info=True)
            self._active = False

    def _drop_arenas(self) -> None:
        if not self._arenas:
            return
        for module in self.root.modules():
            _unpin_from_arenas(module, self._arenas)
        for arena in self._arenas:
            arena.release()
        self._arenas = []

    def _arena_owning(self, data: torch.Tensor) -> PinnedArena | None:
        return next((arena for arena in self._arenas if arena.owns(data)), None)

    def _pin_into_arena(self, modules: list[nn.Module], device: str | torch.device) -> None:
        global _warned_arena_failed
        params: list[nn.Parameter] = []
        seen: set[int] = set()
        for module in modules:
            for p in module.parameters(recurse=False):
                data = p.data
                if id(p) in seen or data.numel() == 0 or self._arena_owning(data) is not None:
                    continue
                seen.add(id(p))
                if data.device.type == "cpu" and data.is_pinned():
                    continue
                params.append(p)
        if not params:
            return
        arena = PinnedArena(segment_bytes=int(_TEARDOWN_RELEASE_CHUNK_GB * _BYTES_PER_GB), device=device)
        try:
            arena.pin(params)
        except Exception:
            arena.release()
            if not _warned_arena_failed:
                _warned_arena_failed = True
                logger.warning("pinned arena unavailable; pinning streamed weights per tensor", exc_info=True)
            return
        self._arenas.append(arena)

    def _arena_bytes(self) -> int:
        return sum(arena.pinned_bytes for arena in self._arenas)

    def _pinned_footprint(self) -> int:
        total = self._arena_bytes()
        seen: set[int] = set()
        for module in self._streamed:
            tensors = [p.data for p in module.parameters(recurse=False)]
            tensors += [b for b in module.buffers(recurse=False) if b is not None]
            for data in tensors:
                if data.data_ptr() in seen or data.device.type != "cpu" or self._arena_owning(data) is not None:
                    continue
                seen.add(data.data_ptr())
                if data.is_pinned():
                    total += caching_host_block_bytes(data.numel() * data.element_size())
        return total

    def _maybe_setup_prefetch(self, device: str | torch.device, non_blocking: bool) -> None:
        """Construct the copy-stream prefetcher, or leave it ``None`` (on-demand).

        Never constructed unless: prefetch is enabled, there ARE streamed leaves,
        the async pinned path is active (``non_blocking``), and this is a real CUDA
        device. Any construction failure degrades silently to on-demand streaming.
        """
        self._teardown_prefetch()
        if not non_blocking or not self._streamed:
            return
        if not str(device).startswith("cuda") or not torch.cuda.is_available():
            return
        if not self._prefetch_enabled():
            return
        try:
            self._prefetcher = LayerPrefetcher(
                self.root, self._streamed, str(device), prefetch_depth=self._prefetch_depth,
            )
        except Exception:  # noqa: BLE001 - prefetch is an optimisation; never fatal
            global _warned_prefetch_setup_failed
            if not _warned_prefetch_setup_failed:
                _warned_prefetch_setup_failed = True
                logger.warning("stream prefetch setup failed; streaming on demand", exc_info=True)
            self._prefetcher = None

    def _teardown_prefetch(self) -> None:
        if self._prefetcher is not None:
            self._prefetcher.teardown()
            self._prefetcher = None

    def _install_stager(self, module: nn.Module, non_blocking: bool) -> None:
        stager = _BufferStager(non_blocking)
        self._stage_handles.append(module.register_forward_pre_hook(stager.stage))
        self._stage_handles.append(_register_forward_hook_always(module, stager.restore))
        self._staged_leaves.append(module)

    def _remove_stagers(self) -> None:
        for handle in self._stage_handles:
            try:
                handle.remove()
            except Exception:
                logger.debug("streamer: stager hook removal failed", exc_info=True)
        self._stage_handles = []
        self._staged_leaves = []

    def teardown(self) -> None:
        """Move everything back to CPU and revert the streamed flags.

        Each streamed leaf's move allocates a fresh UNPINNED copy of a weight
        whose pinned original then sits unreferenced in CUDA's cached host
        pool until something empties it. Done as one sweep with the release
        at the end (the pre-2026-08-19 shape), a ~20GB DiT transiently holds
        BOTH populations at once — fresh copies stacked on the still-cached
        pool — spiking process RSS by ~model size at the exact moment a
        swapless box has the least headroom (observed: available memory fell
        from 23.7GB to ~4GB and earlyoom killed the backend). So the vacated
        pool is drained AS the sweep progresses: every
        ``_TEARDOWN_RELEASE_CHUNK_GB`` of streamed weights moved, the cached
        pinned blocks freed so far go back to the OS, capping the double-hold
        at one chunk instead of the whole model. Gated on a genuinely pinned
        pool at least one chunk large, so a small streamed tail keeps its
        warm pool for a cheap re-pin (the same trade
        ``_reclaim_host_after_teardown`` makes with the same 2GB floor).
        """
        if not self._active:
            return
        # Remove prefetch hooks + restore any swapped weights BEFORE moving tensors,
        # so the CPU move sees the pinned originals (not a transient GPU staged copy).
        self._teardown_prefetch()
        staged_ids = {id(m) for m in self._staged_leaves}
        self._remove_stagers()
        if self._pinned:
            _release_idle_vram()
        legacy_pinned_bytes = self._pinned_bytes - self._arena_bytes()
        drain = self._pinned and legacy_pinned_bytes >= _TEARDOWN_RELEASE_CHUNK_GB * _BYTES_PER_GB
        streamed_ids = {id(m) for m in self._streamed}
        pending_bytes = 0
        for module in self.root.modules():
            _unpin_from_arenas(module, self._arenas)
            _move_own_tensors(module, "cpu", pin=False)
            if id(module) in staged_ids:
                _unpin_own_buffers(module)
            if drain and id(module) in streamed_ids:
                pending_bytes += _own_tensor_bytes(module)
                if pending_bytes >= _TEARDOWN_RELEASE_CHUNK_GB * _BYTES_PER_GB:
                    self._release_vacated_pinned(pending_bytes)
                    pending_bytes = 0
        if drain and pending_bytes:
            self._release_vacated_pinned(pending_bytes)
        for arena in self._arenas:
            arena.release()
        self._arenas = []
        for module in self._streamed:
            # Revert both flags to their namespace class defaults (e.g.
            # comfy_cast_weights False for plain bf16) so a later fully-resident
            # run is unaffected.
            module.comfy_cast_weights = type(module).comfy_cast_weights
            if "stream_non_blocking" in module.__dict__:
                del module.stream_non_blocking
        self._streamed = []
        self._active = False

    def _release_vacated_pinned(self, drained_bytes: int) -> None:
        """Return the pinned blocks the teardown sweep has vacated so far.

        Split out so tests can observe the drain cadence without a CUDA
        device; failure is contained because a missed release only reverts
        this chunk to the old end-of-teardown behaviour.
        """
        from src.platform.runtime.model_lifecycle.lifecycle import empty_pinned_host_cache

        try:
            empty_pinned_host_cache()
            get_profiler().mark(
                "streamer.chunk_release", drained_gb=round(drained_bytes / _BYTES_PER_GB, 2),
            )
        except Exception:  # pragma: no cover - release is best-effort
            logger.debug("streamer: chunked pinned release failed", exc_info=True)


class LayerPrefetcher:
    """Overlaps a streamed leaf's H2D weight copy with the previous leaf's compute.

    Under partial residency each streamed leaf copies its weight from pinned CPU
    RAM to the GPU *inside* its own forward (``cast_bias_weight`` in
    ``kernels/casting.py``), so the PCIe transfer serialises with compute. This stages leaf N+1's weight on a
    dedicated copy stream while leaf N runs, then hands the already-resident copy
    to N+1's forward — turning a PCIe-bound stream into a mostly compute-bound one.

    **No kernels/arch changes.** The staged GPU weight is swapped into
    ``module.weight.data`` in a forward-pre-hook; the leaf's existing
    ``cast_to(module.weight, dtype, input.device)`` then sees a tensor *already on
    the device* and skips the copy (a plain bf16 leaf becomes a true no-op; an fp8
    leaf still does its on-device dtype cast, but no PCIe). A forward-hook restores
    the CPU-pinned original after the forward, so streaming's VRAM invariant holds.

    **Forward-order discovery is data-driven (option b).** Rather than trust that
    ``named_modules`` order equals execution order (it usually does for these
    sequential DiTs, but data-dependent branches and module reuse can break it),
    the first forward *records* the actual execution order via the leaf pre-hooks
    and prefetches nothing; every subsequent forward prefetches along the recorded
    order. Self-correcting and immune to arch quirks — the first denoise sub-step
    pays no prefetch, steps 2..N benefit. If the observed order ever diverges from
    the recording, prefetch simply misses for that leaf (correctness never depends
    on the prediction).

    **Tensor lifetime under stream semantics.** The staged tensor is allocated on
    the copy stream; before its consumer uses it the compute stream ``wait_event``s
    the copy's event, and the tensor is ``record_stream``-ed onto the compute
    stream so the caching allocator will not reuse its block until the compute
    stream has passed the consuming forward — which is why dropping the Python
    reference in the post-hook (or clearing at the next forward) is safe even
    though the kernels are still in flight.

    **Failure containment.** Any exception on the prefetch path logs once, disables
    the prefetcher for the rest of the session, and degrades to the exact
    on-demand behaviour (the leaf just copies its own weight as before). Never
    crashes a generation.
    """

    def __init__(
        self,
        root: nn.Module,
        streamed_leaves: list[nn.Module],
        device: str,
        *,
        prefetch_depth: int = 1,
    ) -> None:
        self._root = root
        self._leaves = list(streamed_leaves)
        self._device = str(device)
        self._depth = max(1, int(prefetch_depth))
        # Bind the copy stream (and later the consuming current_stream) to the
        # STREAMING target device, not the process's globally-current CUDA device —
        # they can differ on multi-GPU, which would record/wait events on the wrong
        # device and leave cuda:N's copy unordered (or fail record_stream).
        self._copy_stream = _new_cuda_stream(self._device)
        self._order: list[nn.Module] = []            # execution order (recorded forward #1)
        self._next_of: dict[int, nn.Module] = {}     # id(module) -> next streamed module
        self._staged: dict[int, tuple[torch.Tensor, object]] = {}   # id(module) -> (gpu_w, event)
        self._orig_weight: dict[int, torch.Tensor] = {}             # id(module) -> cpu weight
        self._forward_count = 0
        self._recording = True
        self._disabled = False
        self._handles: list = []
        self._max_staged = 0                          # high-water mark (tests / logging)
        self._install_hooks()

    # -- introspection (tests / budget accounting) -------------------------

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def max_staged(self) -> int:
        return self._max_staged

    # -- hook installation / teardown --------------------------------------

    def _install_hooks(self) -> None:
        self._handles.append(self._root.register_forward_pre_hook(lambda m, a: self._on_root_pre()))
        self._handles.append(self._root.register_forward_hook(lambda m, a, o: self._on_root_done()))
        self._handles.append(
            _register_forward_hook_always(self._root, lambda m, a, o: self._on_root_post())
        )
        for leaf in self._leaves:
            self._handles.append(leaf.register_forward_pre_hook(self._make_leaf_pre(leaf)))
            self._handles.append(
                _register_forward_hook_always(leaf, self._make_leaf_post(leaf))
            )

    def teardown(self) -> None:
        """Remove all hooks, restore any swapped weights, drop staged refs + stream."""
        for h in self._handles:
            try:
                h.remove()
            except Exception:  # pragma: no cover - hook handle removal is best-effort
                logger.debug("prefetcher: hook handle removal failed", exc_info=True)
        self._handles = []
        self._restore_all()
        self._staged = {}
        self._copy_stream = None
        self._order = []
        self._next_of = {}

    # -- root-forward boundary ---------------------------------------------

    def _on_root_pre(self) -> None:
        self._recording = self._forward_count == 0
        if self._recording:
            self._order = []
        self._restore_all()
        self._staged = {}

    def _on_root_done(self) -> None:
        if self._forward_count == 0:
            self._next_of = {}
            for cur, nxt in zip(self._order, self._order[1:]):
                self._next_of.setdefault(id(cur), nxt)
            logger.debug("prefetcher: recorded %d streamed leaves in execution order", len(self._order))
        self._forward_count += 1

    def _on_root_post(self) -> None:
        self._staged = {}

    # -- per-leaf choreography ---------------------------------------------

    def _make_leaf_pre(self, leaf: nn.Module):
        return lambda m, a: self._on_leaf_pre(leaf)

    def _make_leaf_post(self, leaf: nn.Module):
        return lambda m, a, o: self._on_leaf_post(leaf)

    def _on_leaf_pre(self, leaf: nn.Module) -> None:
        if self._disabled:
            return
        if self._recording:
            self._order.append(leaf)
            return
        try:
            self._consume(leaf)
            self._prefetch_after(leaf)
        except Exception:  # noqa: BLE001 - a prefetch fault must never fail a generation
            self._on_error()

    def _on_leaf_post(self, leaf: nn.Module) -> None:
        # Always restore (even once disabled): a leaf swapped this forward must get
        # its pinned-CPU weight back so the streaming VRAM invariant holds.
        orig = self._orig_weight.pop(id(leaf), None)
        if orig is not None:
            leaf.weight.data = orig

    def _consume(self, leaf: nn.Module) -> None:
        entry = self._staged.pop(id(leaf), None)
        if entry is None:
            return  # not prefetched (first leaf, or its predecessor was skipped) -> on-demand copy
        gpu_w, event = entry
        compute = _current_cuda_stream(self._device)
        recorder = stream_wait.recorder
        waited_from = recorder.mark(compute) if recorder is not None else None
        compute.wait_event(event)          # compute must not read the weight until the copy is done
        if waited_from is not None:
            recorder.span(waited_from, recorder.mark(compute))
        _record_stream_on(gpu_w, compute)  # defer allocator reuse until compute passes this point
        self._orig_weight[id(leaf)] = leaf.weight.data
        leaf.weight.data = gpu_w

    def _prefetch_after(self, leaf: nn.Module) -> None:
        nxt = self._next_of.get(id(leaf))
        if nxt is None or id(nxt) in self._staged:
            return
        if len(self._staged) >= self._depth:   # hard staging-budget cap
            return
        weight = getattr(nxt, "weight", None)
        if weight is None:
            return
        with _cuda_stream_ctx(self._copy_stream):
            gpu_w = _stage_copy(weight, self._device, non_blocking=True)
            event = _new_cuda_event()
            event.record(self._copy_stream)
        self._staged[id(nxt)] = (gpu_w, event)
        self._max_staged = max(self._max_staged, len(self._staged))
        if stream_wait.recorder is not None:
            stream_wait.recorder.add_bytes(weight.numel() * weight.element_size())

    # -- failure / cleanup -------------------------------------------------

    def _on_error(self) -> None:
        global _warned_prefetch_failed
        if not _warned_prefetch_failed:
            _warned_prefetch_failed = True
            logger.warning("stream prefetch failed; disabling for this session", exc_info=True)
        self._disabled = True
        self._restore_all()
        self._staged = {}

    def _restore_all(self) -> None:
        for leaf_id, orig in list(self._orig_weight.items()):
            for leaf in self._leaves:
                if id(leaf) == leaf_id:
                    leaf.weight.data = orig
                    break
        self._orig_weight = {}


def _module_id_by_name(root: nn.Module):
    """Return a callable name -> id(module) for ``root``'s named modules."""
    lookup = {name: id(module) for name, module in root.named_modules()}
    return lambda name: lookup.get(name, 0)


class _BufferStager:
    def __init__(self, non_blocking: bool) -> None:
        self._non_blocking = non_blocking
        self._saved: dict[str, torch.Tensor] = {}

    def stage(self, module: nn.Module, args) -> None:
        self._put_back(module)
        x = args[0] if args else None
        if not isinstance(x, torch.Tensor):
            return
        target = x.device
        for name, buf in list(module._buffers.items()):
            if buf is None or buf.device == target:
                continue
            self._saved[name] = buf
            module._buffers[name] = _stage_copy(buf, target, non_blocking=self._non_blocking)

    def restore(self, module: nn.Module, args, output) -> None:
        self._put_back(module)

    def _put_back(self, module: nn.Module) -> None:
        for name, buf in self._saved.items():
            module._buffers[name] = buf
        self._saved = {}


def _place_streamed_nvfp4(module: nn.Module, device: str | torch.device, *, pin: bool) -> None:
    for p in module.parameters(recurse=False):
        p.data = p.data.to(device)
    placed: dict[str, torch.Tensor] = {}
    for name, buf in list(module._buffers.items()):
        if buf is None:
            continue
        data = buf if buf.device.type == "cpu" else buf.to("cpu")
        if pin and not data.is_pinned():
            data = _pin(data)
        elif not pin and data.is_pinned():
            data = _unpinned_copy(data)
        placed[name] = data
    module._buffers.update(placed)


def _unpin_own_buffers(module: nn.Module) -> None:
    for name, buf in list(module._buffers.items()):
        if buf is None or buf.device.type != "cpu" or not buf.is_pinned():
            continue
        module._buffers[name] = _unpinned_copy(buf)


def _unpin_from_arenas(module: nn.Module, arenas: list[PinnedArena]) -> None:
    if not arenas:
        return
    for p in module.parameters(recurse=False):
        for arena in arenas:
            if arena.unpin(p):
                break


def _move_own_tensors(
    module: nn.Module, device: str | torch.device, *, pin: bool, arenas: Sequence[PinnedArena] = (),
) -> None:
    """Move a module's OWN params/buffers to ``device`` (never its children).

    Leaf-granular so a partial plan never has to materialise the whole model on
    the GPU at once. When ``pin`` and the target is CPU, streamed weights land in
    pinned host memory so their per-forward H2D copy can be ``non_blocking``.
    """
    is_cpu = str(device) == "cpu"
    for p in module.parameters(recurse=False):
        data = p.data
        if is_cpu:
            if pin and any(arena.owns(data) for arena in arenas):
                continue
            if pin and data.device.type == "cpu" and not data.is_pinned():
                data = _pin(data)
            elif data.device.type != "cpu":
                data = data.to("cpu")
            elif not pin and data.is_pinned():
                # Teardown / resident-placement path (pin=False) on a tensor
                # that is ALREADY pinned: it reports device.type == "cpu", so
                # neither branch above fires and a bare reassignment is a
                # no-op -- the page-locked (unswappable) allocation stays
                # resident for the module's whole life. Replace it with a
                # fresh non-pinned copy and drop the old reference so the
                # pinned pages are actually freed. Gated on ``not pin`` so a
                # re-``apply(pin=True)`` on an already-pinned streamed leaf
                # (no teardown in between) leaves it pinned, as requested.
                unpinned = torch.empty_like(data, pin_memory=False)
                unpinned.copy_(data)
                data = unpinned
        else:
            data = data.to(device)
        p.data = data
    for name, buf in list(module._buffers.items()):
        if buf is None:
            continue
        module._buffers[name] = buf.to(device)
