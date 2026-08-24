"""
ModelLifecycleManager - central cache for models and other expensive-to-build
pipeline artifacts (SDXL conditioning tensors, controlnets, etc.).

A generic key/fingerprint/loader cache that:
  - reuses a cached value when key+fingerprint match (cross-generation reuse)
  - evicts LRU entries under HOST-RAM pressure on a miss (cached models sit
    offloaded in host RAM between generations; VRAM occupancy is owned by the
    GpuResidencyManager / placement planner at move_to() time, NOT here)
  - at end_lease() (generation end), sweeps entries owned by the finishing
    generation's preset that it did NOT touch this run — a
    checkpoint swap WITHIN one preset otherwise sits in RAM until the next
    preset switch or RAM-pressure LRU
  - is the ONLY place that calls gc.collect()/torch.cuda.empty_cache()

Thread-safe via RLock: the comfyui-backend plugin drives GenerationManager
(and therefore pipes that call acquire()) from worker threads.
"""

import contextvars
import gc
import hashlib
import logging
import os
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.platform.runtime.model_lifecycle.memory_policy import MemoryPolicy
from src.platform.runtime.system_memory import get_system_memory
from src.platform.observability.profiling import get_profiler

logger = logging.getLogger(__name__)


def _fingerprint_hash(fingerprint: str) -> str:
    """Short, stable digest of a (potentially long) fingerprint string, for
    profiler event fields."""
    return hashlib.sha1(fingerprint.encode("utf-8", "replace")).hexdigest()[:12]

_BYTES_PER_GB = 1024 ** 3

# System-RAM floor kept free on top of whatever a load needs, mirroring
# GpuManager's VRAM safety margin but for host memory: a loader reads its
# checkpoint into plain (sometimes pinned, unswappable) CPU RAM before ever
# touching the GPU, and a large native model (e.g. a ~24.5GB Krea-2 DiT) with
# no RAM-aware admission control can push a loaded-down box to a near-freeze
# even though every individual cache eviction correctly frees its memory (see
# ModelLifecycleManager's module docstring / the RAM-investigation notes).
_MIN_FREE_RAM_GB = 8.0
_MIN_FREE_RAM_FRACTION = 0.10  # of total system RAM

# Process-wide reference to the app's ModelLifecycleManager singleton, set by
# the first instance constructed (mirrors output_type_registry's module-level
# singleton pattern). Lets call sites that aren't wired into the injector
# (staticmethods deep in pipe internals, plugin routes) reach cleanup()
# without threading the manager through every function signature.
_default_manager: Optional["ModelLifecycleManager"] = None

# The cache "owner" (a native generation's preset_id) for entries acquired on the
# current execution context. Set per-generation by begin_generation() on the pipe
# worker thread, read by acquire() on that same thread — so entries are tagged
# with the preset that loaded them. A ContextVar (not an instance attr) keeps it
# isolated to the running generation's thread-context even though the manager is a
# process-wide singleton: a concurrent comfyui generation on another thread never
# sees the native generation's owner. ``None`` = untagged (comfyui / warmup loads
# outside a native generation) and is never auto-evicted by the preset-scope policy.
_cache_owner: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "model_cache_owner", default=None
)

# Models acquired while a lease is active are unevictable for the lease's
# duration: RAM-pressure eviction must not drop a DiT cache entry between the
# model_loader and generator pipes (the bundle weakref would clear -> generator
# gets None). A ContextVar so concurrent generations each get their own lease.
_active_lease_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "active_generation_lease", default=None
)


def get_model_lifecycle_manager() -> Optional["ModelLifecycleManager"]:
    """Return the process-wide ModelLifecycleManager, or None if the app
    hasn't constructed one yet (e.g. isolated pipe unit tests)."""
    return _default_manager


def file_size_gb(path: Any) -> Optional[float]:
    """On-disk size of ``path`` in GB, or None if it can't be stat'd.

    A cheap pre-load estimate for `acquire(estimated_vram_gb=...)`: reading a
    safetensors file into RAM costs roughly its on-disk size, so this lets
    admission control evict BEFORE a multi-GB read instead of after (when the
    loader has no way to know the size up front).
    """
    if not path:
        return None
    try:
        return os.path.getsize(path) / _BYTES_PER_GB
    except (OSError, TypeError, ValueError) as e:
        logger.debug(f"[MODEL_LIFECYCLE] Could not stat {path!r} for size estimate: {e}")
        return None


def trim_host_allocator() -> None:
    """Ask glibc to return freed heap pages to the OS (``malloc_trim(0)``).

    Moving a ~23GB DiT between CPU and GPU frees thousands of layer-sized
    chunks; glibc may keep those pages in its arenas instead of munmap'ing
    them, so RSS stays high even though Python freed every tensor (observed:
    a warm LTX run's move_to(cuda) dropped RSS by only 0.87GB where ~23GB of
    CPU weights were released). Trimming is Linux/glibc-only and harmless
    elsewhere (silent no-op). Costs ~tens of ms on a large heap — call it
    only at coarse points (aggressive cleanup, big offloads), never per-step.

    Deliberately does NOT touch CUDA's *pinned* (page-locked) host memory —
    that allocator lives entirely outside glibc's heap (``cudaHostAlloc``, not
    ``malloc``/``mmap``), so ``malloc_trim`` has zero effect on it. See
    :func:`empty_pinned_host_cache` for that allocator's release call.
    """
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # pragma: no cover - non-glibc platforms
        pass


def empty_pinned_host_cache() -> None:
    """Release PyTorch's cached CUDA *pinned* (page-locked) host allocations.

    The native engine's partial-residency low-VRAM streaming
    (``src.platform.runtime.native.memory.partial``) pins CPU-resident
    "streamed leaf" weights (``tensor.pin_memory()``) so their per-forward
    H2D copy can be ``non_blocking``. Pinned memory is allocated through
    CUDA's own host allocator — a THIRD allocator distinct from both glibc's
    heap (``trim_host_allocator``) and the GPU caching allocator
    (``torch.cuda.empty_cache()``). Dropping every Python reference to a
    pinned tensor does not return its pages to the OS: PyTorch's caching host
    allocator keeps them around for reuse until this is called (a repro
    pinning ~600MB of CPU tensors, then dropping every reference and running
    gc.collect()+torch.cuda.empty_cache()+malloc_trim(0), left RSS elevated by
    ~1GB, completely unchanged — only this call released it). Without it, any
    generation that ever streamed
    partial residency leaves that RAM stuck until the process restarts,
    regardless of how thoroughly the model cache itself is evicted — exactly
    the reported "Clear VRAM & Cache (RAM) doesn't free RAM, needs a backend
    restart" symptom (VRAM clears fine since ``torch.cuda.empty_cache()``
    already covers the GPU-side caching allocator).

    Best-effort and version-tolerant: the private API name has moved between
    torch releases, so this tries the newer accelerator-generic entry point
    first, then the CUDA-specific one, and is a silent no-op (older torch,
    non-CUDA build, no CUDA context yet) rather than ever raising.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return
        empty_fn = getattr(torch, "_accelerator_emptyHostCache", None) or getattr(
            torch._C, "_host_emptyCache", None
        )
        if empty_fn is not None:
            empty_fn()
    except Exception:  # pragma: no cover - best-effort, version-dependent API
        logger.warning("[MODEL_LIFECYCLE] empty_pinned_host_cache failed", exc_info=True)


def _measure_value_ram_gb(value: Any) -> Optional[float]:
    """Best-effort host-RAM footprint of a cached value, for entries whose
    loader didn't pass ``estimated_vram_gb`` and that don't carry their own
    ``estimated_vram_gb`` attribute (e.g. NativeModel does - see engine.py).

    Duck-types an `nn.Module`-shaped `.module` attribute (the shape every
    native loader wrapper uses) and sums parameter + buffer storage. Returns
    None for anything else (plain lists/tensors from prompt_encoder/
    controlnet, etc.) rather than guessing.
    """
    module = getattr(value, "module", None)
    if module is None:
        return None
    try:
        total_bytes = 0
        for p in module.parameters():
            total_bytes += p.numel() * p.element_size()
        for b in module.buffers():
            total_bytes += b.numel() * b.element_size()
        if total_bytes == 0:
            return None
        return total_bytes / _BYTES_PER_GB
    except Exception as e:
        logger.debug(f"[MODEL_LIFECYCLE] Could not measure RAM footprint: {e}")
        return None


@dataclass
class _CacheEntry:
    key: str
    fingerprint: str
    value: Any
    estimated_vram_gb: Optional[float]
    last_used: float
    # The native preset_id that loaded this entry (from ``_cache_owner`` at
    # acquire time), or ``None`` for loads outside a native generation (comfyui,
    # warmups). Drives preset-scoped eviction: a native preset switch evicts
    # entries owned by OTHER presets; ``None``-owned entries are never touched.
    owner: Optional[str] = None
    # Generation lease IDs holding this entry unevictable: evicting an entry
    # mid-generation breaks pipes that hold bundle weakrefs across pipe boundaries.
    leased_by: set = None

    def __post_init__(self):
        if self.leased_by is None:
            self.leased_by = set()


def _best_effort_unload(value: Any) -> None:
    """Best-effort release of a cached value's resources.

    Handles the shapes actually cached today: model wrappers with a
    `.unload()` method (Maya), model wrappers with a `.pipe` diffusers
    pipeline attribute (SDXL/Chroma), and plain lists/tensors (conditioning,
    controlnet) which just need dereferencing.
    """
    if value is None:
        return
    try:
        pipe = getattr(value, "pipe", None)
        if pipe is not None:
            if hasattr(pipe, "unload_lora_weights"):
                try:
                    pipe.unload_lora_weights()
                except Exception as e:
                    logger.debug(f"[MODEL_LIFECYCLE] unload_lora_weights failed: {e}")
            try:
                value.pipe = None
            except Exception:
                pass
        if hasattr(value, "unload"):
            try:
                value.unload()
            except Exception as e:
                logger.debug(f"[MODEL_LIFECYCLE] value.unload() failed: {e}")
    except Exception as e:
        logger.debug(f"[MODEL_LIFECYCLE] cached value cleanup failed: {e}")


# Referrer types carrying no diagnostic signal for "what is holding this model" -
# filtered out of the gc.get_referrers() dump so the log highlights real holders.
_REFERRER_NOISE_TYPES = (dict, list, tuple, frozenset, set, type(sys))


def _log_referrer_diagnostic(key: str, value: Any) -> None:
    """When an eviction can't unload because something still references ``value``,
    log what that something IS, so an incident is diagnosable from production logs.

    ``gc.get_referrers`` is O(all tracked objects) - only for this already-rare
    "still referenced" branch, never the hot (sole-owner) path. Container
    referrers are filtered by type; anything else is the genuine holder to surface.
    """
    try:
        referrers = gc.get_referrers(value)
        interesting = [r for r in referrers if not isinstance(r, _REFERRER_NOISE_TYPES)]
        if interesting:
            summary = ", ".join(f"{type(r).__name__}" for r in interesting[:8])
            logger.debug(
                f"[MODEL_LIFECYCLE] key='{key}' referrer diagnostic: held by [{summary}] "
                f"({len(interesting)} non-container referrer(s), {len(referrers)} total)"
            )
        else:
            # Only container/frame referrers - can't name an owner directly, so
            # log the raw type breakdown.
            type_counts: Dict[str, int] = {}
            for r in referrers:
                type_counts[type(r).__name__] = type_counts.get(type(r).__name__, 0) + 1
            logger.debug(
                f"[MODEL_LIFECYCLE] key='{key}' referrer diagnostic: only container/frame "
                f"referrers found: {type_counts}"
            )
    except Exception:
        logger.debug(f"[MODEL_LIFECYCLE] referrer diagnostic failed for key={key!r}", exc_info=True)


def _sample_parameter_weakref(value: Any) -> "weakref.ref | None":
    """Best-effort weakref to ONE representative parameter/buffer tensor from
    ``value.module``, captured BEFORE :func:`_best_effort_unload` runs.

    Motivated by the LTX upscale-mode host-RAM incident: a production capture
    showed the DiT/TE cache entries passing
    the wrapper-level ``sys.getrefcount(value) <= 2`` check and running
    ``unload()`` -- yet free host RAM did not move AT ALL for either (vs. a
    1.35GB VAE eviction in the SAME capture, which did move free RAM as
    expected). ``_log_referrer_diagnostic`` above only ever fires on the
    ``refcount > 2`` branch, i.e. it can only name a holder of the
    ``NativeModel`` WRAPPER -- it is blind to a holder of the underlying
    weight TENSORS directly (bypassing the wrapper object entirely), which is
    exactly what this capture points at. This weakref lets the caller check,
    right after ``unload()`` returns, whether the actual storage became
    collectible -- and if not, :func:`_log_tensor_referrer_diagnostic` names
    whatever still holds it.
    """
    module = getattr(value, "module", None)
    if module is None or not hasattr(module, "parameters"):
        return None
    try:
        for p in module.parameters():
            return weakref.ref(p)
        for b in module.buffers():
            return weakref.ref(b)
    except Exception:
        return None
    return None


def _log_tensor_referrer_diagnostic(key: str, tensor: Any) -> None:
    """Like :func:`_log_referrer_diagnostic`, but on the raw weight TENSOR a
    wrapper's own ``unload()`` just tried to release, not the wrapper object
    itself -- names whatever is still holding the actual storage (see
    :func:`_sample_parameter_weakref`'s docstring for why this is a distinct,
    previously-blind failure mode from the wrapper-level diagnostic)."""
    try:
        referrers = gc.get_referrers(tensor)
        interesting = [r for r in referrers if not isinstance(r, _REFERRER_NOISE_TYPES)]
        if interesting:
            summary = ", ".join(f"{type(r).__name__}" for r in interesting[:8])
            logger.warning(
                f"[MODEL_LIFECYCLE] key='{key}' TENSOR-level referrer diagnostic: the "
                f"weight storage is held by [{summary}] "
                f"({len(interesting)} non-container referrer(s), {len(referrers)} total)"
            )
        else:
            type_counts: Dict[str, int] = {}
            for r in referrers:
                type_counts[type(r).__name__] = type_counts.get(type(r).__name__, 0) + 1
            logger.warning(
                f"[MODEL_LIFECYCLE] key='{key}' TENSOR-level referrer diagnostic: only "
                f"container/frame referrers found: {type_counts}"
            )
    except Exception:
        logger.debug(f"[MODEL_LIFECYCLE] tensor referrer diagnostic failed for key={key!r}", exc_info=True)


class ModelLifecycleManager:
    """
    acquire(key, fingerprint, loader) -> cached or freshly-loaded value.

    `key` identifies the cache slot (typically the pipe name, e.g.
    "checkpoint_loader/sdxl" or "prompt_encoder.conditioning"). `fingerprint`
    is a string capturing everything that would make a cached value stale
    (file paths, LoRA config, dtype, ...). A fingerprint mismatch busts the
    cache for that key; an unchanged fingerprint reuses the cached value
    without re-running `loader`.
    """

    def __init__(self, gpu_manager=None, settings_manager=None):
        self.gpu_manager = gpu_manager
        self.settings_manager = settings_manager
        self._lock = threading.RLock()
        self._entries: Dict[str, _CacheEntry] = {}
        # Keys with an acquire() in flight on this thread (loader() running,
        # or about to run) - eviction must never pick these, since the entry
        # they'd produce isn't in `_entries` yet to protect itself. RLock
        # makes concurrent acquire() from other threads impossible while this
        # is held, but a loader() that recursively calls acquire() for a
        # different key (e.g. an LTX DiT load pulling in its projection
        # tensors) still needs this guard on the same thread.
        self._acquiring: set = set()
        self._hits = 0
        self._misses = 0
        # Last native preset_id begin_generation() saw — the switch detector for
        # preset-scoped eviction (process-global: switching presets is a
        # cross-generation event).
        self._last_owner: Optional[str] = None
        # Generation leases: {lease_id -> set[key]}. Entries acquired under an
        # active lease are unevictable until release, so RAM-pressure eviction
        # can't drop a model between pipes within one generation.
        self._leases: Dict[str, set] = {}
        # Per-lease hit/miss/load-time counters. Always on (NOT gated behind
        # `profiling_enabled()`): the stats table needs a cold/warm signal for
        # every generation. Keyed by lease_id, popped by `end_lease`.
        self._lease_stats: Dict[str, Dict[str, float]] = {}
        logger.info("[MODEL_LIFECYCLE] Initialized")

        global _default_manager
        if _default_manager is None:
            _default_manager = self

    def acquire(
        self,
        key: str,
        fingerprint: str,
        loader: Callable[[], Any],
        estimated_vram_gb: Optional[float] = None,
    ) -> Any:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.fingerprint == fingerprint:
                entry.last_used = time.monotonic()
                self._hits += 1
                # Mark this entry as leased by the active generation, if any.
                lease_id = _active_lease_id.get()
                if lease_id is not None and lease_id not in entry.leased_by:
                    entry.leased_by.add(lease_id)
                    if lease_id not in self._leases:
                        self._leases[lease_id] = set()
                    self._leases[lease_id].add(key)
                    logger.debug(f"[MODEL_LIFECYCLE] Marked key='{key}' as leased by '{lease_id}'")
                logger.info(f"[MODEL_LIFECYCLE] Cache hit for key='{key}'")
                get_profiler().mark("models.acquire.hit", key=key, fingerprint=_fingerprint_hash(fingerprint))
                if lease_id is not None:
                    stats = self._lease_stats.get(lease_id)
                    if stats is not None:
                        stats["hits"] += 1
                return entry.value

            self._misses += 1
            if entry is not None:
                # Fingerprint-bust eviction: the old entry is replaced regardless
                # of leases (same key, fresh value wanted). But a leased entry's
                # value may still be referenced by an in-flight generation, so
                # only unload when it's the sole owner, as _evict_entry does.
                old_hash = _fingerprint_hash(entry.fingerprint)
                new_hash = _fingerprint_hash(fingerprint)
                if entry.leased_by:
                    logger.info(
                        f"[MODEL_LIFECYCLE] Fingerprint changed for key='{key}' "
                        f"(old={old_hash} new={new_hash}); evicting LEASED entry "
                        f"(leased by: {list(entry.leased_by)}); old value will "
                        f"drop-without-unload if still referenced"
                    )
                    logger.debug(
                        f"[MODEL_LIFECYCLE] Fingerprint changed for key='{key}' "
                        f"(old={entry.fingerprint!r} new={fingerprint!r})"
                    )
                else:
                    logger.info(
                        f"[MODEL_LIFECYCLE] Fingerprint changed for key='{key}' "
                        f"(old={old_hash} new={new_hash}); evicting"
                    )
                    logger.debug(
                        f"[MODEL_LIFECYCLE] Fingerprint changed for key='{key}' "
                        f"(old={entry.fingerprint!r} new={fingerprint!r})"
                    )
                # Trim glibc's heap after a same-preset fingerprint bust (e.g. a
                # LoRA added to a cached preset): _make_room_for_ram only trims
                # under pressure, which a big-RAM box won't detect, so without an
                # explicit trim here the freed DiT's heap arena stays unreturned
                # and the fresh checkpoint read below ratchets RSS up every swap.
                # Only when the entry was actually unloaded (sole owner).
                if self._evict_entry(key):
                    self.cleanup(aggressive=True)

            self._acquiring.add(key)
            try:
                # RAM admission only. There is deliberately NO VRAM-budget
                # gate here: cached models sit OFFLOADED in host RAM between
                # generations, so summing cache entries against a VRAM budget
                # treats CPU-resident weights as GPU occupancy and thrashes
                # (e.g. Krea-2 TE+DiT estimates > budget -> each acquire
                # evicted the other -> every generation re-read ~34GB from
                # disk, 25s -> 2min). VRAM occupancy is owned by the
                # GpuResidencyManager / placement planner at move_to() time.
                self._make_room_for_ram(estimated_vram_gb)

                logger.info(f"[MODEL_LIFECYCLE] Cache miss for key='{key}'; loading")
                get_profiler().mark("models.acquire.miss", key=key, fingerprint=_fingerprint_hash(fingerprint))
                miss_lease_id = _active_lease_id.get()
                miss_stats = self._lease_stats.get(miss_lease_id) if miss_lease_id is not None else None
                load_start = time.monotonic()
                value = loader()
                if miss_stats is not None:
                    miss_stats["misses"] += 1
                    miss_stats["load_ms"] += (time.monotonic() - load_start) * 1000

                # The caller's estimate wins when given (it ran BEFORE the
                # load, e.g. file_size_gb() on the checkpoint path, so it's
                # useful for admission control on the *next* acquire too).
                # Otherwise record the real post-load footprint so eviction
                # logging and RAM admission stop being a fiction.
                recorded_estimate = estimated_vram_gb
                if recorded_estimate is None:
                    recorded_estimate = getattr(value, "estimated_vram_gb", None)
                if recorded_estimate is None:
                    recorded_estimate = _measure_value_ram_gb(value)

                new_entry = _CacheEntry(
                    key=key,
                    fingerprint=fingerprint,
                    value=value,
                    estimated_vram_gb=recorded_estimate,
                    last_used=time.monotonic(),
                    owner=_cache_owner.get(),
                )
                # Mark this new entry as leased by the active generation, if any.
                lease_id = _active_lease_id.get()
                if lease_id is not None:
                    new_entry.leased_by.add(lease_id)
                    if lease_id not in self._leases:
                        self._leases[lease_id] = set()
                    self._leases[lease_id].add(key)
                    logger.debug(f"[MODEL_LIFECYCLE] Marked key='{key}' as leased by '{lease_id}' (newly loaded)")
                self._entries[key] = new_entry
                return value
            finally:
                self._acquiring.discard(key)

    def _evictable_keys(self) -> List[str]:
        """Keys eligible for LRU eviction, oldest-first: everything except:
        - a key whose acquire() is currently in flight on this thread (A3)
        - a key held by an active generation lease (production crash fix)
        """
        return [
            k for k in sorted(self._entries, key=lambda k: self._entries[k].last_used)
            if k not in self._acquiring and not self._entries[k].leased_by
        ]

    def _make_room_for_ram(self, needed_gb: Optional[float] = None) -> None:
        """Evict LRU entries until system RAM has headroom, or everything
        evictable has been evicted.

        Every native loader reads its checkpoint into CPU RAM before any GPU
        placement decision - a large model (e.g. a ~24.5GB Krea-2 DiT) with no
        RAM-aware admission control can push a loaded-down box to a near-freeze
        even when each individual cache eviction correctly frees its own
        memory. The risk is a large load simply outrunning whatever RAM
        happens to be free at that moment, e.g. many back-to-back LoRA-stack
        swaps each triggering a fresh multi-GB reload from disk.

        ``needed_gb`` is the caller's pre-load estimate for the incoming load,
        when one is available. When it's None (most call sites - no path was
        handy, or the loader isn't file-backed), this method does NOT no-op:
        that omission is exactly what let every model ever loaded pile up in
        RAM forever (see module-level bug notes). Instead it still enforces a
        floor of free RAM measured live, independent of any estimate.
        """
        try:
            mem = get_system_memory()
        except Exception as e:
            logger.debug(f"[MODEL_LIFECYCLE] Could not read system RAM info: {e}")
            return

        available_gb = mem.available_gb
        total_gb = mem.total_gb
        floor_gb = max(_MIN_FREE_RAM_GB, _MIN_FREE_RAM_FRACTION * total_gb)

        def _headroom_ok(avail_gb: float) -> bool:
            if needed_gb is None:
                return avail_gb >= floor_gb
            return avail_gb - needed_gb >= floor_gb

        if _headroom_ok(available_gb):
            return

        if needed_gb is None:
            logger.info(
                f"[MODEL_LIFECYCLE] RAM pressure: {available_gb:.2f}GB free is below the "
                f"{floor_gb:.2f}GB floor (of {total_gb:.2f}GB total RAM); evicting LRU cache entries"
            )
        else:
            logger.info(
                f"[MODEL_LIFECYCLE] RAM pressure: loading ~{needed_gb:.2f}GB would leave "
                f"~{available_gb - needed_gb:.2f}GB free (floor {floor_gb:.2f}GB of {total_gb:.2f}GB "
                f"total RAM); evicting LRU cache entries"
            )

        # Estimates (caller-supplied or measured post-load) are unreliable
        # enough that this is exactly the scenario that caused the original
        # bug - re-measure real free RAM after every single eviction rather
        # than trusting a running `available_gb += freed` estimate.
        for key in self._evictable_keys():
            if _headroom_ok(available_gb):
                break
            self._evict_entry(key)
            try:
                available_gb = get_system_memory().available_gb
            except Exception as e:
                logger.debug(f"[MODEL_LIFECYCLE] Could not re-read system RAM info: {e}")
                break

        self.cleanup(aggressive=True)
        try:
            available_gb = get_system_memory().available_gb
        except Exception as e:
            logger.debug(f"[MODEL_LIFECYCLE] Could not re-read system RAM info: {e}")

        if not _headroom_ok(available_gb):
            # Count leased GB that couldn't be evicted, so the "persists" warning
            # names the reason room-making failed.
            leased_gb = sum(
                self._entries[k].estimated_vram_gb or 0.0
                for k in self._entries
                if self._entries[k].leased_by
            )
            leased_count = sum(1 for k in self._entries if self._entries[k].leased_by)
            leased_note = (
                f" ({leased_count} entr(y/ies) totaling ~{leased_gb:.2f}GB were leased and skipped)"
                if leased_count > 0 else ""
            )
            if needed_gb is None:
                logger.warning(
                    f"[MODEL_LIFECYCLE] RAM pressure persists after evicting the entire cache: "
                    f"{available_gb:.2f}GB free is still below the {floor_gb:.2f}GB floor "
                    f"(of {total_gb:.2f}GB total RAM){leased_note}. Proceeding anyway - the OS may need to swap."
                )
            else:
                logger.warning(
                    f"[MODEL_LIFECYCLE] RAM pressure persists after evicting the entire cache: "
                    f"loading ~{needed_gb:.2f}GB would leave ~{available_gb - needed_gb:.2f}GB free "
                    f"(floor {floor_gb:.2f}GB of {total_gb:.2f}GB total RAM){leased_note}. Proceeding anyway - "
                    f"the OS may need to swap."
                )

    def _evict_entry(self, key: str) -> bool:
        """Evict ``key`` from the cache. Returns True iff the value was
        actually unloaded (freed VRAM/RAM immediately).

        A3 safety: a value may still be referenced outside the cache (e.g. a
        loader mid-generation holds `te_model` locally while acquiring the
        DiT next, and LRU pressure from that DiT acquire lands on the TE's
        key). Nulling `.module` out from under a live reference would crash
        the in-flight generation, so this only calls `_best_effort_unload`
        when the cache was the LAST holder - determined via `sys.getrefcount`
        after popping the entry and dropping the entry's own alias (measured
        empirically: a cache-only value shows refcount == 2 inside this
        method - the local `value` + getrefcount's own transient arg; any
        other live reference pushes it to 3+). Otherwise the entry is just
        dropped from `_entries` - ordinary refcounting frees it once its
        other holder releases it, same as today's fingerprint-bust path
        relies on with no reference cycle involved.
        """
        entry = self._entries.pop(key, None)
        if entry is None:
            return False

        # Clean up lease tracking (the entry is removed regardless of leases -
        # fingerprint-bust or invalidate can evict leased entries; lease
        # protection only applies to LRU).
        for lease_id in list(entry.leased_by):
            if lease_id in self._leases:
                self._leases[lease_id].discard(key)

        value = entry.value
        entry.value = None
        freed_gb = entry.estimated_vram_gb or 0.0
        del entry

        unloaded = False
        if value is not None:
            refcount = sys.getrefcount(value)
            if refcount <= 2:
                # The wrapper being sole-owner does NOT guarantee its underlying
                # weight tensors are collectible too (see _sample_parameter_weakref).
                # Captured BEFORE unload() so the weakref targets the tensor about
                # to be orphaned by `self.module = None`.
                sample_ref = _sample_parameter_weakref(value)
                _best_effort_unload(value)
                unloaded = True
                if sample_ref is not None and sample_ref() is not None:
                    gc.collect()
                    if sample_ref() is not None:
                        logger.warning(
                            f"[MODEL_LIFECYCLE] key='{key}' unload() ran (wrapper was sole "
                            f"owner) but a sample weight tensor is STILL ALIVE afterward -- "
                            f"this component's host RAM will NOT actually be freed"
                        )
                        _log_tensor_referrer_diagnostic(key, sample_ref())
            else:
                logger.info(
                    f"[MODEL_LIFECYCLE] key='{key}' still referenced elsewhere "
                    f"(refcount={refcount}); dropping from cache without unloading - "
                    f"it will free when its other holder releases it"
                )
                _log_referrer_diagnostic(key, value)
                # Tell the GPU residency tracker its cache entry is gone, so a
                # later reclaim pass can re-check the refcount and fully unload it
                # instead of offloading it to host RAM forever (a stale holder
                # otherwise leaves an orphaned DiT offloaded-not-unloaded and
                # never freed). Best-effort, never load-bearing for the eviction.
                try:
                    from src.platform.runtime.native.memory.residency import get_residency_manager

                    get_residency_manager().mark_orphaned(value)
                except Exception:
                    logger.debug("[MODEL_LIFECYCLE] mark_orphaned failed; continuing", exc_info=True)
        del value

        try:
            free_ram_gb = get_system_memory().available_gb
            free_note = f"{free_ram_gb:.2f}GB RAM free now"
        except Exception:
            free_note = "RAM free now (unavailable)"
        logger.info(
            f"[MODEL_LIFECYCLE] Evicted key='{key}' (~{freed_gb:.2f}GB estimated"
            f"{'' if unloaded else ', NOT unloaded - still referenced'}); {free_note}"
        )
        get_profiler().mark("models.evict", key=key, freed_gb=freed_gb, unloaded=unloaded)
        return unloaded

    def invalidate(self, key: Optional[str] = None) -> None:
        """Evict a single key, or everything when key is None.

        Overrides leases (manual/admin invalidation) but logs loudly which
        leased keys were killed.
        """
        with self._lock:
            if key is None:
                leased_keys = [k for k in self._entries if self._entries[k].leased_by]
                if leased_keys:
                    logger.warning(
                        f"[MODEL_LIFECYCLE] invalidate(all) is evicting {len(leased_keys)} "
                        f"LEASED entr(y/ies) (admin/manual override): {leased_keys}"
                    )
                for k in list(self._entries.keys()):
                    self._evict_entry(k)
                logger.info("[MODEL_LIFECYCLE] Invalidated all cache entries")
                # A full invalidate() must drop every native RAM cache. The
                # prompt-embed cache is a SEPARATE process-global singleton that
                # acquire()/_evict_entry() never touch. Best-effort.
                try:
                    from src.platform.runtime.native.text_encoders.embed_cache import (
                        get_prompt_embed_cache,
                    )

                    get_prompt_embed_cache().clear()
                except Exception:
                    logger.debug(
                        "[MODEL_LIFECYCLE] prompt embed cache clear failed; continuing",
                        exc_info=True,
                    )
            else:
                entry = self._entries.get(key)
                if entry and entry.leased_by:
                    logger.warning(
                        f"[MODEL_LIFECYCLE] invalidate(key='{key}') is evicting a LEASED entry "
                        f"(leased by: {list(entry.leased_by)}) - admin/manual override"
                    )
                self._evict_entry(key)
                logger.info(f"[MODEL_LIFECYCLE] Invalidated cache entry key='{key}'")
        self.cleanup(aggressive=True)
        # invalidate() is the manual/admin path, never a per-generation hot path,
        # so it's the one place safe to also empty CUDA's pinned-host-memory
        # cache. Doing this in cleanup() would fire on every generation and force
        # each partial-residency-streamed run to re-pin its streamed leaves
        # instead of reusing the warm pool.
        empty_pinned_host_cache()

    def begin_generation(self, owner: Optional[str]) -> None:
        """Mark the start of a native generation owned by preset ``owner``.

        Two jobs, both required BEFORE this generation's loaders run:
          1. Stamp ``owner`` onto the current execution context (``_cache_owner``)
             so every entry this generation acquires is tagged with its preset.
          2. When ``model_cache_scope == "preset"`` (the default) and ``owner``
             differs from the last native preset seen, evict every cached entry
             owned by a DIFFERENT preset and trim the host allocator — so RAM
             holds only the active preset's models, per the user request.

        ``owner=None`` (comfyui / non-native generations, warmups) only clears the
        context tag; it never evicts and never advances the switch detector, so a
        comfyui generation can't drop the native cache and vice-versa. Callers must
        invoke this on the same thread/context that will run the loaders (the pipe
        worker thread), which the GenerationManager does. The native queue is
        serial per backend (GenerationManager._run_lock), so there is no concurrent
        native generation racing this eviction.
        """
        _cache_owner.set(owner)
        if owner is None:
            return
        with self._lock:
            if (
                self._cache_scope() == "preset"
                and self._last_owner is not None
                and self._last_owner != owner
            ):
                self._evict_foreign_owner(owner)
            self._last_owner = owner

    def _cache_scope(self) -> str:
        """``"preset"`` (default, per-preset RAM) or ``"global"`` (legacy: keep
        every preset's models until RAM pressure), from the ``model_cache_scope``
        system setting. Unknown/unavailable -> ``"preset"``."""
        if self.settings_manager is None:
            return "preset"
        try:
            scope = self.settings_manager.get_setting("model_cache_scope", "preset")
        except Exception as e:  # pragma: no cover - settings backend hiccup
            logger.debug(f"[MODEL_LIFECYCLE] could not read model_cache_scope: {e}")
            return "preset"
        return scope if scope in ("preset", "global") else "preset"

    def _evict_foreign_owner(self, owner: str) -> None:
        """Evict every evictable entry owned by a preset OTHER than ``owner``.

        Skips ``None``-owned entries (comfyui/warmup — never auto-evicted) and any
        key with an acquire() in flight (``_evictable_keys`` already excludes
        ``_acquiring``). Trims the host allocator once at the end when it evicted
        anything, so freed pages actually return to the OS (the RAM-freeze fix)."""
        victims = [
            k for k in self._evictable_keys()
            if self._entries[k].owner is not None and self._entries[k].owner != owner
        ]
        for k in victims:
            self._evict_entry(k)
        if victims:
            logger.info(
                f"[MODEL_LIFECYCLE] preset switch -> {owner!r}: evicted {len(victims)} "
                f"cache entr(y/ies) belonging to other preset(s)"
            )
            self.cleanup(aggressive=True)

    def cleanup(self, aggressive: bool = False) -> None:
        """THE only gc.collect()/torch.cuda.empty_cache() call site."""
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                if aggressive:
                    torch.cuda.synchronize()
                torch.cuda.empty_cache()
        except ImportError:
            pass
        if aggressive:
            trim_host_allocator()
        # Deliberately NOT calling empty_pinned_host_cache() here even when
        # aggressive: this runs at the end of EVERY generation and after routine
        # per-generation eviction - hot paths a long-video LTX run re-enters
        # constantly, and dropping the pinned-host cache would force each to
        # re-pin its streamed leaves. Emptying that allocator is reserved for
        # invalidate(), where the user has explicitly asked for RAM back.

    def memory_policy(self) -> MemoryPolicy:
        vram_gb = 8.0
        if self.gpu_manager is not None:
            try:
                vram_gb = self.gpu_manager.get_vram_budget()
            except Exception as e:
                logger.warning(f"[MODEL_LIFECYCLE] Could not get VRAM budget, defaulting to {vram_gb}GB: {e}")
        return MemoryPolicy(vram_gb)

    def evict_dead_weight(self, key: str) -> bool:
        """Explicitly evict ONE cache entry mid-generation because the calling
        pipe already knows it is dead weight for the rest of THIS generation
        (the LTX standalone-upscale pipe releases its ~22GB idle Gemma3 text
        encoder once ``prompt_encoder`` has already produced
        the conditioning the downstream refine pass needs -- see
        ``latent_upscaler/ltx/main.py``'s ``_unload_idle_te``).

        Distinct from ``invalidate()`` (the admin "Clear VRAM & Cache" action)
        in two ways: no "admin override" logging (a pipe releasing its OWN
        known-dead component is routine, not an override), and it does NOT
        call ``empty_pinned_host_cache()`` -- that stays invalidate()-only, so
        a live partial-residency ``stream_to()`` pool elsewhere in the SAME
        generation (e.g. this same pipeline's DiT placement, a few pipes
        later) is not forced to re-pin from scratch (see
        ``empty_pinned_host_cache``'s docstring).

        Bypasses lease protection exactly like ``invalidate()`` does --
        ``_evict_entry`` unconditionally discards the key from every lease
        that holds it before evicting (leases only gate the LRU sweep in
        ``_evictable_keys``, never an explicit single-key call). That's safe
        here because the native generation queue is serial per backend (see
        ``begin_generation``'s docstring) -- no concurrent native generation
        can still need this exact entry. ACCEPTED COST for the caller: a
        later ``acquire()`` of this key will cache-miss and pay a fresh reload
        from disk.

        Returns whether the entry was actually unloaded (freed RAM
        immediately) -- False for an absent key or a value still referenced
        elsewhere (mirrors ``_evict_entry``'s own return contract).
        """
        with self._lock:
            unloaded = self._evict_entry(key)
        if unloaded:
            self.cleanup(aggressive=True)
        return unloaded

    def begin_lease(self, lease_id: str) -> None:
        """Begin a generation lease: models acquired while this lease is active
        become unevictable until end_lease().

        Production crash fix: without this, RAM-pressure eviction dropped the
        DiT cache entry between the model_loader and generator pipes, and the
        bundle's weakref cleared -> generator got None -> AttributeError on
        bundle.dit.spec.

        ``lease_id`` is typically the generation_id. Call this on the worker
        thread that will run the generation (so the ContextVar tag is visible
        to acquire()).
        """
        with self._lock:
            if lease_id in self._leases:
                logger.warning(
                    f"[MODEL_LIFECYCLE] begin_lease('{lease_id}') called but lease already exists; "
                    f"reusing existing lease"
                )
            else:
                self._leases[lease_id] = set()
            # Fresh always-on hit/miss/load_ms counters for this lease -- see
            # the docstring on `self._lease_stats` in `__init__`.
            self._lease_stats[lease_id] = {"hits": 0, "misses": 0, "load_ms": 0.0}
            _active_lease_id.set(lease_id)
            logger.debug(f"[MODEL_LIFECYCLE] Began generation lease '{lease_id}'")

    def end_lease(self, lease_id: str) -> Optional[Dict[str, float]]:
        """End a generation lease: entries acquired under this lease become
        normally evictable again, then sweep this generation's own
        preset for entries it left behind.

        Must be exception-safe (call from a finally block). If the lease
        doesn't exist (double-release, or release without begin), this is a
        no-op with a debug log (not an error - best-effort cleanup).

        Returns the lease's accumulated ``{"hits", "misses", "load_ms"}``
        counters, or ``None`` if the lease didn't exist.
        A generation is cold iff ``misses > 0``; ``load_ms`` is the total wall
        time spent inside every ``loader()`` call made under this lease.
        """
        with self._lock:
            stats = self._lease_stats.pop(lease_id, None)
            if lease_id not in self._leases:
                logger.debug(
                    f"[MODEL_LIFECYCLE] end_lease('{lease_id}') called but lease doesn't exist "
                    f"(double-release or release-without-begin); ignoring"
                )
                return stats
            leased_keys = self._leases.pop(lease_id)
            # Remove this lease_id from every entry it held.
            for key in leased_keys:
                entry = self._entries.get(key)
                if entry:
                    entry.leased_by.discard(lease_id)
            # Clear the ContextVar if it's set to this lease (it might not be if
            # end_lease() runs on a different thread/context than begin_lease()).
            if _active_lease_id.get() == lease_id:
                _active_lease_id.set(None)
            logger.debug(
                f"[MODEL_LIFECYCLE] Ended generation lease '{lease_id}' "
                f"({len(leased_keys)} key(s) now normally evictable)"
            )
            # begin_generation() stamped this generation's preset onto
            # _cache_owner on this same thread/context, and nothing clears it
            # before this finally-block call runs -- so it still names the
            # finishing generation's preset here.
            owner = _cache_owner.get()
            if owner is not None and self._cache_scope() == "preset":
                self._sweep_unused_owned(owner, leased_keys)
            return stats

    def _sweep_unused_owned(self, owner: str, touched_keys: set) -> None:
        """End-of-generation sweep: evict entries owned by ``owner``
        that THIS generation did not acquire/hit (``touched_keys`` — the
        lease's own key set, already populated by every ``acquire()`` call
        under it).

        Preset-switch eviction (``_evict_foreign_owner``) only fires between
        two different presets, so swapping a checkpoint WITHIN one preset
        (same owner, new cache key) left the old checkpoint in RAM until the
        next preset switch or RAM-pressure LRU. This closes that gap: reuses
        ``_evictable_keys()`` / ``_evict_entry`` (never a second eviction
        path), so a key still leased by another concurrent generation, or
        mid-acquire, is untouched exactly as preset-switch eviction already
        guarantees.

        ACCEPTED TRADEOFF: a model this run conditionally skipped (e.g. an
        upscaler toggled off) is swept too and reloads from disk next time
        it's enabled -- intentional: A/B'ing checkpoints within a preset
        should not accumulate dead weight.
        """
        victims = [
            k for k in self._evictable_keys()
            if self._entries[k].owner == owner and k not in touched_keys
        ]
        for k in victims:
            self._evict_entry(k)
        if victims:
            logger.info(
                f"[MODEL_LIFECYCLE] generation end (owner={owner!r}): evicted {len(victims)} "
                f"cache entr(y/ies) not used by this generation"
            )
            self.cleanup(aggressive=True)

    def generation_lease(self, generation_id: str):
        """Context manager for generation-scoped leases: models acquired during
        the with-block are held unevictable until the block exits.

        Usage:
            with models.generation_lease(generation_id) as lease_stats:
                # run pipeline - all acquires are protected from eviction
                ...
            # lease released, entries are evictable again; lease_stats now
            # holds {"hits", "misses", "load_ms"} for the whole block

        Exception-safe: the lease is released even if the generation fails.
        """
        from contextlib import contextmanager

        @contextmanager
        def _lease_context():
            self.begin_lease(generation_id)
            lease_stats: Dict[str, float] = {"hits": 0, "misses": 0, "load_ms": 0.0}
            try:
                yield lease_stats
            finally:
                result = self.end_lease(generation_id)
                if result:
                    lease_stats.update(result)

        return _lease_context()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "keys": list(self._entries.keys()),
                "hits": self._hits,
                "misses": self._misses,
                "estimated_vram_gb": sum(e.estimated_vram_gb or 0.0 for e in self._entries.values()),
                "active_leases": len(self._leases),
                "leased_keys": sum(len(keys) for keys in self._leases.values()),
            }

    def leased_values(self) -> List[Any]:
        """Cached values currently held unevictable by an active generation
        lease (see ``begin_lease``/``generation_lease``).

        For a caller that wants to free VRAM WITHOUT yanking a model out from
        under a generation that's still running (e.g. the Clear VRAM admin
        action's ``GpuResidencyManager.offload_all(device, exclude=...)`` —
        the same cache entries this manager already protects from LRU
        eviction are exactly the models a concurrent native generation is
        actively using right now.
        """
        with self._lock:
            return [e.value for e in self._entries.values() if e.leased_by]

    def expected_ram_gb(self) -> float:
        """Sum of every cache entry's ``estimated_vram_gb`` -- the cache's own
        belief about how much host RAM it is holding right now.

        Reuses the SAME per-entry number ``stats()`` already sums (recorded
        once at ``acquire()`` time -- see ``recorded_estimate`` there: the
        caller's pre-load estimate, or ``value.estimated_vram_gb``, or a
        one-time ``_measure_value_ram_gb`` walk). No re-measurement here, so
        this is cheap enough to call on every ``models.cleanup.post`` mark:
        subtracting it from that mark's ``rss_gb`` names how much of the
        process's RSS is NOT accounted for by the model cache (leaks, glibc
        arena fragmentation, other subsystems) without a special investigation.
        """
        with self._lock:
            return sum(e.estimated_vram_gb or 0.0 for e in self._entries.values())

    def entry_size_gb(self, key: str) -> Optional[float]:
        """The recorded ``estimated_vram_gb`` for a cached entry, or ``None``
        if the key is absent or the size was never known. A read-only lookup
        for a caller that needs the already-computed size estimate (recorded
        once at ``acquire()`` time — see ``recorded_estimate`` there) rather
        than re-deriving it — e.g. ``NativeLLMClient._note_resident`` handing
        a size to ``GpuResidencyManager.note_resident``.
        """
        with self._lock:
            entry = self._entries.get(key)
            return entry.estimated_vram_gb if entry is not None else None

    def is_cached(self, key: str) -> bool:
        """Whether ``key`` currently has a live cache entry — loaded right
        now, whether or not it's leased. Distinct from ``entry_size_gb``,
        which returns ``None`` both when the key is absent AND when it's
        present with an unknown size, so it can't answer "is it loaded" on
        its own. For a caller distinguishing on-disk presence (a filesystem
        check, e.g. a provider's own ``is_available()``) from in-memory
        residency — evicting a cache entry must never make something that's
        still on disk look absent to a status endpoint.
        """
        with self._lock:
            return key in self._entries

    def cached_values(self) -> List[Any]:
        """Every value currently in the cache, leased or not, GPU-resident or not.

        A general iteration seam for a caller that needs to reason about the
        whole cache rather than just the eviction-protected subset
        (``leased_values()``) — e.g. the Clear VRAM admin action's fallback
        sweep: a cached model that ended up GPU-resident without registering
        with ``GpuResidencyManager`` (a placement path that forgot to, now or
        in the future) is still reachable here, so the action can offload it
        even though the residency ledger never saw it.
        """
        with self._lock:
            return [e.value for e in self._entries.values()]
