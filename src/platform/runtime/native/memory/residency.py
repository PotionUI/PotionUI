"""Process-global GPU residency coordinator (ComfyUI ``load_models_gpu`` style).

The native engine loads each heavy component (DiT / text encoder / VAE) under its
own ``MODELS`` cache key and moves it to the GPU only for the phase that needs it.
But the phases run in *different pipes*: ``prompt_encoder`` encodes (needs the TE
on the GPU) before ``generator/*`` samples (needs the DiT on the GPU), and once a
generation leaves the DiT resident on the GPU, the next generation's encode would
have to squeeze the TE in beside a 24GB DiT — or silently fall back to a CPU
encode (the bug this fixes).

This coordinator is the missing seam: every ``NativeModel`` that moves to a CUDA
device registers here, and a phase that is about to claim VRAM calls
:meth:`ensure_free`, which offloads the least-recently-used resident component(s)
back to CPU/RAM until the request fits — exactly ComfyUI's "free memory then
load" behaviour, scoped to the native engine. It is deliberately *estimate
driven* (offload by known component sizes) so it is deterministic and unit
testable without a GPU; the caller empties the CUDA cache once afterwards to hand
the freed blocks back to the allocator.
"""

from __future__ import annotations

import itertools
import logging
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import torch

from src.platform.runtime.vram_cap import apply_vram_cap_bytes

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1024 ** 3

# Set once a bad NATIVE_MIN_INFERENCE_MEMORY_GB value has been warned about -
# the env var doesn't change mid-process, so re-parsing it on every call would
# otherwise re-log the same warning every generation.
_warned_bad_min_inference_memory_env = False


def device_index(device: str | torch.device) -> int:
    """CUDA ordinal for a device string (``"cuda"`` -> 0, ``"cuda:1"`` -> 1)."""
    s = str(device)
    if ":" in s:
        try:
            return int(s.split(":", 1)[1])
        except ValueError:  # pragma: no cover - malformed device string
            return 0
    return 0


def module_size_gb(module: Any) -> float:
    """Estimated resident size (params + buffers) of an ``nn.Module`` in GB.

    Sums the underlying ``nn.Module`` of a native wrapper: a ``NativeModel``
    exposes ``.module``; a composite text encoder (Flux1 = T5 + CLIP-L) exposes
    ``.t5`` / ``.clip_l`` each wrapping a ``.module``. Returns 0.0 for anything
    with no discoverable parameters (so it never blocks an offload decision).
    """
    seen: set[int] = set()
    total = 0

    def module_bytes(m: Any) -> int:
        inner = getattr(m, "module", m)
        if not isinstance(inner, torch.nn.Module) or id(inner) in seen:
            return 0
        seen.add(id(inner))
        tensors = list(inner.parameters()) + list(inner.buffers())
        return sum(t.numel() * t.element_size() for t in tensors)

    for attr in ("module", "t5", "clip_l"):
        sub = getattr(module, attr, None)
        if sub is not None:
            total += module_bytes(sub)
    if total == 0:
        total = module_bytes(module)
    return total / _BYTES_PER_GB


def _weights_gb_by_device(wrapper: Any) -> dict[str, float]:
    """Per-device byte census (GB) of every real parameter/buffer backing
    ``wrapper``.

    A single sentinel tensor's ``.device`` (the first cut of this check) can
    read "cuda:0" while the bulk of a large composite module is still on CPU —
    e.g. a small embedding or norm moved but the 48 transformer blocks behind
    it didn't — which would look identical to a correct move in a mark that
    only samples one tensor. Summing bytes per device instead makes a partial
    move impossible to hide: a healthy co-resident encode reports
    ``{"cuda:0": <full size>}``; a partial-move regression reports both
    ``"cuda:0"`` and ``"cpu"`` with the CPU share carrying the bulk of the
    bytes. Empty dict when no real module/tensor is discoverable.
    """
    totals: dict[str, int] = {}
    seen: set[int] = set()
    for real in _iter_real_modules(wrapper):
        for t in itertools.chain(real.parameters(), real.buffers()):
            if t is None or id(t) in seen:
                continue
            seen.add(id(t))
            dev = str(t.device)
            totals[dev] = totals.get(dev, 0) + t.numel() * t.element_size()
    return {dev: n / _BYTES_PER_GB for dev, n in totals.items()}


def _iter_real_modules(wrapper: Any) -> list[torch.nn.Module]:
    """Discover the actual ``nn.Module``(s) backing a native TE wrapper.

    Mirrors :func:`module_size_gb`'s discovery walk: a single-role wrapper
    exposes ``.module``; a composite (Flux1 = T5 + CLIP-L) exposes ``.t5``/
    ``.clip_l``, each in turn wrapping a ``.module``. Falls back to treating
    ``wrapper`` itself as the module when it already is one. Used so
    ``run_text_encode`` can move every real module in place even if some
    wrapper's own ``.to()`` fails to cascade to one of its parts.
    """
    seen: set[int] = set()
    found: list[torch.nn.Module] = []

    def add(m: Any) -> None:
        inner = getattr(m, "module", m)
        if isinstance(inner, torch.nn.Module) and id(inner) not in seen:
            seen.add(id(inner))
            found.append(inner)

    for attr in ("module", "t5", "clip_l"):
        sub = getattr(wrapper, attr, None)
        if sub is not None:
            add(sub)
    if not found:
        add(wrapper)
    return found


@dataclass
class _Entry:
    model_ref: "weakref.ref[Any]"
    device: str
    size_gb: float
    last_used: float
    # Set by mark_orphaned() when ModelLifecycleManager evicted this model's
    # cache entry but could not unload it immediately (something else held a
    # reference at that moment - see manager.py's _evict_entry A3 safety).
    # A later reclaim (ensure_free/offload_all) re-checks the refcount for an
    # orphaned entry and fully unloads it if the stale holder has since let
    # go, instead of unconditionally offloading it to host RAM where it would
    # sit forever with no cache entry pointing at it.
    orphaned: bool = False


class OffloadResult(list):
    """``list[Any]`` of components :meth:`GpuResidencyManager.offload_all`
    actually offloaded, plus the stats a caller reporting on the run (e.g. the
    Clear VRAM admin action) needs. Subclasses ``list`` so every existing
    truthiness/``len()``/membership/equality check on the return value keeps
    working unchanged.
    """

    def __init__(self, models: Iterable[Any] = (), *, freed_gb: float = 0.0, failed: Iterable[Any] = ()) -> None:
        super().__init__(models)
        self.freed_gb = freed_gb
        self.failed = list(failed)


class GpuResidencyManager:
    """Tracks GPU-resident native components and evicts LRU to free VRAM.

    Holds each component via ``weakref.ref`` — this registry must never be an
    ownership root. When ``ModelLifecycleManager`` drops a cache entry without
    an explicit unload (the refcount>2 fast path), the model's last strong
    reference dies with it; a strong-ref entry here would keep the whole
    GPU-resident component (a multi-GB DiT) alive forever, invisible to every
    eviction path. Dead refs are pruned opportunistically wherever entries
    are read.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[int, _Entry] = {}

    # -- registration (driven by NativeModel.move_to / offload) -------------

    def note_resident(self, model: Any, device: str | torch.device, size_gb: float) -> None:
        """Record that ``model`` now holds VRAM on ``device`` (cuda only)."""
        if not str(device).startswith("cuda"):
            self.note_offloaded(model)
            return
        with self._lock:
            self._prune_dead()
            self._entries[id(model)] = _Entry(
                weakref.ref(model), str(device), float(size_gb or 0.0), time.monotonic()
            )

    def note_offloaded(self, model: Any) -> None:
        """Record that ``model`` no longer holds VRAM."""
        with self._lock:
            self._entries.pop(id(model), None)

    def touch(self, model: Any) -> None:
        """Mark ``model`` as most-recently-used (so it is evicted last)."""
        with self._lock:
            entry = self._entries.get(id(model))
            if entry is not None:
                entry.last_used = time.monotonic()

    def mark_orphaned(self, model: Any) -> None:
        """Flag a GPU-resident component whose owning cache entry was just
        evicted (fingerprint bust / preset switch) while still referenced
        elsewhere, so ``ModelLifecycleManager`` could not unload it (see
        ``_evict_entry``'s A3 safety - refcount>2 at eviction time). No-op if
        ``model`` isn't currently tracked (not GPU-resident, or already pruned).

        This does NOT unload anything itself - it only marks the entry so the
        next reclaim (``ensure_free``/``offload_all``) re-checks whether the
        stale holder has since released, and fully frees the model instead of
        parking it in host RAM forever with no cache entry left to reference
        it (exactly this sat at "final_rss 31.98GB" after a LoRA swap orphaned
        the old DiT).

        Also registers a ``weakref.finalize`` on ``model`` regardless of
        whether it's currently tracked here. This is the ONLY reliable
        catch-all: ``NativeModel.offload()`` calls ``note_offloaded()``, which
        removes the entry from ``_entries`` entirely - so once an orphaned
        entry has been offloaded once (exactly what the production evidence
        showed: TE-encode's ``ensure_free`` offloaded the orphaned DiT to host
        RAM), there is no SECOND chance for a later ``_reclaim`` call to catch
        it, because it is no longer GPU-resident/tracked at all. The
        finalizer has no such timing dependency - it fires exactly once,
        whenever the object's actual last reference (wherever it lives) goes
        away, and guarantees a host-allocator trim happens then, closing the
        "the freed multi-GB weights never get trimmed" gap regardless of
        which code path or how much later that release happens.
        """
        with self._lock:
            entry = self._entries.get(id(model))
            if entry is not None:
                entry.orphaned = True

        def _on_finally_released() -> None:
            try:
                from src.platform.runtime.model_lifecycle.manager import trim_host_allocator

                trim_host_allocator()
                logger.info(
                    "residency: an orphaned model's last reference was finally released; "
                    "trimmed host allocator"
                )
            except Exception:
                logger.debug("residency: post-orphan-release trim failed", exc_info=True)

        weakref.finalize(model, _on_finally_released)

    def _reclaim(self, model: Any, entry: "_Entry") -> None:
        """Free ``model``'s GPU residency: fully ``unload()`` an orphaned
        entry whose stale holder has (by now) actually released it, otherwise
        ``offload()`` to host RAM as before (a live holder may still need the
        weights - never unload out from under an active user).

        The refcount check mirrors ``ModelLifecycleManager._evict_entry``'s
        established convention (a cache-only value shows refcount==2 there:
        the local var + ``sys.getrefcount``'s own transient arg) with one more
        added for the local ``model`` parameter this method itself holds -
        measured empirically alongside the caller's own local reference during
        iteration. Never raises: a failed unload attempt falls back to the
        always-safe offload path.
        """
        if entry.orphaned:
            refcount = sys.getrefcount(model)
            if refcount <= 3:
                try:
                    model.unload()
                    from src.platform.runtime.model_lifecycle.manager import trim_host_allocator

                    trim_host_allocator()
                    logger.info(
                        "residency: orphaned %s (~%.1fGB) fully unloaded - stale holder had "
                        "released (refcount=%d)", type(model).__name__, entry.size_gb, refcount,
                    )
                    return
                except Exception:  # pragma: no cover - best-effort eviction
                    logger.debug("residency: orphaned unload failed; falling back to offload", exc_info=True)
            else:
                logger.debug(
                    "residency: orphaned %s still referenced (refcount=%d); offloading, not unloading",
                    type(model).__name__, refcount,
                )
        model.offload()

    def resident_gb(self, device: str | torch.device | None = None) -> float:
        idx = device_index(device) if device is not None else None
        with self._lock:
            self._prune_dead()
            return sum(
                e.size_gb for e in self._entries.values()
                if idx is None or device_index(e.device) == idx
            )

    def _prune_dead(self) -> None:
        """Drop entries whose model has already been garbage-collected.

        Must be called while holding ``self._lock``.
        """
        dead = [key for key, e in self._entries.items() if e.model_ref() is None]
        for key in dead:
            self._entries.pop(key, None)

    # -- eviction -----------------------------------------------------------

    def ensure_free(
        self,
        device: str | torch.device,
        need_gb: float,
        current_free_gb: float,
        *,
        exclude: Iterable[Any] = (),
    ) -> list[Any]:
        """Offload LRU resident components on ``device`` until ``need_gb`` fits.

        ``current_free_gb`` is the VRAM free *now* (queried once by the caller);
        this method offloads registered components — least-recently-used first —
        until ``current_free_gb + freed_estimate >= need_gb`` or nothing evictable
        remains. Components in ``exclude`` are never offloaded. Returns the list of
        components it offloaded (already moved to CPU) so the caller can empty the
        CUDA cache once. Estimate-driven and GPU-free for testability.
        """
        idx = device_index(device)
        exclude_ids = {id(m) for m in exclude}
        offloaded: list[Any] = []
        if current_free_gb >= need_gb:
            return offloaded

        with self._lock:
            self._prune_dead()
            # Sort candidate KEYS only - deliberately not a parallel list of
            # (key, entry, model) tuples (measured: materializing model refs
            # into a comprehension/sorted() result inflates sys.getrefcount by
            # several - the sort's internal list, the generator's frame, the
            # tuple itself - none of which are a genuine external holder, which
            # would make _reclaim's orphaned-refcount check meaningless). Popping
            # the entry and dereferencing the weakref fresh, right before the
            # reclaim call, keeps the refcount check clean: only the reference
            # this loop body itself holds, matching ModelLifecycleManager.
            # _evict_entry's identical, verified convention.
            keys = sorted(
                (key for key, e in self._entries.items()
                 if device_index(e.device) == idx and key not in exclude_ids),
                key=lambda k: self._entries[k].last_used,
            )
            freed = 0.0
            for key in keys:
                if current_free_gb + freed >= need_gb:
                    break
                entry = self._entries.pop(key, None)
                if entry is None:
                    continue
                model = entry.model_ref()
                if model is None:
                    continue
                try:
                    self._reclaim(model, entry)
                except Exception:  # pragma: no cover - best-effort eviction
                    logger.debug("residency: reclaim of %r failed", model, exc_info=True)
                offloaded.append(model)
                freed += entry.size_gb
                logger.debug(
                    "residency: reclaimed %s (~%.1fGB) to free room (need %.1fGB, had %.1fGB free)",
                    type(model).__name__, entry.size_gb, need_gb, current_free_gb,
                )
                del model
        return offloaded

    def offload_all(self, device: str | torch.device, *, exclude: Iterable[Any] = ()) -> "OffloadResult":
        """Offload EVERY resident component on ``device`` except ``exclude``.

        The correctness-first fallback for when the caller can't estimate its VRAM
        need (a NativeModel with no ``estimated_vram_gb``): rather than guess, free
        all foreign residents. ``exclude`` is the calling generation's OWN models,
        which must never be evicted out from under it.

        Returns an :class:`OffloadResult` — a ``list[Any]`` of what it offloaded
        (existing callers that only check truthiness/length/membership keep
        working unchanged) that also carries ``freed_gb``/``failed`` so a caller
        that needs to report "did this actually do anything" (e.g. the Clear VRAM
        admin action) doesn't have to re-derive it from CUDA's before/after
        allocated bytes.
        """
        idx = device_index(device)
        exclude_ids = {id(m) for m in exclude}
        offloaded: list[Any] = []
        freed_gb = 0.0
        failed: list[Any] = []
        with self._lock:
            self._prune_dead()
            # See ensure_free's identical comment: keys only, entry popped and
            # the weakref dereferenced fresh right before _reclaim, so its
            # orphaned-refcount check sees only this loop body's own reference.
            keys = [
                key for key, e in self._entries.items()
                if device_index(e.device) == idx and key not in exclude_ids
            ]
            for key in keys:
                entry = self._entries.pop(key, None)
                if entry is None:
                    continue
                model = entry.model_ref()
                if model is None:
                    continue
                try:
                    self._reclaim(model, entry)
                except Exception:  # pragma: no cover - best-effort eviction
                    logger.warning("residency: reclaim of %r failed; leaving it resident", model, exc_info=True)
                    failed.append(model)
                    del model
                    continue
                offloaded.append(model)
                freed_gb += entry.size_gb
                del model
        # Always log what happened, including the zero case - a silent no-op
        # here is exactly what makes "Clear VRAM ran but nothing changed"
        # unobservable from the logs.
        if offloaded:
            logger.info(
                "residency: offloaded %d component(s) on %s (~%.1fGB)%s",
                len(offloaded), device, freed_gb,
                f"; {len(failed)} failed" if failed else "",
            )
        elif failed:
            logger.info("residency: nothing offloaded on %s (%d failed)", device, len(failed))
        else:
            logger.info("residency: nothing resident to offload on %s", device)
        return OffloadResult(offloaded, freed_gb=freed_gb, failed=failed)

    def clear(self) -> None:
        """Drop all tracking (does not move anything). For tests."""
        with self._lock:
            self._entries.clear()


# Process-wide singleton (mirrors ModelLifecycleManager's module-level default).
_manager: GpuResidencyManager | None = None


def get_residency_manager() -> GpuResidencyManager:
    global _manager
    if _manager is None:
        _manager = GpuResidencyManager()
    return _manager


class ClearVramResult:
    """Everything a "Clear VRAM" caller (the admin quick action, the
    automation node) needs to report: what the residency ledger offloaded,
    plus what the lifecycle-cache fallback swept up on top of it.

    ``offloaded_count``/``freed_gb``/``failed_count`` fold both sources
    together for a caller that only wants one number; ``swept_count`` is
    broken out separately for a caller that wants to show *how much* of the
    total came from the fallback sweep specifically (a component the
    residency ledger never knew about).
    """

    def __init__(
        self, offloaded: Iterable[Any], swept: Iterable[Any],
        *, freed_gb: float = 0.0, failed: Iterable[Any] = (),
    ) -> None:
        self.offloaded = list(offloaded)
        self.swept = list(swept)
        self.freed_gb = freed_gb
        self.failed = list(failed)

    @property
    def offloaded_count(self) -> int:
        return len(self.offloaded) + len(self.swept)

    @property
    def swept_count(self) -> int:
        return len(self.swept)

    @property
    def failed_count(self) -> int:
        return len(self.failed)


def clear_vram(device: str | torch.device, lifecycle_manager: Any = None) -> ClearVramResult:
    """Offload every GPU-resident component on ``device`` — the shared "Clear
    VRAM" implementation behind both the admin quick action
    (``BackendController.clear_backend_vram``) and the "backend_action"
    automation node (``_execute_backend_action``).

    Two layers:

      1. ``GpuResidencyManager.offload_all(device, exclude=leased)`` — every
         component the ledger was told about, except one leased by an
         in-flight generation (``lifecycle_manager.leased_values()``).
      2. A registration-gap-proof fallback: ``lifecycle_manager.cached_values()``
         is swept for anything GPU-resident that the ledger never saw (a
         placement path that forgot to register), skipping anything leased
         or already offloaded by step 1 (which reads back as CPU-resident by
         the time this runs, so it is never double-counted or double-freed).

    ``lifecycle_manager`` is duck-typed (``leased_values()``, ``cached_values()``,
    and each cached value optionally exposing ``.device``/``.offload()``/
    ``.estimated_vram_gb``) so this module stays decoupled from
    ``ModelLifecycleManager``'s concrete type. ``None`` skips both the lease
    exclusion and the sweep — offload_all still runs unconditionally.
    """
    manager = get_residency_manager()
    leased_values = list(lifecycle_manager.leased_values()) if lifecycle_manager is not None else []
    result = manager.offload_all(device, exclude=leased_values)

    leased_ids = {id(v) for v in leased_values}
    swept: list = []
    swept_gb = 0.0
    swept_failed: list = []
    if lifecycle_manager is not None:
        for value in lifecycle_manager.cached_values():
            if id(value) in leased_ids:
                continue
            model_device = getattr(value, "device", None)
            if model_device is None or not str(model_device).startswith("cuda"):
                continue
            offload = getattr(value, "offload", None)
            if not callable(offload):
                continue
            size_gb = float(getattr(value, "estimated_vram_gb", None) or 0.0)
            try:
                offload()
            except Exception:
                logger.warning("clear_vram: lifecycle-cache offload failed for %r", value, exc_info=True)
                swept_failed.append(value)
                continue
            swept.append(value)
            swept_gb += size_gb

    return ClearVramResult(
        list(result), swept,
        freed_gb=result.freed_gb + swept_gb,
        failed=list(result.failed) + swept_failed,
    )


def free_vram_gb(device: str | torch.device) -> float | None:
    """Free VRAM (GB) on ``device``, or ``None`` when it can't be queried.

    Subject to the ``POTIONUI_VRAM_CAP_GB`` rig-simulation cap (see
    ``src.platform.runtime.vram_cap``) - a no-op when that env var is unset.
    """
    if not str(device).startswith("cuda") or not torch.cuda.is_available():
        return None
    try:
        free, total = torch.cuda.mem_get_info(device_index(device))
        free, _total = apply_vram_cap_bytes(free, total)
        return free / _BYTES_PER_GB
    except Exception:  # pragma: no cover - device query is best-effort
        logger.debug("residency: mem_get_info failed", exc_info=True)
        return None


def effective_free_vram_gb(device: str | torch.device) -> float | None:
    """Free VRAM (GB) on ``device`` as it will be immediately after an
    ``empty_cache()`` — i.e. what's ACTUALLY available for a new resident
    component, not just what ``mem_get_info`` reports right now.

    ``torch.cuda.mem_get_info`` reports the device's raw free memory, but our
    own caching allocator can be holding a large "reserved but not currently
    allocated" pool left over from a just-finished phase (e.g. a decode's
    activation buffers) that hasn't been handed back to the driver yet —
    ``mem_get_info`` counts that pool as used, even though nothing is
    actually using it and a subsequent ``move_to()``/``cudaMalloc`` would
    happily reuse or reclaim it. A fits-check that only asks
    ``mem_get_info`` therefore under-reports free VRAM right after a
    memory-heavy phase — exactly the shape of the
    ``dit_restore.skip reason=insufficient_free_vram`` firing with
    free_gb=16.956/need_gb=24.297 right after decode, on a card that had
    enough room once the idle caching-allocator pool was counted.

    Effective free = raw free (``mem_get_info``) + (this process's reserved -
    allocated) — the idle, giveable-back slice of OUR OWN caching allocator
    pool. Never raises; returns ``None`` on any failure or when CUDA/the
    device isn't available (mirrors :func:`free_vram_gb`).
    """
    if not str(device).startswith("cuda") or not torch.cuda.is_available():
        return None
    try:
        idx = device_index(device)
        free, total = torch.cuda.mem_get_info(idx)
        free, _total = apply_vram_cap_bytes(free, total)
        reserved = torch.cuda.memory_reserved(idx)
        allocated = torch.cuda.memory_allocated(idx)
        idle = max(0, reserved - allocated)
        return (free + idle) / _BYTES_PER_GB
    except Exception:  # pragma: no cover - device query is best-effort
        logger.debug("residency: effective_free_vram_gb query failed", exc_info=True)
        return None


def total_vram_gb(device: str | torch.device) -> float | None:
    """Total VRAM (GB) on ``device``, or ``None`` when it can't be queried.

    Deterministic (unlike free VRAM) — use for decisions that must be stable
    across runs, e.g. the fp8 quantise-at-load gate: gating on load-moment
    free VRAM made the same checkpoint quantise or not depending on what else
    happened to sit on the GPU, which also poisoned the MODELS cache (the
    fingerprint doesn't encode the weather-dependent decision)."""
    if not str(device).startswith("cuda") or not torch.cuda.is_available():
        return None
    try:
        free, total = torch.cuda.mem_get_info(device_index(device))
        _free, total = apply_vram_cap_bytes(free, total)
        return total / _BYTES_PER_GB
    except Exception:  # pragma: no cover - device query is best-effort
        logger.debug("residency: mem_get_info failed", exc_info=True)
        return None


# Multiplier on the text encoder's weight size to reserve for its (fp32)
# activation transients when EVICTING to make room (the pessimistic path).
_TE_ACTIVATION_FACTOR = 1.5


def minimum_inference_memory_gb() -> float:
    """VRAM (GB) kept free on top of the resident weights — ComfyUI's
    ``minimum_inference_memory`` analog. Env-overridable via
    ``NATIVE_MIN_INFERENCE_MEMORY_GB`` because the production server sometimes
    shares ``cuda:0`` with a large already-resident model and needs a bigger
    reserve to leave headroom for its inference."""
    import os

    raw = os.environ.get("NATIVE_MIN_INFERENCE_MEMORY_GB")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:  # pragma: no cover - bad env value
            global _warned_bad_min_inference_memory_env
            if not _warned_bad_min_inference_memory_env:
                _warned_bad_min_inference_memory_env = True
                logger.warning("residency: ignoring non-numeric NATIVE_MIN_INFERENCE_MEMORY_GB=%r", raw)
    return 1.0


def run_text_encode(
    encoder: Any,
    device: str | torch.device,
    encode_fn: "Callable[[], Any]",
    *,
    reserve_gb: float | None = None,
    cache_key: str | None = None,
) -> Any:
    """Encode ``encode_fn()`` on ``device``, memoising the result by ``cache_key``.

    Thin caching wrapper over :func:`_run_text_encode_uncached` (the residency
    machinery that moves ``encoder`` to the GPU, encodes, and moves it back). When
    ``cache_key`` is provided and the process-global :class:`PromptEmbedCache` has
    a matching entry, the cached CPU embeddings are materialised on ``device`` and
    returned **without touching the encoder at all** — the whole GPU co-reside /
    evict dance is skipped, which is the point of the cache in a seed-iterate loop.
    On a miss the uncached path runs and its output is stored as detached CPU
    clones. ``cache_key=None`` (the default) bypasses the cache entirely — the
    contract callers use for image-conditioned encodes or encoders with no stable
    fingerprint (see :func:`prompt_embed_key`).
    """
    if cache_key is not None:
        from src.platform.runtime.native.text_encoders.embed_cache import get_prompt_embed_cache
        from src.platform.observability.profiling import get_profiler

        cache = get_prompt_embed_cache()
        cached = cache.get_on_device(cache_key, str(device))
        if cached is not None:
            get_profiler().mark("te.encode", device=str(device), path="embed-cache-hit")
            return cached
        result = _run_text_encode_uncached(encoder, device, encode_fn, reserve_gb=reserve_gb)
        cache.put(cache_key, result)
        return result
    return _run_text_encode_uncached(encoder, device, encode_fn, reserve_gb=reserve_gb)


def run_text_encode_batch(
    encoder: Any,
    device: str | torch.device,
    encode_fns: "list[Callable[[], Any]]",
    *,
    reserve_gb: float | None = None,
    cache_keys: "list[str | None] | None" = None,
) -> list:
    """Run N independent ``encode_fns`` under at most ONE GPU-resident window.

    The multi-request analog of :func:`run_text_encode`: a storyboard's
    worth of prompts each need ``encoder`` on the GPU, but calling
    :func:`run_text_encode` once per request pays for a full co-reside/evict
    placement decision AND a CPU<->GPU move N times, when the encoder itself
    only needs to be resident once for however many requests actually miss the
    embed cache. This is the LOW-RISK batching variant: still one
    ``encode_fns[i]()`` call per request (no padding/attention-mask batched
    forward) — only the *placement cadence* changes.

    Per-item cache semantics are byte-identical to calling
    ``run_text_encode(encoder, device, encode_fns[i], cache_key=cache_keys[i])``
    for each ``i`` in isolation: a ``None`` key is always a miss and is never
    stored; a hit is served straight from :class:`PromptEmbedCache` and its
    ``encode_fns[i]`` is never called, so an all-hit batch touches the encoder
    (and the GPU) not at all. Only the misses are executed, in order, inside
    ONE shared window.

    ``cache_keys=None`` (the default) treats every item as uncacheable, mirroring
    ``run_text_encode(cache_key=None)``.
    """
    from src.platform.runtime.native.text_encoders.embed_cache import get_prompt_embed_cache
    from src.platform.observability.profiling import get_profiler

    n = len(encode_fns)
    if cache_keys is None:
        cache_keys = [None] * n
    elif len(cache_keys) != n:
        raise ValueError("cache_keys must be the same length as encode_fns")

    cache = get_prompt_embed_cache()
    results: list = [None] * n
    miss_indices: list[int] = []
    for i, key in enumerate(cache_keys):
        cached = cache.get_on_device(key, str(device)) if key is not None else None
        if cached is not None:
            results[i] = cached
        else:
            miss_indices.append(i)

    if not miss_indices:
        if n:
            get_profiler().mark("te.encode", device=str(device), path="embed-cache-hit", count=n)
        return results

    def _run_misses() -> list:
        return [encode_fns[i]() for i in miss_indices]

    miss_results = _run_text_encode_uncached(
        encoder, device, _run_misses, reserve_gb=reserve_gb, count=len(miss_indices),
    )
    for idx, result in zip(miss_indices, miss_results):
        results[idx] = result
        key = cache_keys[idx]
        if key is not None:
            cache.put(key, result)
    return results


def _run_text_encode_uncached(
    encoder: Any,
    device: str | torch.device,
    encode_fn: "Callable[[], Any]",
    *,
    reserve_gb: float | None = None,
    count: int | None = None,
) -> Any:
    """Run ``encode_fn()`` with ``encoder`` on ``device`` instead of the CPU.

    The native text encoders are loaded on the CPU; without this, every prompt
    encode would run a multi-billion-parameter transformer on the CPU in fp32 — a
    big chunk of a native generation's first pass. This moves the encoder to the
    GPU for the encode, then back to CPU, using ComfyUI's ``load_models_gpu``
    policy: **co-reside first, evict only on pressure.**

      1. If the encoder's weights + a ``minimum_inference_memory`` reserve already
         fit in the **live** free VRAM (``mem_get_info`` — accounts for memory a
         co-tenant process holds, not just our tracked models), run it *beside*
         whatever is resident. This avoids a ~15GB DiT offload→reload ping-pong
         (measured ~15s on a 5090) when the ~4GB TE fits next to a resident 24.5GB
         DiT — which it does on a 32GB card.
      2. Only if it doesn't fit (or the co-resident attempt OOMs) do we offload
         LRU-resident components (the DiT) to RAM and retry on the GPU.
      3. If it STILL doesn't fit, fall back to a CPU encode — slow but correct,
         never a crash.

    Takes ``encode_fn`` (not a context manager) so it can retry the encode after
    freeing VRAM. On a CPU / no-CUDA device it just calls ``encode_fn()``.

    ``encoder.to(device)`` is trusted to move the wrapper's own weights, but a
    wrapper's ``.to()`` is caller-authored and may not cascade to every part it
    holds (a composite, a part with no override, ...). To make the "resident on
    GPU" contract hold regardless, every real ``nn.Module`` discovered via the
    same walk :func:`module_size_gb` uses (``_iter_real_modules``) is also moved
    directly and in place — a no-op when the wrapper's own ``.to()`` already got
    it, a safety net when it didn't. The ``te.encode`` mark also carries
    ``weights_gb`` (:func:`_weights_gb_by_device` — a full per-device byte
    census taken *during* the encode, not the device the caller merely asked
    for). A single-sentinel device check can't catch a PARTIAL move (a small
    embedding/norm lands on the GPU while the bulk of a large composite stays
    on CPU — reads as "moved" while still running slow and VRAM-light); the
    census can't hide that, since the CPU share would carry the bulk of the
    bytes.

    Every ``te.encode`` mark also carries ``free_gb`` (the live
    :func:`free_vram_gb` reading the co-residency/after-evict gate just read)
    and ``needed_gb`` (``size_gb + reserve``, the threshold it was compared
    against) — the numbers that actually drove which ``path`` fired, not just
    the outcome. Without them, an unexpected ``after-evict`` (the DiT
    ping-pong the co-resident path exists to avoid) could only be explained by
    re-deriving ``free_vram_gb`` from other marks after the fact; a co-tenant
    process quietly eating VRAM between generations is otherwise invisible.
    Both are ``None`` on the no-CUDA-at-all ``cpu-fallback`` mark below (no
    placement gate runs there — there is nothing to report), not a fake
    ``0.0``/silent omission.
    """
    from src.platform.observability.profiling import get_profiler

    # `count` is only set by run_text_encode_batch's shared window -- omitted
    # entirely (not a fake 0/1) for every existing single-item call site, so
    # their `te.encode` marks keep their exact historical field set.
    count_fields = {} if count is None else {"count": count}

    dev = str(device)
    if not dev.startswith("cuda") or not torch.cuda.is_available():
        # No placement decision runs here (no CUDA to place on), so neither
        # number that drives the OTHER paths' choice is available -- explicit
        # None, not a fake 0.0/omission, so a reader can tell "not applicable"
        # apart from "forgot to wire it up".
        get_profiler().mark(
            "te.encode", device="cpu", size_gb=module_size_gb(encoder), path="cpu-fallback",
            free_gb=None, needed_gb=None, **count_fields,
        )
        return encode_fn()

    manager = get_residency_manager()
    size_gb = module_size_gb(encoder)
    reserve = minimum_inference_memory_gb() if reserve_gb is None else reserve_gb
    weights_gb: dict[str, float] = {}

    def _move(target: str) -> None:
        encoder.to(target)
        # Belt-and-suspenders: .to() on nn.Module is already a no-op when a
        # tensor is already on `target`, so re-issuing it on every real module
        # discovered underneath the wrapper is cheap and closes the gap if the
        # wrapper's own .to() didn't cascade to one of its parts.
        for real in _iter_real_modules(encoder):
            real.to(target)

    def _attempt_on_gpu() -> Any:
        nonlocal weights_gb
        _move(dev)
        # Register with the coordinator for the window the weights are
        # actually up: other components (the DiT) tracked here must see this
        # encoder's real VRAM footprint, or a concurrent eviction decision
        # would under-count what's resident. note_offloaded (in the finally)
        # mirrors NativeModel.move_to's own resident/offloaded pairing.
        manager.note_resident(encoder, dev, size_gb)
        weights_gb = _weights_gb_by_device(encoder)
        try:
            return encode_fn()
        finally:
            _move("cpu")
            manager.note_offloaded(encoder)
            torch.cuda.empty_cache()

    # 1) Co-residency: weights + reserve already fit -> no eviction, no ping-pong.
    free = free_vram_gb(dev)
    needed_gb = size_gb + reserve
    if free is None or free >= needed_gb:
        try:
            result = _attempt_on_gpu()
            get_profiler().mark(
                "te.encode", device=dev, size_gb=size_gb, path="co-resident",
                weights_gb=weights_gb, free_gb=free, needed_gb=needed_gb, **count_fields,
            )
            return result
        except torch.cuda.OutOfMemoryError:
            logger.warning("residency: co-resident TE encode OOM'd; freeing VRAM and retrying")
            torch.cuda.empty_cache()

    # 2) Make room (offload LRU-resident components), then retry on the GPU.
    free = free_vram_gb(dev) or 0.0
    manager.ensure_free(dev, size_gb * _TE_ACTIVATION_FACTOR + reserve, free, exclude=[encoder])
    torch.cuda.empty_cache()
    try:
        result = _attempt_on_gpu()
        get_profiler().mark(
            "te.encode", device=dev, size_gb=size_gb, path="after-evict",
            weights_gb=weights_gb, free_gb=free, needed_gb=needed_gb, **count_fields,
        )
        return result
    except torch.cuda.OutOfMemoryError:
        logger.warning("residency: TE encode still OOM after eviction; encoding on CPU")
        try:
            _move("cpu")
        finally:
            torch.cuda.empty_cache()
        get_profiler().mark(
            "te.encode", device="cpu", size_gb=size_gb, path="cpu-fallback",
            weights_gb=_weights_gb_by_device(encoder), free_gb=free, needed_gb=needed_gb, **count_fields,
        )
        return encode_fn()
