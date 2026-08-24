"""Per-generation resource profiler.

Tracks host and device memory pressure across a generation so RSS growth can
be attributed to a specific pipe/model transition instead of the process as a
whole. Produces a ``profile.jsonl`` artifact with one row per periodic sample
plus one row per named event ("chokepoint" in the pipeline/model
lifecycle/native engine), each carrying an RSS / available RAM / swap / CPU /
per-device VRAM / pinned-memory snapshot. ``scripts/profile_report.py`` turns
that into a stage table and a top-N RSS-jump report.

Enable/disable
--------------
Profiling is OFF by default and must be cheap when off: :func:`profiling_enabled`
checks (in order) the ``POTIONUI_PROFILE`` env var, then the ``profiling.enabled``
settings-table key (via whatever ``SettingsManager`` was registered with
:func:`configure_settings_manager`, mirroring the ``get_global_*`` accessors in
``src.platform.plugins.runtime_registries``). The decision is cached per-process after the first call
so :func:`mark` never hits the DB; call :func:`reset_enabled_cache` (tests only)
to force a re-read.

Per-generation log
-------------------
While a generation is being profiled, :meth:`GenerationProfiler.start` also
attaches a ``logging.FileHandler`` to the ROOT logger writing
``<out_dir>/generation.log`` (INFO and above -- DEBUG would be enormous), so
the profile and the app's log lines for that window travel together as one
artifact. Detached and closed in :meth:`GenerationProfiler.stop` (and when
:meth:`start` replaces an already-active generation). Attach/detach never
raise into the caller.

Pinned-bytes gauge
------------------
:func:`add_pinned_bytes` / :func:`pinned_cum_gb` track memory pinned via
``Tensor.pin_memory()`` in the partial-residency streamer
(``src.platform.runtime.native.memory.partial``). There is no cheap hook for when pinned
memory is *freed* (it happens implicitly when the pinned tensor is garbage
collected), so this is a **cumulative, monotonically-increasing** counter since
process start, not a live "currently pinned" gauge — it answers "how much
pinning has this process done", not "how much is pinned right now". The field
is named ``pinned_cum_gb`` in the output so it isn't mistaken for the latter;
correlate it with ``streamer.teardown``/``models.evict`` marks to reason about
frees.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import warnings
from pathlib import Path
from typing import Any, Optional

import psutil

from src.platform.runtime.system_memory import get_system_memory

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1024 ** 3


# -- enable/disable -----------------------------------------------------------

_settings_manager: Any = None
_enabled_cache: Optional[bool] = None


def configure_settings_manager(settings_manager: Any) -> None:
    """Register the ``SettingsManager`` used by the ``profiling.enabled``
    fallback. Call once during app wiring (see ``build_container``)."""
    global _settings_manager
    _settings_manager = settings_manager


def reset_enabled_cache() -> None:
    """Test hook: force :func:`profiling_enabled` to re-read its source."""
    global _enabled_cache
    _enabled_cache = None


def profiling_enabled() -> bool:
    """Whether profiling is on. Cached per-process after the first call."""
    global _enabled_cache
    if _enabled_cache is not None:
        return _enabled_cache

    env = os.environ.get("POTIONUI_PROFILE")
    if env is not None:
        _enabled_cache = env.strip().lower() in ("1", "true", "yes", "on")
        return _enabled_cache

    if _settings_manager is not None:
        try:
            _enabled_cache = bool(_settings_manager.get_setting("profiling.enabled", False))
        except Exception:
            logger.debug("profiling: could not read 'profiling.enabled' setting", exc_info=True)
            _enabled_cache = False
    else:
        _enabled_cache = False
    return _enabled_cache


# -- pinned-bytes gauge ---------------------------------------------------------

_pinned_lock = threading.Lock()
_pinned_bytes_cum = 0


def add_pinned_bytes(n: int) -> None:
    """Record ``n`` bytes just pinned via ``Tensor.pin_memory()``. Cumulative
    since process start -- see module docstring for why there's no "live" gauge."""
    if not n or not profiling_enabled():
        return
    global _pinned_bytes_cum
    with _pinned_lock:
        _pinned_bytes_cum += int(n)


def pinned_cum_gb() -> float:
    with _pinned_lock:
        return _pinned_bytes_cum / _BYTES_PER_GB


def read_process_rss_gb() -> Optional[float]:
    """This process's current RSS in GB -- the same read :meth:`GenerationProfiler._snapshot`
    puts in every row's ``rss_gb`` field, exposed standalone so a caller that
    wants a before/after pair bracketing one specific operation (e.g. a
    ``trim_host_allocator()`` call) doesn't have to invent its own RSS read.
    Uses a fresh ``psutil.Process()`` rather than a profiler instance's cached
    handle -- both read the same live ``/proc/self/status`` counter for THIS
    process, so there is no second method here, just no dependency on a
    ``GenerationProfiler`` instance existing. Fails soft (``None``) exactly
    like every other stat in this module."""
    try:
        return psutil.Process().memory_info().rss / _BYTES_PER_GB
    except Exception:
        return None


def _round(v: Any) -> Any:
    return round(v, 3) if isinstance(v, float) else v


# -- anon/file RSS split (EVENT marks only -- see mark()) ---------------------
#
# Distinguishes page-cache-backed RSS growth (reclaimable) from genuinely
# anonymous (heap/tensor) growth, which plain ``rss_gb`` can't tell apart.
# NOTE: read ``/proc/self/status``, NOT ``/proc/self/smaps_rollup`` -- the
# latter has no ``RssAnon``/``RssFile``/``RssShmem`` lines (only PSS-per-category
# and a single combined ``Rss:``). Same cost profile (one kernel-aggregated
# counter read, no per-VMA walk).
_PROC_STATUS_PATH = "/proc/self/status"


def _parse_rss_anon_file_split(text: str) -> Optional[dict]:
    """Pure parser for ``/proc/self/status``'s ``RssAnon``/``RssFile``/
    ``RssShmem`` lines (each ``"RssAnon:      1234 kB"``) -> a
    ``{"rss_anon_gb": ..., "rss_file_gb": ..., "rss_shmem_gb": ...}`` dict
    (kB -> GB, only the keys actually found in ``text``). Split out from the
    file-reading wrapper below so it can be unit-tested against a fabricated
    text blob, with no real ``/proc`` file involved. Returns ``None`` when
    none of the three lines are found (a malformed or unrelated ``text``,
    e.g. a non-Linux ``/proc/self/status`` shape)."""
    field_keys = {"RssAnon:": "rss_anon_gb", "RssFile:": "rss_file_gb", "RssShmem:": "rss_shmem_gb"}
    result: dict = {}
    for line in text.splitlines():
        line = line.strip()
        for prefix, out_key in field_keys.items():
            if not line.startswith(prefix):
                continue
            parts = line[len(prefix):].split()
            if not parts:
                continue
            try:
                kb = float(parts[0])
            except ValueError:
                continue
            result[out_key] = round(kb / (1024 ** 2), 4)  # kB -> GB
    return result or None


def _read_rss_anon_file_split() -> Optional[dict]:
    """Read + parse the anon/file/shmem RSS split for THIS process, right
    now. Fail-soft end to end: a missing file (non-Linux, sandboxed, no
    ``/proc``), a read error, or a parse miss all return ``None`` -- the
    caller (``mark()``) then simply omits the fields from that row rather
    than ever raising into the generation it's observing.

    Cost: one small, kernel-aggregated ``/proc`` file read (not a per-VMA
    walk like ``/proc/self/smaps`` proper) -- still real I/O, so this is
    called from EVENT marks only, never the 250ms sample loop (see
    ``_sample_loop``, which stays psutil-only).
    """
    try:
        with open(_PROC_STATUS_PATH, "r") as f:
            text = f.read()
    except OSError:
        return None
    try:
        return _parse_rss_anon_file_split(text)
    except Exception:
        logger.debug("profiler: rss anon/file split parse failed", exc_info=True)
        return None


def _tensor_storage_key(t: "torch.Tensor") -> Any:
    """Best-effort identity for the allocation backing ``t``.

    Used to dedup views/aliases of the same underlying storage during the
    tensor census's grouped section, so N Python ``Tensor`` objects that all
    point at one weight (e.g. a base tensor plus a ``.data`` alias, or a slice)
    count as one allocation instead of N. Falls back to ``id(t)`` (no dedup,
    but never wrong in the "double-counts an alias" direction that matters
    for a leak hunt) on anything lacking the modern storage API.
    """
    try:
        return t.untyped_storage().data_ptr()
    except Exception:
        try:
            return t.storage().data_ptr()  # pragma: no cover - pre-1.13 torch
        except Exception:
            return id(t)


def _storage_nbytes(t: "torch.Tensor", fallback_nbytes: int) -> int:
    """Bytes actually backing ``t``'s storage (the allocation), not the
    tensor's logical view size (``numel() * element_size()``, which
    undercounts e.g. a narrow view and overcounts nothing) -- falls back to
    the logical size if the storage API is unavailable."""
    try:
        return t.untyped_storage().nbytes()
    except Exception:
        try:
            return t.storage().nbytes()  # pragma: no cover - pre-1.13 torch
        except Exception:
            return fallback_nbytes


# -- per-generation log ---------------------------------------------------------

LOG_FILENAME = "generation.log"
_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


# -- profiler -------------------------------------------------------------------

class GenerationProfiler:
    """Samples process/system/VRAM stats at a fixed interval on a daemon
    thread for the duration of one generation, plus records named events on
    demand. Only one generation is profiled at a time; a new :meth:`start`
    replaces whatever is currently running.
    """

    _SAMPLE_INTERVAL_S = 0.25
    _FLUSH_INTERVAL_S = 2.0

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc = psutil.Process()
        self._generation_id: Optional[str] = None
        self._fh = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._last_flush = 0.0
        self._log_handler: Optional[logging.Handler] = None

    def start(self, generation_id: str, out_dir: str | Path) -> None:
        if not profiling_enabled():
            return
        try:
            with self._lock:
                if self._thread is not None:
                    logger.debug(
                        "profiler: start(%s) while %s is active; replacing",
                        generation_id, self._generation_id,
                    )
                    self._stop_locked()

                out_dir = Path(out_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                profile_path = out_dir / "profile.jsonl"
                self._fh = open(profile_path, "a", buffering=1)
                self._attach_log_handler(out_dir)
                self._generation_id = generation_id
                self._last_flush = time.monotonic()
                try:
                    self._proc.cpu_percent(interval=None)  # prime the % counter
                except Exception:
                    pass

                stop_event = threading.Event()
                self._stop_event = stop_event
                self._thread = threading.Thread(
                    target=self._sample_loop,
                    args=(stop_event,),
                    name=f"gen-profiler-{generation_id}",
                    daemon=True,
                )
                self._thread.start()
        except Exception:
            logger.debug("profiler: start failed", exc_info=True)
            return
        self.mark("generation.start")

    def stop(self, generation_id: str) -> None:
        if not profiling_enabled():
            return
        try:
            with self._lock:
                if self._generation_id != generation_id:
                    return
                self.mark("generation.end")
                self._write_tensor_census(device_kind="cpu")
                self._write_tensor_census(device_kind="cuda")
                self._stop_locked()
        except Exception:
            logger.debug("profiler: stop failed", exc_info=True)

    def census_now(self, tag: str) -> None:
        """Fire an ad-hoc grouped tensor census AT A COARSE POINT MID-GENERATION,
        independent of :meth:`stop`'s end-of-run census.

        A killed run (earlyoom / OOM) never reaches :meth:`stop` -- SIGKILL
        doesn't run Python's own cleanup -- so the one census that could name
        what's actually holding memory right before a kill never gets
        written. This lets a caller that already suspects a specific moment
        (e.g. right after an eviction that reported ``unloaded=True`` but
        should have freed real RAM) request a census AT THAT MOMENT, so a
        killed run's ``profile.jsonl`` still carries at least one census
        taken near the point of death.

        Writes ``kind: "census_group_now"`` / ``"census_now"`` rows (NOT the
        ``"census_group"``/``"census"`` kinds :meth:`stop` writes) so a
        mid-run snapshot never gets summed together with the end-of-run one
        by ``report.py``'s renderers -- two point-in-time snapshots of the
        same live tensor would otherwise double-count it. Every row carries
        ``tag`` (the caller-supplied label) so several ``census_now`` calls
        across one generation each render as their own labeled section (see
        ``report.py``'s ``render_*_tensor_census_groups_now``).

        Cost: one ``gc.get_objects()`` walk (the same one :meth:`stop`'s
        end-of-run census does), bounded by the same
        :data:`_CENSUS_TIME_BUDGET_S`. Coarse by design -- call this at a
        rare, meaningful phase boundary (an eviction, a placement decision),
        NEVER from a per-step/per-layer path.
        """
        if not profiling_enabled():
            return
        try:
            with self._lock:
                if self._fh is None:
                    return
                self._write_tensor_census(
                    device_kind="cpu", kind_group="census_group_now", kind_detail="census_now", tag=tag,
                )
                self._write_tensor_census(
                    device_kind="cuda", kind_group="census_group_now", kind_detail="census_now", tag=tag,
                )
        except Exception:
            logger.debug("profiler: census_now(%s) failed", tag, exc_info=True)

    def _stop_locked(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                logger.debug("profiler: failed closing writer", exc_info=True)
        self._fh = None
        self._generation_id = None
        self._stop_event = None
        self._detach_log_handler()

    def _attach_log_handler(self, out_dir: Path) -> None:
        try:
            handler = logging.FileHandler(out_dir / LOG_FILENAME, encoding="utf-8")
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter(_LOG_FORMAT))
            logging.getLogger().addHandler(handler)
            self._log_handler = handler
        except Exception:
            logger.debug("profiler: failed to attach log handler", exc_info=True)
            self._log_handler = None

    def _detach_log_handler(self) -> None:
        handler, self._log_handler = self._log_handler, None
        if handler is None:
            return
        try:
            logging.getLogger().removeHandler(handler)
            handler.close()
        except Exception:
            logger.debug("profiler: failed to detach log handler", exc_info=True)

    # Row-schema fields reserved from caller-supplied ``**fields``: a caller
    # field named ``"kind"``/``"event"`` is prefixed (``component_kind=...``)
    # rather than allowed to clobber the row-type discriminator.
    _RESERVED_ROW_KEYS = frozenset({"kind", "event"})

    def mark(self, event: str, **fields: Any) -> None:
        """Write one ``kind: "event"`` row for ``event``, plus any extra
        ``**fields`` the caller wants attached (rendered as ``[k=v ...]`` in
        the stage table -- see ``report.py``'s ``render_stage_table``).

        A caller field named ``"kind"`` (e.g. ``mark("native.move_to",
        kind=self.kind, ...)``) would otherwise clobber the row-type
        discriminator and make the row invisible to every ``kind == "event"``
        filter; such keys are prefixed to ``component_kind`` and ``kind``/
        ``event`` are set LAST so they always win.
        """
        if not profiling_enabled():
            return
        try:
            with self._lock:
                if self._fh is None:
                    return
                row = self._snapshot()
                # Anon/file/shmem RSS split -- EVENT rows only (see
                # _read_rss_anon_file_split's docstring for cost/why), never
                # the 250ms sample loop. Omitted entirely (no key at all,
                # not a null) when unavailable, so an old report.py or a
                # non-Linux run just doesn't render the extra column.
                rss_split = _read_rss_anon_file_split()
                if rss_split is not None:
                    row.update(rss_split)
                for k, v in fields.items():
                    key = f"component_{k}" if k in self._RESERVED_ROW_KEYS else k
                    row[key] = _round(v)
                # Set LAST so no caller-supplied field can impersonate the
                # row-type discriminator or the event name.
                row["kind"] = "event"
                row["event"] = event
                self._fh.write(json.dumps(row) + "\n")
                self._fh.flush()
                self._last_flush = time.monotonic()
        except Exception:
            logger.debug("profiler: mark(%s) failed", event, exc_info=True)

    def _sample_loop(self, stop_event: threading.Event) -> None:
        # Takes its own event by value (not ``self._stop_event``, which a
        # concurrent stop()/start() rebinds to a new Event or None) so this
        # thread's lifecycle is governed solely by the event it was handed at
        # spawn time -- reading the mutable instance attribute here raced
        # against `_stop_locked` nulling it out and could crash on
        # ``None.wait()``.
        while not stop_event.is_set():
            try:
                with self._lock:
                    if self._fh is not None:
                        row = self._snapshot()
                        row["kind"] = "sample"
                        self._fh.write(json.dumps(row) + "\n")
                        now = time.monotonic()
                        if now - self._last_flush >= self._FLUSH_INTERVAL_S:
                            self._fh.flush()
                            self._last_flush = now
            except Exception:
                logger.debug("profiler: sample failed", exc_info=True)
            stop_event.wait(self._SAMPLE_INTERVAL_S)

    # -- CPU / CUDA tensor census ------------------------------------------------
    #
    # Walks the live object graph at generation.end and records reachable
    # tensors plus a best-effort guess at what holds them, so profile.jsonl from
    # a leaking run names the culprit, not just the timestamp.
    # ``_write_tensor_census(device_kind="cuda")`` is the same walk with the
    # device filter flipped (CUDA-side mirror), carrying an extra ``"device"``
    # field.
    #
    # ``census_group`` rows report EVERY live tensor for the scanned device with
    # NO size floor -- the >64MB per-tensor detail threshold is structurally
    # blind to a fully-resident multi-GB model (an fp8 DiT's Linear weights land
    # at/below 64MiB), so a group census is the only way to see it. Deduped by
    # underlying storage (:func:`_tensor_storage_key`), aggregated by
    # ``(device, dtype, owner, is_pinned)``. ``is_pinned`` is its own grouping
    # key (not folded into owner) because pinned vs. non-pinned copies are backed
    # by two DIFFERENT host allocators (page-locked vs. glibc heap) with
    # different release semantics. Bounded by ``_CENSUS_MAX_GROUP_ROWS`` (sorted
    # largest-first). The per-tensor >64MB detail rows are kept as a secondary
    # ``kind: "census"`` section, deliberately NOT deduped by storage.

    _CENSUS_MIN_BYTES = 64 * 1024 * 1024  # 64MB
    _CENSUS_TIME_BUDGET_S = 2.0
    _CENSUS_MAX_OWNER_DEPTH = 3
    _CENSUS_MAX_REFERRERS = 20
    _CENSUS_MAX_GROUP_ROWS = 200

    def _write_tensor_census(
        self, *, device_kind: str,
        kind_group: str = "census_group", kind_detail: str = "census",
        tag: Optional[str] = None,
    ) -> None:
        """Shared walk behind the CPU and CUDA tensor census, selected via
        `device_kind`. Best-effort end to end: any failure (including torch
        not being importable) is swallowed so profiling never breaks the
        generation it's observing.

        Writes two sections, both derived from ONE ``gc.get_objects()`` walk
        (a second walk over a live heap this size would double the cost for no
        benefit): ``kind: <kind_group>`` rows aggregate EVERY live tensor for
        ``device_kind`` (no size floor, deduped by storage) so the census can
        actually see where a fully-resident multi-GB model's memory is; ``kind:
        <kind_detail>`` rows are the original per-tensor detail for anything
        >= ``_CENSUS_MIN_BYTES`` (kept verbatim -- not deduped -- for continuity
        with anything already matching on that row shape). See the class-level
        comment above :data:`_CENSUS_MIN_BYTES` for the full motivation.

        ``kind_group``/``kind_detail``/``tag`` let :meth:`census_now` reuse this
        exact walk for an ad-hoc mid-generation snapshot without colliding with
        :meth:`stop`'s end-of-run rows (see that method's docstring) -- the
        default ``kind_group="census_group"``/``kind_detail="census"``/
        ``tag=None`` triple is byte-identical to this method's original,
        untagged behaviour.
        """
        if self._fh is None:
            return
        try:
            import gc
            try:
                import torch as _torch
            except Exception:
                return

            start = time.monotonic()
            try:
                objects = gc.get_objects()
            except Exception:
                logger.debug("profiler: census gc.get_objects failed", exc_info=True)
                return

            detail_rows: list[dict] = []
            # group_key -> {device, dtype, owner, is_pinned, count, nbytes}.
            groups: dict[tuple, dict] = {}
            # storage_key -> owner string, memoised so N tensors/views sharing
            # one storage pay ONE referrer walk (the expensive part), not N.
            owner_cache: dict[Any, str] = {}
            seen_storage: set = set()

            # The gc walk's isinstance() check runs against every live object,
            # including deprecated torch aliases (e.g. torch.distributed.reduce_op)
            # whose mere __instancecheck__/__class__ access emits a FutureWarning --
            # harmless here (we only ever match real torch.Tensor instances) but
            # noisy on every profiled run, so it's suppressed for the whole walk
            # rather than per-object.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for obj in objects:
                    if time.monotonic() - start > self._CENSUS_TIME_BUDGET_S:
                        break
                    try:
                        if not isinstance(obj, _torch.Tensor):
                            continue
                        if obj.is_meta or obj.device.type != device_kind:
                            continue
                        nbytes = obj.numel() * obj.element_size()
                        if nbytes <= 0:
                            continue
                    except Exception:
                        continue

                    is_pinned = None
                    if device_kind == "cpu":
                        try:
                            is_pinned = bool(obj.is_pinned())
                        except Exception:
                            is_pinned = None

                    storage_key = _tensor_storage_key(obj)
                    first_sighting = storage_key not in seen_storage
                    if first_sighting:
                        seen_storage.add(storage_key)

                    cached_owner = owner_cache.get(storage_key)
                    if cached_owner is None:
                        cached_owner = self._describe_owner(obj, start)
                        owner_cache[storage_key] = cached_owner
                    owner = cached_owner

                    if first_sighting:
                        storage_bytes = _storage_nbytes(obj, nbytes)
                        group_key = (device_kind, str(obj.dtype), owner, is_pinned)
                        g = groups.setdefault(group_key, {
                            "device": device_kind,
                            "dtype": str(obj.dtype),
                            "owner": owner,
                            "is_pinned": is_pinned,
                            "count": 0,
                            "nbytes": 0,
                        })
                        g["count"] += 1
                        g["nbytes"] += storage_bytes

                    if nbytes >= self._CENSUS_MIN_BYTES:
                        detail_rows.append({
                            "device": device_kind,
                            "shape": list(obj.shape),
                            "dtype": str(obj.dtype),
                            "nbytes_gb": round(nbytes / _BYTES_PER_GB, 4),
                            "is_pinned": is_pinned,
                            "owner": owner,
                        })

            # Primary section: aggregate groups, largest first, bounded.
            ranked_groups = sorted(groups.values(), key=lambda g: g["nbytes"], reverse=True)
            for g in ranked_groups[: self._CENSUS_MAX_GROUP_ROWS]:
                try:
                    row = self._snapshot()
                    row["kind"] = kind_group
                    if tag is not None:
                        row["tag"] = tag
                    row["device"] = g["device"]
                    row["dtype"] = g["dtype"]
                    row["owner"] = g["owner"]
                    row["is_pinned"] = g["is_pinned"]
                    row["count"] = g["count"]
                    row["nbytes_gb"] = round(g["nbytes"] / _BYTES_PER_GB, 4)
                    self._fh.write(json.dumps(row) + "\n")
                except Exception:
                    logger.debug("profiler: census group row write failed", exc_info=True)

            # Secondary section: original per-tensor >=64MB detail, unchanged.
            for r in detail_rows:
                try:
                    row = self._snapshot()
                    row["kind"] = kind_detail
                    if tag is not None:
                        row["tag"] = tag
                    row.update(r)
                    self._fh.write(json.dumps(row) + "\n")
                except Exception:
                    logger.debug("profiler: census row write failed", exc_info=True)

            if groups or detail_rows:
                self._fh.flush()
        except Exception:
            logger.debug(f"profiler: {device_kind} tensor census failed", exc_info=True)

    def _describe_owner(self, tensor: Any, start_time: float) -> str:
        """Best-effort name for whatever is keeping ``tensor`` alive.

        Walks ``gc.get_referrers`` up a few levels looking for a dict/list/set
        holding the tensor, then the object whose ``__dict__``/attribute is that
        container, so a hit reads like ``"LTXModelBundle.some_attr"`` rather than
        a bare object id. Capped in both depth and total time (shared with the
        caller's per-tensor budget check) — a partial or "unknown" answer is
        acceptable, this must never dominate profiling cost.

        On CPython 3.11+, an instance's ``__dict__`` is lazily materialised (the
        "managed dict"/inline-values optimisation): until something actually
        calls ``instance.__dict__``, attribute values are stored in a private
        array the GC attributes directly to the *owning object*, not to an
        intermediate dict. So ``gc.get_referrers(tensor)`` on a tensor stashed
        as ``bundle.diffusion_model = big_tensor`` returns ``bundle`` itself,
        not ``bundle.__dict__`` -- the plain-object case below handles that by
        scanning ``vars(ref)`` directly, which is exactly the shape a leaked
        cached-model attribute takes.
        """
        try:
            import gc
            current: Any = tensor
            for _ in range(self._CENSUS_MAX_OWNER_DEPTH):
                if time.monotonic() - start_time > self._CENSUS_TIME_BUDGET_S:
                    return "unknown (budget)"
                try:
                    referrers = gc.get_referrers(current)
                except Exception:
                    return "unknown"

                next_container: Any = None
                for ref in referrers[: self._CENSUS_MAX_REFERRERS]:
                    if isinstance(ref, dict):
                        name = None
                        for k, v in ref.items():
                            if v is current:
                                name = k
                                break
                        try:
                            owners = gc.get_referrers(ref)
                        except Exception:
                            owners = []
                        for o in owners[: self._CENSUS_MAX_REFERRERS]:
                            if getattr(o, "__dict__", None) is ref:
                                cls = type(o).__name__
                                return f"{cls}.{name}" if name else f"dict of {cls}"
                        if name is not None:
                            return f"dict[{name!r}]"
                        next_container = ref
                    elif isinstance(ref, (list, tuple, set)):
                        try:
                            owners = gc.get_referrers(ref)
                        except Exception:
                            owners = []
                        matched = False
                        for o in owners[: self._CENSUS_MAX_REFERRERS]:
                            attrs = getattr(o, "__dict__", None)
                            if not attrs:
                                continue
                            for k, v in attrs.items():
                                if v is ref:
                                    return f"{type(o).__name__}.{k} ({type(ref).__name__})"
                        if not matched:
                            next_container = ref
                    elif hasattr(ref, "__dict__") and not isinstance(ref, type):
                        # The lazily-materialised-dict case (see docstring): ``ref``
                        # is the owning instance, not an intermediate dict.
                        name = None
                        for k, v in vars(ref).items():
                            if v is current:
                                name = k
                                break
                        if name is not None:
                            return f"{type(ref).__name__}.{name}"
                if next_container is None:
                    break
                current = next_container
            return "unknown"
        except Exception:
            return "unknown"

    def _snapshot(self) -> dict:
        """One row's worth of stats.

        ``vram_alloc_gb``/``vram_reserved_gb`` are ``torch.cuda.memory_allocated``/
        ``memory_reserved`` — THIS PROCESS's PyTorch caching-allocator view only
        (live-tensor bytes / the allocator's whole cached pool), never what another
        process holds and never the same number as
        ``residency.free_vram_gb``/``mem_get_info`` (a driver-level, device-wide
        free-byte query across every process). The two can disagree: a low
        ``vram_alloc_gb`` next to a low ``mem_get_info`` free reading means
        something OTHER than this process's live tensors is occupying the card
        (another process, or this process's own idle-but-not-yet-``empty_cache``d
        reserved blocks) — not that a move to the GPU silently failed. The
        per-device ``weights_gb`` byte census
        (``memory/residency._weights_gb_by_device``) on the ``te.encode`` mark
        settles "did the weights actually move" independently of either metric.
        """
        rss_gb = read_process_rss_gb()
        try:
            avail_gb = get_system_memory().available_gb
        except Exception:
            avail_gb = None
        try:
            swap_gb = psutil.swap_memory().used / _BYTES_PER_GB
        except Exception:
            swap_gb = None
        try:
            cpu = self._proc.cpu_percent(interval=None)
        except Exception:
            cpu = None

        vram_alloc: dict = {}
        vram_reserved: dict = {}
        try:
            import torch

            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    try:
                        vram_alloc[str(i)] = _round(torch.cuda.memory_allocated(i) / _BYTES_PER_GB)
                        vram_reserved[str(i)] = _round(torch.cuda.memory_reserved(i) / _BYTES_PER_GB)
                    except Exception:
                        continue
        except Exception:
            logger.debug("profiler: vram snapshot failed", exc_info=True)

        return {
            "t": _round(time.monotonic()),
            "wall": _round(time.time()),
            "rss_gb": _round(rss_gb),
            "avail_gb": _round(avail_gb),
            "swap_gb": _round(swap_gb),
            "cpu": _round(cpu),
            "vram_alloc_gb": vram_alloc,
            "vram_reserved_gb": vram_reserved,
            "pinned_cum_gb": _round(pinned_cum_gb()),
        }


_profiler: Optional[GenerationProfiler] = None


def get_profiler() -> GenerationProfiler:
    """Process-wide singleton (mirrors ``get_residency_manager()``)."""
    global _profiler
    if _profiler is None:
        _profiler = GenerationProfiler()
    return _profiler
