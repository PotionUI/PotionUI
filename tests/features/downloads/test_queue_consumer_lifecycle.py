"""
Regression coverage: the download queue consumer used to be
started on whatever asyncio loop happened to be current at the moment the
worker started - almost always a *throwaway* loop (see
`persistent_loop.py`'s module docstring for the three call sites and why
each one is throwaway). The consumer's tasks died the instant that loop went
away, leaving queued downloads stuck at `status='pending'` forever.

These tests drive `DownloadQueue` through a REAL `DownloadWorker` and a
small in-memory fake repository (no sqlite, no real network - the actual
HTTP fetch is monkeypatched out), reproducing the two broken call shapes
plus the one that used to work, all against the SAME manager code path.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.downloads.models import Download, DownloadStatus
from src.features.downloads.queue import DownloadQueue
from src.features.downloads.worker import DownloadWorker
from src.features.setup.executors._async_bridge import run_sync

_POLL_TIMEOUT_SECONDS = 5.0
_POLL_INTERVAL_SECONDS = 0.02


class FakeDownloadRepository:
    """In-memory stand-in for `DownloadRepository` - just enough state
    (a dict keyed by id, mutated the same way the real sqlite-backed
    repository would be) for the worker's full pending -> downloading ->
    completed flow to be observable from a test."""

    def __init__(self) -> None:
        self._rows: Dict[str, Download] = {}
        self._next_id = 0

    def create(self, download: Download) -> Download:
        self._next_id += 1
        download.id = download.id or f"dl-{self._next_id}"
        self._rows[download.id] = download
        return download

    def get_by_id(self, download_id: str) -> Optional[Download]:
        return self._rows.get(download_id)

    def get_active(self) -> List[Download]:
        return [d for d in self._rows.values() if d.status == DownloadStatus.DOWNLOADING]

    def get_pending(self, limit: Optional[int] = None) -> List[Download]:
        pending = [d for d in self._rows.values() if d.status == DownloadStatus.PENDING]
        return pending[:limit] if limit else pending

    def update_status(self, download_id: str, status: DownloadStatus, error_message: Optional[str] = None) -> bool:
        row = self._rows.get(download_id)
        if row is None:
            return False
        row.status = status
        row.error_message = error_message
        return True

    def update_progress(self, download_id, progress, downloaded_bytes, speed_bytes_per_sec=None) -> bool:
        row = self._rows.get(download_id)
        if row is None:
            return False
        row.progress = progress
        row.downloaded_bytes = downloaded_bytes
        row.speed_bytes_per_sec = speed_bytes_per_sec
        return True

    def increment_retry(self, download_id: str) -> int:
        row = self._rows.get(download_id)
        if row is None:
            return 0
        row.retry_count += 1
        return row.retry_count

    def update_total_bytes(self, download_id: str, total_bytes) -> bool:
        row = self._rows.get(download_id)
        if row is None:
            return False
        row.total_bytes = total_bytes
        return True

    def get_children(self, group_id: str) -> List[Download]:
        return [d for d in self._rows.values() if d.group_id == group_id]

    def refresh_group(self, group_id: str):
        parent = self._rows.get(group_id)
        children = self.get_children(group_id)
        if parent is None or not children:
            return parent, False
        statuses = {c.status for c in children}
        old = parent.status
        if DownloadStatus.DOWNLOADING in statuses or DownloadStatus.PENDING in statuses:
            parent.status = DownloadStatus.DOWNLOADING
        elif DownloadStatus.FAILED in statuses:
            parent.status = DownloadStatus.FAILED
        elif statuses == {DownloadStatus.COMPLETED}:
            parent.status = DownloadStatus.COMPLETED
            parent.progress = 1.0
        return parent, parent.status != old


def _fake_plugin_registry() -> Mock:
    registry = Mock()
    context = Mock()
    context.data = {}
    registry.execute_hook.return_value = (context, [])
    return registry


async def _fake_download_file(self, download: Download) -> bool:
    """Stands in for `DownloadWorker._download_file` - the real method does
    an aiohttp GET and writes chunks to disk. No network, no disk: just
    mark the transfer as having "completed" instantly."""
    download.total_bytes = 128
    download.downloaded_bytes = 128
    return True


@pytest.fixture
def repo() -> FakeDownloadRepository:
    return FakeDownloadRepository()


@pytest.fixture
def manager(repo):
    settings = Mock()
    settings.get_setting.return_value = None
    mgr = DownloadQueue(
        download_repository=repo,
        plugin_registry=_fake_plugin_registry(),
        settings=settings,
        connection_hub=AsyncMock(),
    )
    yield mgr
    # Daemon threads would otherwise outlive the test. Stop the worker
    # gracefully first (closes its aiohttp session, cancels its tasks
    # cleanly) when its loop is still alive; a test that already killed the
    # loop itself (TestSelfHealing) just gets a plain shutdown.
    if mgr._persistent_loop.is_alive() and mgr.worker is not None:
        try:
            future = asyncio.run_coroutine_threadsafe(mgr.worker.stop(), mgr._worker_loop)
            future.result(timeout=5)
        except Exception:
            pass
    mgr._persistent_loop.shutdown()


async def _wait_for_status(repo: FakeDownloadRepository, download_id: str, *statuses: DownloadStatus) -> DownloadStatus:
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    last = None
    while time.monotonic() < deadline:
        row = repo.get_by_id(download_id)
        last = row.status if row else None
        if last in statuses:
            return last
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(f"download {download_id} never reached {statuses} (last seen: {last})")


class TestNormalStartupPathStillWorks:
    """The one call shape that already worked before the fix: everything
    awaited directly on one long-lived loop, the way an admin enabling the
    plugin from a live server would exercise it. Must keep working exactly
    as before."""

    async def test_direct_await_start_then_queue_completes(self, manager, repo, monkeypatch):
        monkeypatch.setattr(DownloadWorker, "_download_file", _fake_download_file)

        await manager.start()
        download = await manager.queue_model_download(
            url="https://example.com/model.safetensors",
            filename="model.safetensors",
        )

        status = await _wait_for_status(repo, download.id, DownloadStatus.COMPLETED, DownloadStatus.FAILED)
        assert status == DownloadStatus.COMPLETED


class TestThrowawayLoopOrphaning:
    """The two broken call shapes. Both queue through
    `_async_bridge.run_sync()` - the exact function a setup-run executor
    uses - which either (a) runs `asyncio.run()` on the calling thread when
    no loop is already running (mirrors process boot, before uvicorn's own
    loop exists), or (b) spins a brand-new thread+loop per call when a loop
    IS already running (mirrors a FastAPI route handler / setup executor
    mid-request). Before the fix, either shape orphaned the consumer the
    moment its throwaway loop went away and the download sat at 'pending'
    forever."""

    def test_survives_run_sync_with_no_loop_already_running(self, manager, repo, monkeypatch):
        """Plain synchronous caller - `run_sync` takes its `asyncio.run()`
        branch for both the worker start and the queue call, each getting
        its own throwaway loop."""
        monkeypatch.setattr(DownloadWorker, "_download_file", _fake_download_file)

        run_sync(manager.start())
        download = run_sync(
            manager.queue_model_download(
                url="https://example.com/model.safetensors",
                filename="model.safetensors",
            )
        )

        # The polling loop itself needs a running loop; run it via run_sync too.
        status = run_sync(_wait_for_status(repo, download.id, DownloadStatus.COMPLETED, DownloadStatus.FAILED))
        assert status == DownloadStatus.COMPLETED

    async def test_survives_run_sync_thread_pool_branch(self, manager, repo, monkeypatch):
        """Async caller (a loop is already running in this thread, exactly
        like a FastAPI route handler) - `run_sync` takes its
        ThreadPoolExecutor branch: a brand-new thread running a brand-new
        loop per call, discarded the instant each call returns. This is the
        literal shape `ArtifactsFetchExecutor` uses via
        `src.features.setup.executors._async_bridge.run_sync`."""
        monkeypatch.setattr(DownloadWorker, "_download_file", _fake_download_file)

        run_sync(manager.start())
        download = run_sync(
            manager.queue_model_download(
                url="https://example.com/model.safetensors",
                filename="model.safetensors",
            )
        )

        status = await _wait_for_status(repo, download.id, DownloadStatus.COMPLETED, DownloadStatus.FAILED)
        assert status == DownloadStatus.COMPLETED

    async def test_queue_before_any_start_call_self_starts_via_run_sync(self, manager, repo, monkeypatch):
        """A setup run's `artifacts.fetch` step never calls `start()` itself
        - it only ever queues. The lazy self-start (`_ensure_worker_ready`)
        must kick in on first queue use even when `start()` was never called
        at all, still through the throwaway-loop bridge."""
        monkeypatch.setattr(DownloadWorker, "_download_file", _fake_download_file)

        assert manager.worker is None

        download = run_sync(
            manager.queue_model_download(
                url="https://example.com/model.safetensors",
                filename="model.safetensors",
            )
        )

        status = await _wait_for_status(repo, download.id, DownloadStatus.COMPLETED, DownloadStatus.FAILED)
        assert status == DownloadStatus.COMPLETED


class TestSelfHealing:
    async def test_next_queue_use_recovers_after_the_consumer_thread_dies(self, manager, repo, monkeypatch):
        """A consumer that died (thread crashed, killed, whatever) must be
        transparently replaced on the next queue use rather than silently
        swallowing the enqueue - 'self-healing beats perfect lifecycle
        bookkeeping' per the fix requirements."""
        monkeypatch.setattr(DownloadWorker, "_download_file", _fake_download_file)

        await manager.start()
        assert manager._persistent_loop.is_alive() is True

        # Kill the consumer's loop/thread out from under the manager, the
        # way a throwaway loop dying used to (silently, with nothing left
        # running it).
        manager._persistent_loop.shutdown()
        assert manager._persistent_loop.is_alive() is False

        download = await manager.queue_model_download(
            url="https://example.com/model2.safetensors",
            filename="model2.safetensors",
        )

        status = await _wait_for_status(repo, download.id, DownloadStatus.COMPLETED, DownloadStatus.FAILED)
        assert status == DownloadStatus.COMPLETED


class TestEnsureLocalHfRepoThroughRealWorker:
    """The sync lazy-loader path end to end: `ensure_local_hf_repo` called
    from a plain thread with no event loop, queueing a grouped job whose
    children run on the REAL worker (byte fetch monkeypatched), the parent
    aggregate completing via `refresh_group`."""

    def test_sync_wait_completes_against_real_worker(self, manager, repo, monkeypatch, tmp_path):
        monkeypatch.setattr(DownloadWorker, "_download_file", _fake_download_file)
        monkeypatch.setattr(
            DownloadQueue,
            "_enumerate_hf_repo",
            lambda self, repo_id, revision, allow_patterns: [
                ("model.safetensors", 64, "https://huggingface.co/org/tiny/resolve/main/model.safetensors"),
                ("config.json", 64, "https://huggingface.co/org/tiny/resolve/main/config.json"),
            ],
        )
        # destination_dir is now contained inside the configured depot (see
        # `_resolve_contained_dir`), so the e2e target must actually live
        # under it rather than an arbitrary absolute path.
        manager.settings.default_model_directory = str(tmp_path)
        target_dir = str(tmp_path / "e2e-target")

        result = manager.ensure_local_hf_repo(
            "org/tiny", target_dir, poll_interval=0.02, timeout=_POLL_TIMEOUT_SECONDS
        )

        assert str(result) == target_dir
        parents = [d for d in repo._rows.values() if d.group_id is None]
        assert len(parents) == 1
        assert parents[0].status == DownloadStatus.COMPLETED
        children = repo.get_children(parents[0].id)
        assert len(children) == 2
        assert all(c.status == DownloadStatus.COMPLETED for c in children)
