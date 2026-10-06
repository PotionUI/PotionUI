"""Per-generation resource profiler.

Tracks host and device memory pressure across a generation so RSS growth can
be attributed to a specific pipe/model transition instead of the process as a
whole. Produces a ``profile.jsonl`` artifact with one row per periodic sample
plus one row per named event ("chokepoint" in the pipeline/model
lifecycle/native engine), each carrying an RSS / available RAM / swap / CPU /
per-device VRAM / pinned-memory snapshot. ``scripts/profile_report.py`` turns
that into a stage table and a top-N RSS-jump report.

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

import gc
import json
import logging
import threading
import time
import warnings
from pathlib import Path
from typing import Any, Optional

import psutil

from src.platform.runtime.system_memory import get_system_memory
from src.platform.settings.runtime_flags import runtime_flag

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1024 ** 3


# -- enable/disable -----------------------------------------------------------


def profiling_enabled() -> bool:
    return bool(runtime_flag("profiling_enabled"))


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
        self._out_dir: Optional[Path] = None
        self._fh = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._last_flush = 0.0
        self._log_handler: Optional[logging.Handler] = None
        self._census_lock = threading.Lock()
        self._census_thread: Optional[threading.Thread] = None
        self._census_cancel: Optional[threading.Event] = None
        self._census_generation_id: Optional[str] = None

    def start(self, generation_id: str, out_dir: str | Path) -> None:
        self._cancel_pending_census(generation_id)
        if not profiling_enabled():
            return
        replaced = None
        try:
            with self._lock:
                if self._thread is not None:
                    logger.debug(
                        "profiler: start(%s) while %s is active; replacing",
                        generation_id, self._generation_id,
                    )
                    replaced = self._stop_locked()

                out_dir = Path(out_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                profile_path = out_dir / "profile.jsonl"
                self._fh = open(profile_path, "a", buffering=1)
                self._attach_log_handler(out_dir)
                self._generation_id = generation_id
                self._out_dir = out_dir
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
            self._join_sampler(replaced)
            return
        self._join_sampler(replaced)
        self.mark("generation.start")

    @property
    def out_dir(self) -> Optional[Path]:
        return self._out_dir

    def stop(self, generation_id: str) -> None:
        handoff = None
        sampler = None
        try:
            with self._lock:
                if self._generation_id != generation_id or self._fh is None:
                    return
                self.mark("generation.end")
                if runtime_flag("profiling_census"):
                    handoff = self._fh
                    self._fh = None
                sampler = self._stop_locked()
        except Exception:
            logger.debug("profiler: stop failed", exc_info=True)
        self._join_sampler(sampler)
        if handoff is not None:
            self._start_census(generation_id, handoff)

    def wait_for_census(self, timeout: Optional[float] = None) -> bool:
        with self._census_lock:
            thread = self._census_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _start_census(self, generation_id: str, fh: Any) -> None:
        cancel = threading.Event()
        thread = threading.Thread(
            target=self._run_census,
            args=(generation_id, fh, cancel),
            name=f"gen-profiler-census-{generation_id}",
            daemon=True,
        )
        with self._census_lock:
            self._census_thread = thread
            self._census_cancel = cancel
            self._census_generation_id = generation_id
        try:
            thread.start()
        except Exception:
            logger.debug("profiler: census thread failed to start", exc_info=True)
            self._finish_census(thread, fh)

    def _cancel_pending_census(self, next_generation_id: str) -> None:
        with self._census_lock:
            thread, cancel, census_id = self._census_thread, self._census_cancel, self._census_generation_id
        if thread is None or cancel is None or not thread.is_alive():
            return
        cancel.set()
        logger.info(
            "profiler: skipping the memory census for %s because generation %s is starting",
            census_id, next_generation_id,
        )

    def _run_census(self, generation_id: str, fh: Any, cancel: threading.Event) -> None:
        try:
            if cancel.wait(self._CENSUS_DELAY_S):
                self._write_census_skipped(fh)
                return
            rows = self._collect_tensor_census(
                ("cpu", "cuda"), budget_s=self._CENSUS_BACKGROUND_BUDGET_S, cancel=cancel,
            )
            if rows is None:
                self._write_census_skipped(fh)
                return
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        except Exception:
            logger.debug("profiler: census for %s failed", generation_id, exc_info=True)
        finally:
            self._finish_census(threading.current_thread(), fh)

    def _write_census_skipped(self, fh: Any) -> None:
        try:
            row = self._snapshot(include_cpu=False)
            row["kind"] = "event"
            row["event"] = "census.skipped"
            fh.write(json.dumps(row) + "\n")
        except Exception:
            logger.debug("profiler: census.skipped row write failed", exc_info=True)

    def _finish_census(self, thread: threading.Thread, fh: Any) -> None:
        try:
            fh.flush()
            fh.close()
        except Exception:
            logger.debug("profiler: failed closing census writer", exc_info=True)
        with self._census_lock:
            if self._census_thread is thread:
                self._census_thread = None
                self._census_cancel = None
                self._census_generation_id = None

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
        if self._fh is None or not runtime_flag("profiling_census"):
            return
        try:
            with self._lock:
                if self._fh is None:
                    return
                rows = self._collect_tensor_census(
                    ("cpu", "cuda"), budget_s=self._CENSUS_TIME_BUDGET_S,
                    kind_group="census_group_now", kind_detail="census_now", tag=tag,
                )
                for row in rows or ():
                    self._fh.write(json.dumps(row) + "\n")
                if rows:
                    self._fh.flush()
        except Exception:
            logger.debug("profiler: census_now(%s) failed", tag, exc_info=True)

    def _stop_locked(self) -> Optional[threading.Thread]:
        if self._stop_event is not None:
            self._stop_event.set()
        thread, self._thread = self._thread, None
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                logger.debug("profiler: failed closing writer", exc_info=True)
        self._fh = None
        self._generation_id = None
        self._out_dir = None
        self._stop_event = None
        self._detach_log_handler()
        return thread

    @staticmethod
    def _join_sampler(thread: Optional[threading.Thread]) -> None:
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

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
        if self._fh is None:
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
                    if stop_event.is_set():
                        break
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
    _CENSUS_BACKGROUND_BUDGET_S = 6.0
    _CENSUS_DELAY_S = 1.0
    _CENSUS_MAX_OWNER_DEPTH = 3
    _CENSUS_MAX_REFERRERS = 20
    _CENSUS_MAX_GROUP_ROWS = 200

    def _collect_tensor_census(
        self, device_kinds: tuple[str, ...], *, budget_s: float,
        kind_group: str = "census_group", kind_detail: str = "census",
        tag: Optional[str] = None, cancel: Optional[threading.Event] = None,
    ) -> Optional[list[dict]]:
        try:
            import torch as _torch
        except Exception:
            return []
        try:
            start = time.monotonic()
            try:
                objects = gc.get_objects()
            except Exception:
                logger.debug("profiler: census gc.get_objects failed", exc_info=True)
                return []

            wanted = frozenset(device_kinds)
            detail_rows: list[dict] = []
            groups: dict[tuple, dict] = {}
            owner_cache: dict[Any, str] = {}
            seen_storage: set = set()

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for obj in objects:
                    if cancel is not None and cancel.is_set():
                        return None
                    if time.monotonic() - start > budget_s:
                        break
                    try:
                        if not isinstance(obj, _torch.Tensor):
                            continue
                        if obj.is_meta:
                            continue
                        device_kind = obj.device.type
                        if device_kind not in wanted:
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

                    storage_key = (device_kind, _tensor_storage_key(obj))
                    first_sighting = storage_key not in seen_storage
                    if first_sighting:
                        seen_storage.add(storage_key)

                    owner = owner_cache.get(storage_key)
                    if owner is None:
                        owner = self._describe_owner(obj, start, budget_s)
                        owner_cache[storage_key] = owner

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
            del objects

            base = self._snapshot(include_cpu=False)
            rows: list[dict] = []
            for device_kind in device_kinds:
                ranked = sorted(
                    (g for g in groups.values() if g["device"] == device_kind),
                    key=lambda g: g["nbytes"], reverse=True,
                )
                for g in ranked[: self._CENSUS_MAX_GROUP_ROWS]:
                    row = dict(base)
                    row["kind"] = kind_group
                    if tag is not None:
                        row["tag"] = tag
                    row["device"] = g["device"]
                    row["dtype"] = g["dtype"]
                    row["owner"] = g["owner"]
                    row["is_pinned"] = g["is_pinned"]
                    row["count"] = g["count"]
                    row["nbytes_gb"] = round(g["nbytes"] / _BYTES_PER_GB, 4)
                    rows.append(row)
            for device_kind in device_kinds:
                for r in detail_rows:
                    if r["device"] != device_kind:
                        continue
                    row = dict(base)
                    row["kind"] = kind_detail
                    if tag is not None:
                        row["tag"] = tag
                    row.update(r)
                    rows.append(row)
            return rows
        except Exception:
            logger.debug("profiler: tensor census failed", exc_info=True)
            return []

    def _describe_owner(self, tensor: Any, start_time: float, budget_s: Optional[float] = None) -> str:
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
            budget = self._CENSUS_TIME_BUDGET_S if budget_s is None else budget_s
            current: Any = tensor
            for _ in range(self._CENSUS_MAX_OWNER_DEPTH):
                if time.monotonic() - start_time > budget:
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

    def _snapshot(self, include_cpu: bool = True) -> dict:
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
        cpu = None
        if include_cpu:
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
    """Process-wide singleton (mirrors ``get_residency_registry()``)."""
    global _profiler
    if _profiler is None:
        _profiler = GenerationProfiler()
    return _profiler
