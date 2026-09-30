import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.models.exceptions import ModelIndexingException
from src.features.models.indexer import ModelScanner
from src.features.models.indexing_coordinator import ModelIndexingCoordinator
from tests.features.models.test_indexing_scheduling import ThreadSpawner


def _no_spawn(target):
    return None


def _run_inline(target):
    target()


class FakeHookContext:
    def __init__(self, data=None):
        self.data = data or {}


class FakePluginRegistry:

    def __init__(self, blocked=False, block_reason="Indexing blocked by plugin"):
        self.blocked = blocked
        self.block_reason = block_reason

    def execute_hook(self, hook, initial_data):
        if self.blocked:
            return FakeHookContext({"blocked": True, "block_reason": self.block_reason}), True
        return FakeHookContext(), False


def _coordinator(scanner_result=None, scanner_raises=None, reconciler=None, backend_registry=None, plugin_registry=None, spawn=None):
    scanner = MagicMock()
    if scanner_raises is not None:
        scanner.index_models.side_effect = scanner_raises
    else:
        scanner.index_models.return_value = scanner_result or {"indexed": 1}

    return ModelIndexingCoordinator(
        spawn=spawn or _run_inline,
        model_repository=MagicMock(),
        plugin_registry=plugin_registry or FakePluginRegistry(),
        scanner=scanner,
        backend_registry=backend_registry,
        native_availability_projector=reconciler,
    )

def test_start_indexing_reconciles_native_availability_after_a_successful_scan():
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    backend_registry = MagicMock()
    coordinator = _coordinator(reconciler=reconciler, backend_registry=backend_registry)

    coordinator.start_indexing()

    reconciler.reconcile.assert_awaited_once_with(backend_registry)

def test_start_indexing_skips_reconcile_when_the_scan_fails():
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    coordinator = _coordinator(scanner_raises=OSError("disk unavailable"), reconciler=reconciler)

    coordinator.start_indexing()

    reconciler.reconcile.assert_not_awaited()

def test_reconcile_failure_does_not_raise():
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock(side_effect=RuntimeError("boom"))
    coordinator = _coordinator(reconciler=reconciler)

    coordinator.start_indexing()

    reconciler.reconcile.assert_awaited_once()

class FakeScanner:
    def __init__(self, unindexed_files):
        self._unindexed = list(unindexed_files)
        self.index_models_calls = 0
        self._progress_cb = None

    def count_unindexed(self):
        return {"total": len(self._unindexed), "by_type": {}}

    def set_progress_callback(self, callback):
        self._progress_cb = callback

    def index_models(self, cancel_check=None):
        indexed = list(self._unindexed)
        self._unindexed = []
        self.index_models_calls += 1
        if self._progress_cb:
            self._progress_cb(len(indexed), len(indexed), "done")
        return {
            "indexed": len(indexed),
            "found_on_disk": len(indexed),
            "failed_files": [],
            "cancelled": False,
        }

def _coordinator_with_scanner(scanner, reconciler=None):
    return ModelIndexingCoordinator(
        spawn=_run_inline,
        model_repository=MagicMock(),
        plugin_registry=FakePluginRegistry(),
        scanner=scanner,
        backend_registry=None,
        native_availability_projector=reconciler,
    )

def test_resume_interrupted_indexing_starts_and_indexes_every_unindexed_file():
    scanner = FakeScanner(["a.safetensors", "b.safetensors"])
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    coordinator = _coordinator_with_scanner(scanner, reconciler)

    started = coordinator.resume_interrupted_indexing()

    assert started is True
    assert scanner.index_models_calls == 1
    assert coordinator.count_unindexed()["total"] == 0
    assert coordinator.status()["trigger"] == "startup"

def test_resume_interrupted_indexing_does_not_start_when_nothing_is_unindexed():
    scanner = FakeScanner([])
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock()
    coordinator = _coordinator_with_scanner(scanner, reconciler)

    started = coordinator.resume_interrupted_indexing()

    assert started is False
    assert scanner.index_models_calls == 0



def test_second_start_indexing_call_joins_the_first_instead_of_restarting():
    coordinator = _coordinator(spawn=_no_spawn)

    first = coordinator.start_indexing(trigger="manual")
    second = coordinator.start_indexing(trigger="manual")

    assert first["state"] == "scanning"
    assert second["state"] == "scanning"
    assert second["started_at"] == first["started_at"]

def test_status_never_walks_disk_for_unindexed_counts():
    coordinator = _coordinator()

    status = coordinator.status()

    assert "unindexed" not in status
    coordinator.scanner.count_unindexed.assert_not_called()

def test_veto_sets_blocked_state_visible_via_status():
    plugin_registry = FakePluginRegistry(blocked=True, block_reason="maintenance in progress")
    coordinator = ModelIndexingCoordinator(
        spawn=_no_spawn,
        model_repository=MagicMock(), plugin_registry=plugin_registry, scanner=MagicMock(),
    )

    with pytest.raises(ModelIndexingException):
        coordinator.start_indexing()

    status = coordinator.status()
    assert status["state"] == "blocked"
    assert status["error"] == "maintenance in progress"

def test_start_indexing_sets_failed_state_with_error_message_on_crash():
    coordinator = _coordinator(scanner_raises=RuntimeError("disk exploded"))
    coordinator.start_indexing()

    status = coordinator.status()
    assert status["state"] == "failed"
    assert "disk exploded" in status["error"]

def test_zero_files_on_disk_is_reported_explicitly():
    coordinator = _coordinator(scanner_result={
        "indexed": 0, "found_on_disk": 0, "failed_files": [], "cancelled": False, "total": 0,
    })
    coordinator.start_indexing()

    status = coordinator.status()
    assert status["found_on_disk"] == 0
    assert status["state"] == "done"

def test_per_file_failures_from_the_scan_are_recorded_in_status():
    failed = [{"path": f"f{i}.safetensors", "error": "boom"} for i in range(3)]
    coordinator = _coordinator(scanner_result={
        "indexed": 1, "failed_files": failed, "found_on_disk": 4, "cancelled": False,
    })
    coordinator.start_indexing()

    status = coordinator.status()
    assert status["failed_files_total"] == 3
    assert status["failed_files"] == failed

def test_progress_updates_processed_and_total_via_scanner_callback():
    class ProgressScanner:
        def __init__(self):
            self._cb = None

        def set_progress_callback(self, cb):
            self._cb = cb

        def index_models(self, cancel_check=None):
            self._cb(0, 0, "Scanning...")
            self._cb(0, 3, "Looking...")
            self._cb(1, 3, "Checked a")
            self._cb(2, 3, "Checked b")
            self._cb(3, 3, "Checked c")
            return {"indexed": 3, "found_on_disk": 3, "failed_files": [], "cancelled": False}

    coordinator = ModelIndexingCoordinator(
        spawn=_run_inline,
        model_repository=MagicMock(), plugin_registry=FakePluginRegistry(), scanner=ProgressScanner(),
    )
    coordinator.start_indexing()

    status = coordinator.status()
    assert status["total"] == 3
    assert status["processed"] == 3
    assert status["state"] == "done"



def test_start_indexing_while_the_scan_is_executing_does_not_spawn_a_second_worker():
    started = threading.Event()
    release = threading.Event()
    call_count = {"n": 0}

    class BlockingScanner:
        def set_progress_callback(self, cb):
            pass

        def index_models(self, cancel_check=None):
            call_count["n"] += 1
            started.set()
            release.wait(timeout=5)
            return {"indexed": 0, "found_on_disk": 0, "failed_files": [], "cancelled": False}

    spawner = ThreadSpawner()
    coordinator = ModelIndexingCoordinator(
        spawn=spawner,
        model_repository=MagicMock(), plugin_registry=FakePluginRegistry(), scanner=BlockingScanner(),
    )
    coordinator.start_indexing()
    assert started.wait(timeout=5)

    coordinator.start_indexing()

    assert len(spawner.threads) == 1
    release.set()
    spawner.join()
    assert call_count["n"] == 1
    assert coordinator.status()["state"] == "done"


def test_cancel_and_restart_returns_immediately_without_waiting_for_the_running_scan():
    started = threading.Event()
    release = threading.Event()

    class IgnoresCancelScanner:
        def __init__(self):
            self.calls = 0

        def set_progress_callback(self, cb):
            pass

        def index_models(self, cancel_check=None):
            self.calls += 1
            started.set()
            release.wait(timeout=5)
            return {"indexed": 0, "found_on_disk": 1, "failed_files": [], "cancelled": False}

    scanner = IgnoresCancelScanner()
    spawner = ThreadSpawner()
    coordinator = ModelIndexingCoordinator(
        spawn=spawner,
        model_repository=MagicMock(), plugin_registry=FakePluginRegistry(), scanner=scanner,
    )
    coordinator.start_indexing(trigger="manual")
    assert started.wait(timeout=5)

    before = time.monotonic()
    result = coordinator.cancel_and_restart(trigger="location_change")
    elapsed = time.monotonic() - before

    assert elapsed < 1.0
    assert result["restart_pending"] is True
    assert result["state"] in ("scanning", "indexing")

    release.set()
    spawner.join()

    assert scanner.calls == 2
    status = coordinator.status()
    assert status["trigger"] == "location_change"
    assert status["restart_pending"] is False
    assert status["state"] == "done"


def test_cancel_and_restart_starts_normally_when_nothing_is_running():
    coordinator = _coordinator(spawn=_no_spawn)

    result = coordinator.cancel_and_restart(trigger="location_change")

    assert result["state"] == "scanning"
    assert result["trigger"] == "location_change"
    assert result["restart_pending"] is False


def test_cancel_and_restart_while_a_start_is_still_queued_does_not_cancel_or_restart_it():
    queued = []
    coordinator = _coordinator(spawn=queued.append)

    coordinator.start_indexing(trigger="manual")
    result = coordinator.cancel_and_restart(trigger="roots_change")

    assert len(queued) == 1
    assert result["restart_pending"] is False
    assert coordinator._cancel_event.is_set() is False
    queued[0]()
    assert coordinator.status()["state"] == "done"
    assert coordinator.scanner.index_models.call_count == 1


def test_a_restart_requested_during_the_post_scan_window_is_scanned_after_the_current_run():
    hold = threading.Event()
    entered_reconcile = threading.Event()

    class SlowReconciler:
        async def reconcile(self, backend_registry):
            entered_reconcile.set()
            await asyncio.to_thread(hold.wait, 5)

    scanner = FakeScanner(["a.safetensors"])
    spawner = ThreadSpawner()
    coordinator = ModelIndexingCoordinator(
        spawn=spawner,
        model_repository=MagicMock(),
        plugin_registry=FakePluginRegistry(),
        scanner=scanner,
        native_availability_projector=SlowReconciler(),
    )
    coordinator.start_indexing(trigger="manual")
    assert entered_reconcile.wait(timeout=5)

    first = coordinator.cancel_and_restart(trigger="roots_change")
    second = coordinator.cancel_and_restart(trigger="roots_change")

    assert first["state"] == "scanning"
    assert second["restart_pending"] is False
    hold.set()
    spawner.join()

    status = coordinator.status()
    assert status["state"] == "done"
    assert status["trigger"] == "roots_change"
    assert scanner.index_models_calls == 2


def test_an_after_index_hook_that_raises_marks_the_run_failed_and_a_later_start_works():
    class ExplodingHooks(FakePluginRegistry):
        armed = True

        def execute_hook(self, hook, initial_data):
            if self.armed and "result" in initial_data:
                self.armed = False
                raise RuntimeError("hook exploded")
            return super().execute_hook(hook, initial_data)

    scanner = FakeScanner(["a.safetensors"])
    coordinator = ModelIndexingCoordinator(
        spawn=_run_inline,
        model_repository=MagicMock(), plugin_registry=ExplodingHooks(), scanner=scanner,
    )

    coordinator.start_indexing(trigger="manual")

    status = coordinator.status()
    assert status["state"] == "failed"
    assert "hook exploded" in status["error"]

    coordinator.start_indexing(trigger="manual")

    assert coordinator.status()["state"] == "done"
    assert scanner.index_models_calls == 2


def test_a_spawn_that_raises_marks_the_run_failed_instead_of_leaving_it_scanning():
    def failing_spawn(target):
        raise RuntimeError("cannot start new thread")

    coordinator = _coordinator(spawn=failing_spawn)

    with pytest.raises(ModelIndexingException):
        coordinator.start_indexing(trigger="manual")

    status = coordinator.status()
    assert status["state"] == "failed"
    assert "cannot start new thread" in status["error"]
    assert status["finished_at"] is not None

    coordinator.spawn_replaced = True
    coordinator._spawn = _run_inline
    coordinator.start_indexing(trigger="manual")
    assert coordinator.status()["state"] == "done"


def _real_scanner(tmp_path, root_id="r_home", model_type="checkpoint"):
    from src.platform.database.rows import now_iso
    from src.platform.filesystem.model_roots import ModelRoot, ModelRootResolver, TypeDir, _Snapshot, root_path_key
    from src.platform.filesystem.model_roots_repository import ModelRootRepository
    from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY

    root_dir = tmp_path / root_id
    type_dir = root_dir / MODEL_TYPE_TO_DIRECTORY[model_type]
    type_dir.mkdir(parents=True, exist_ok=True)

    now = now_iso()
    key = root_path_key(str(root_dir))
    repo = ModelRootRepository()
    repo.insert_root(root_id, root_id, str(root_dir), key, "library", False, False, now)
    binding_id = repo.insert_binding(root_id, model_type, MODEL_TYPE_TO_DIRECTORY[model_type], 1000, False)

    resolver = ModelRootResolver(repository=None, probe=None, base_dir=Path.cwd())
    root = ModelRoot(
        id=root_id, label=root_id, path=root_dir, kind="library", read_only=False,
        case_insensitive=False, state="online", state_reason=None, raw_path=str(root_dir),
    )
    binding = TypeDir(root_id=root_id, model_type=model_type, path=type_dir, position=1000, is_write=False,
                       subdir=MODEL_TYPE_TO_DIRECTORY[model_type], binding_id=binding_id)
    resolver._snapshot = _Snapshot(
        roots=(root,), roots_by_id={root_id: root}, type_dirs_by_type={model_type: (binding,)},
    )
    resolver._probe = _AlwaysOnlineProbe()
    return ModelScanner(resolver), root_dir, type_dir


class _AlwaysOnlineProbe:
    def state(self, root):
        return ("online", None)


def _coordinator_for(scanner):
    return ModelIndexingCoordinator(
        spawn=_run_inline,
        model_repository=MagicMock(), plugin_registry=FakePluginRegistry(), scanner=scanner,
    )


def test_status_reports_per_root_state_and_last_run_counts(tmp_path, mock_db):
    scanner, root_dir, type_dir = _real_scanner(tmp_path)
    (type_dir / "a.safetensors").write_bytes(b"content")
    coordinator = _coordinator_for(scanner)
    coordinator.start_indexing(trigger="manual")

    coordinator.start_indexing()

    status = coordinator.status()
    assert status["state"] == "done"
    root_entries = {r["root_id"]: r for r in status["roots"]}
    assert root_entries["r_home"]["state"] == "online"
    assert root_entries["r_home"]["found"] == 1
    assert root_entries["r_home"]["indexed"] == 1
    assert root_entries["r_home"]["failed"] == 0
    assert status["conflicts"] == []
    assert status["duplicates"] == []


def test_index_path_indexes_one_file_without_touching_state(tmp_path, mock_db):
    scanner, root_dir, type_dir = _real_scanner(tmp_path)
    path = type_dir / "single.safetensors"
    path.write_bytes(b"single-file-content")
    coordinator = _coordinator_for(scanner)

    result = coordinator.index_path(str(path))

    assert result["indexed"] is True
    assert result["model_id"] is not None
    status = coordinator.status()
    assert status["state"] == "idle"
    assert status["last_single_index"]["path"] == str(path)
    assert status["last_single_index"]["model_id"] == result["model_id"]


def test_index_path_refuses_a_path_outside_every_root(tmp_path, mock_db):
    scanner, root_dir, type_dir = _real_scanner(tmp_path)
    outside = tmp_path / "elsewhere" / "x.safetensors"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x")
    coordinator = _coordinator_for(scanner)

    with pytest.raises(ModelIndexingException):
        coordinator.index_path(str(outside))


def test_index_path_and_a_full_run_share_the_scanners_write_lock(tmp_path, mock_db):
    scanner, root_dir, type_dir = _real_scanner(tmp_path)
    coordinator = _coordinator_for(scanner)

    assert coordinator.scanner._write_lock is scanner._write_lock


def test_cleanup_deleted_models_removes_missing_locations_on_online_roots_only(tmp_path, mock_db):
    scanner, root_dir, type_dir = _real_scanner(tmp_path)
    path = type_dir / "gone.safetensors"
    path.write_bytes(b"will vanish")
    coordinator = _coordinator_for(scanner)
    coordinator.start_indexing(trigger="manual")

    path.unlink()
    coordinator.start_indexing(trigger="manual")

    result = coordinator.cleanup_deleted_models()

    assert result["deleted_locations"] == 1
    assert scanner.locations.list_for_root_type("r_home", "checkpoint") == []


def test_skipped_duplicates_are_kept_in_status_after_the_run_and_are_not_failures(tmp_path, mock_db):
    scanner, root_dir, type_dir = _real_scanner(tmp_path)
    (type_dir / "3361846.safetensors").write_bytes(b"same-bytes")
    coordinator = _coordinator_for(scanner)
    coordinator.start_indexing(trigger="manual")
    (type_dir / "copy.safetensors").write_bytes(b"same-bytes")

    coordinator.start_indexing(trigger="manual")

    status = coordinator.status()
    assert status["state"] == "done"
    assert status["failed_files"] == []
    assert status["failed_files_total"] == 0
    assert status["roots"][0]["failed"] == 0
    assert status["skipped_duplicates_total"] == 1
    entry = status["skipped_duplicates"][0]
    assert entry["path"] == (type_dir / "copy.safetensors").as_posix()
    assert entry["root"] == "r_home"
    assert entry["same_as"]["model_type"] == "checkpoint"
    assert entry["same_as"]["root"] == "r_home"
    assert entry["same_as"]["path"] == (type_dir / "3361846.safetensors").as_posix()


def test_skipped_duplicates_survive_a_cancelled_run(tmp_path, threadsafe_db):
    scanner, root_dir, type_dir = _real_scanner(tmp_path)
    (type_dir / "3361846.safetensors").write_bytes(b"same-bytes")
    (type_dir / "copy.safetensors").write_bytes(b"same-bytes")
    coordinator = _coordinator_for(scanner)
    coordinator.start_indexing(trigger="manual")
    assert coordinator.status()["skipped_duplicates_total"] == 1

    scanner.index_models = lambda cancel_check=None: {
        "indexed": 0, "found_on_disk": 2, "failed_files": [], "skipped_duplicates": [], "cancelled": True,
    }
    coordinator.start_indexing(trigger="manual")

    status = coordinator.status()
    assert status["state"] == "cancelled"
    assert status["skipped_duplicates_total"] == 1
    assert len(status["skipped_duplicates"]) == 1


def test_index_path_reports_a_same_bytes_copy_as_a_duplicate_instead_of_a_bare_failure(tmp_path, mock_db):
    scanner, root_dir, type_dir = _real_scanner(tmp_path)
    original = type_dir / "3361846.safetensors"
    original.write_bytes(b"same-bytes")
    copy = type_dir / "copy.safetensors"
    copy.write_bytes(b"same-bytes")
    coordinator = _coordinator_for(scanner)
    assert coordinator.index_path(str(original))["indexed"] is True

    result = coordinator.index_path(str(copy))

    assert result["indexed"] is False
    assert result["model_id"] is None
    assert result["duplicate_of"]["path"] == original.as_posix()
    assert result["duplicate_of"]["model_type"] == "checkpoint"
