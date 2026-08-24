"""hf_repo jobs: enumeration, grouping, and the synchronous wait wrapper."""

import os
import sys
import types
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.downloads.exceptions import DownloadOperationException, DownloadQueueException
from src.features.downloads.manager import DownloadManager
from src.features.downloads.models import Download, DownloadStatus, DownloadType


class FakeRepo:
    def __init__(self):
        self.rows: Dict[str, Download] = {}
        self._next = 0

    def create(self, download: Download) -> Download:
        self._next += 1
        download.id = download.id or f"dl-{self._next}"
        self.rows[download.id] = download
        return download

    def get_by_id(self, download_id: str) -> Optional[Download]:
        return self.rows.get(download_id)

    def get_children(self, group_id: str) -> List[Download]:
        return [d for d in self.rows.values() if d.group_id == group_id]


class CompletingRepo(FakeRepo):
    """Every row is terminal the moment it is created, so the synchronous
    wrappers' completion poll returns on its first look and the test can then
    read the rows the REAL `queue_hf_repo_download` wrote.

    The wrapper tests further down stub `queue_hf_repo_download` out entirely,
    which is what left the destination hand-off between it and its callers
    untested.
    """

    def create(self, download: Download) -> Download:
        download.status = DownloadStatus.COMPLETED
        return super().create(download)


def _plugin_registry():
    registry = Mock()
    context = Mock()
    context.data = {}
    registry.execute_hook.return_value = (context, [])
    return registry


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def depot(tmp_path):
    return tmp_path / "depot"


def _build_manager(repo, models_dir):
    settings_manager = Mock()
    settings_manager.get_setting.side_effect = lambda key, default=None: {
        "models_dir": str(models_dir),
    }.get(key, default)
    mgr = DownloadManager(
        download_repository=repo,
        plugin_registry=_plugin_registry(),
        settings_manager=settings_manager,
        connection_manager=AsyncMock(),
    )
    worker = AsyncMock()
    worker.get_queue_position.return_value = 0
    mgr.worker = worker
    return mgr


@pytest.fixture
def manager(repo, depot):
    mgr = _build_manager(repo, depot)
    yield mgr
    mgr._persistent_loop.shutdown()


@pytest.fixture
def cwd_depot(tmp_path, monkeypatch):
    """A depot configured as a RELATIVE directory - which is the shipped
    default (`models`) and what a stock install runs with.

    Worth its own fixture because the absolute `depot` above cannot express
    the double-prefix regression at all: joining an already-depot-rooted path
    onto an absolute root is a no-op (`Path('/depot') / '/depot/x'` is
    `/depot/x` under pathlib), so the doubling only becomes reachable once the
    root is relative. Every test here predating that regression used the
    absolute depot, which is why all of them stayed green through it.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "depot").mkdir()
    return "depot"


@pytest.fixture
def cwd_manager(repo, cwd_depot):
    mgr = _build_manager(repo, cwd_depot)
    yield mgr
    mgr._persistent_loop.shutdown()


_FILES = [
    ("model.safetensors", 100, "https://huggingface.co/org/tiny/resolve/main/model.safetensors"),
    ("config.json", 20, "https://huggingface.co/org/tiny/resolve/main/config.json"),
]


class TestQueueHfRepoDownload:
    async def test_creates_parent_and_children(self, manager, repo, depot):
        destination_dir = str(depot / "org--tiny")
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            parent = await manager.queue_hf_repo_download(
                "org/tiny", destination_dir=destination_dir
            )

        assert parent.type == DownloadType.HF_REPO
        assert parent.repo_id == "org/tiny"
        assert parent.total_bytes == 120
        assert Path(parent.destination_path).resolve() == Path(destination_dir).resolve()

        children = repo.get_children(parent.id)
        assert {c.filename for c in children} == {"model.safetensors", "config.json"}
        assert all(c.type == DownloadType.MODEL for c in children)
        assert all(c.group_id == parent.id for c in children)
        assert children[0].destination_path.startswith(destination_dir + os.sep)

        # each child, and only the children, got enqueued to the worker
        enqueued = [call.args[0] for call in manager.worker.enqueue.call_args_list]
        assert sorted(enqueued) == sorted(c.id for c in children)
        assert parent.id not in enqueued

    async def test_default_destination_derives_from_repo_id(self, manager, repo):
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            parent = await manager.queue_hf_repo_download("org/tiny")

        assert parent.destination_path.endswith("org--tiny")

    async def test_empty_repo_raises(self, manager):
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=[]):
            with pytest.raises(DownloadQueueException):
                await manager.queue_hf_repo_download("org/empty")

    async def test_enumeration_failure_raises_queue_exception(self, manager):
        with patch.object(DownloadManager, "_enumerate_hf_repo", side_effect=RuntimeError("offline")):
            with pytest.raises(DownloadQueueException):
                await manager.queue_hf_repo_download("org/tiny")

    async def test_blocking_hook_prevents_queueing(self, repo, manager):
        context = Mock()
        context.data = {"blocked": True, "block_reason": "nope"}
        manager.plugins.execute_hook.return_value = (context, [])
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            with pytest.raises(DownloadQueueException):
                await manager.queue_hf_repo_download("org/tiny")
        assert repo.rows == {}


class TestQueueHfRepoDownloadDestinationContainment:
    """A grouped hf_repo destination - whether taken from the request body
    or handed back by a plugin's `before_queue` hook - must be contained
    inside the configured model depot before anything is enumerated or
    written to history, the same contract `queue_model_download` and
    `queue_media_download` already enforce.
    """

    def _forbidden_enumerate(self):
        # A plain Mock, deliberately NOT side_effect=AssertionError: the
        # production code wraps any enumeration exception into
        # DownloadQueueException, which would satisfy the pytest.raises below
        # and make these tests pass with containment removed. Ordering is
        # asserted via assert_not_called instead.
        return Mock()

    async def test_relative_traversal_is_rejected_before_enumeration(self, manager, repo):
        enumerate_mock = self._forbidden_enumerate()
        with patch.object(DownloadManager, "_enumerate_hf_repo", enumerate_mock):
            with pytest.raises(DownloadQueueException, match="escapes the configured directory"):
                await manager.queue_hf_repo_download(
                    "org/tiny", destination_dir="../../outside"
                )
        enumerate_mock.assert_not_called()
        assert repo.rows == {}

    async def test_absolute_outside_depot_is_rejected_before_enumeration(self, manager, repo):
        enumerate_mock = self._forbidden_enumerate()
        with patch.object(DownloadManager, "_enumerate_hf_repo", enumerate_mock):
            with pytest.raises(DownloadQueueException, match="escapes the configured directory"):
                await manager.queue_hf_repo_download(
                    "org/tiny", destination_dir="/tmp/outside"
                )
        enumerate_mock.assert_not_called()
        assert repo.rows == {}

    async def test_hook_supplied_escaping_destination_is_rejected(self, manager, repo):
        """The case the grouped path most clearly missed: a plugin's
        `before_queue` hook is untrusted input exactly like the request
        body, so a hook-returned escape must be rejected too."""
        context = Mock()
        context.data = {"destination_dir": "../../outside"}
        manager.plugins.execute_hook.return_value = (context, [])
        enumerate_mock = self._forbidden_enumerate()
        with patch.object(DownloadManager, "_enumerate_hf_repo", enumerate_mock):
            with pytest.raises(DownloadQueueException, match="escapes the configured directory"):
                await manager.queue_hf_repo_download("org/tiny")
        enumerate_mock.assert_not_called()
        assert repo.rows == {}

    async def test_valid_nested_destination_still_works(self, manager, repo, depot):
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            parent = await manager.queue_hf_repo_download(
                "org/tiny", destination_dir="nested/sub"
            )

        assert Path(parent.destination_path).resolve() == (depot / "nested" / "sub").resolve()
        assert repo.rows


class TestDepotRootedDestinationIsRootedOnce:
    """A destination this process already resolved against the depot must be
    used as given, not joined onto the depot root a second time.

    The regression this pins down: `ensure_asset_repo` resolved its `subdir`
    into `<depot>/audio/x` and handed that to `ensure_local_hf_repo`, which
    forwarded it as an untrusted `destination_dir`; the containment join then
    turned it into `<depot>/<depot>/audio/x`. The doubled path is still inside
    the depot, so containment passed and nothing raised - the bytes simply
    landed a directory deeper than the path handed back to the caller, which
    then hit FileNotFoundError on a file the history reported as completed.

    Every assertion here is on the queued CHILD ROW's `destination_path` - the
    directory the worker would really fetch into. Asserting on the returned
    path alone cannot see this bug: the return value was correct all along.
    """

    @pytest.fixture
    def repo(self):
        return CompletingRepo()

    def _child_dirs(self, repo):
        return {
            Path(row.destination_path).parent
            for row in repo.rows.values()
            if row.group_id
        }

    def test_ensure_asset_repo_fetches_into_the_directory_it_returns(
        self, cwd_manager, repo
    ):
        """The reported failure, at its own seam: a pipe calling
        `ASSETS.ensure_asset_repo` and then opening a file under the path it
        got back."""
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            returned = cwd_manager.ensure_asset_repo(
                "org/tiny",
                subdir="audio/org-tiny",
                files=["config.json"],
                poll_interval=0.01,
            )

        assert Path(returned) == Path("depot/audio/org-tiny")
        assert self._child_dirs(repo) == {Path("depot/audio/org-tiny")}

    def test_ensure_local_hf_repo_uses_a_relative_depot_path_as_given(
        self, cwd_manager, repo
    ):
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            result = cwd_manager.ensure_local_hf_repo(
                "org/tiny", "depot/clip/org-tiny", poll_interval=0.01
            )

        assert Path(result) == Path("depot/clip/org-tiny")
        assert self._child_dirs(repo) == {Path("depot/clip/org-tiny")}

    def test_ensure_local_hf_repo_uses_an_absolute_depot_path_as_given(
        self, cwd_manager, repo, tmp_path
    ):
        """The documented contract: `target_dir` is where the bytes land, and
        it is returned unchanged so the caller can hand it to
        `from_pretrained`."""
        target = tmp_path / "depot" / "clip" / "org-tiny"
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            result = cwd_manager.ensure_local_hf_repo(
                "org/tiny", str(target), poll_interval=0.01
            )

        assert Path(result) == target
        assert self._child_dirs(repo) == {target}

    async def test_default_destination_is_rooted_once(self, cwd_manager, repo):
        """The default was built by joining the depot root and then went
        through the join again - the same doubling, on the path an HTTP request
        that names no destination takes."""
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            parent = await cwd_manager.queue_hf_repo_download("org/tiny")

        assert Path(parent.destination_path) == Path("depot/org--tiny")
        assert self._child_dirs(repo) == {Path("depot/org--tiny")}

    async def test_request_supplied_subdir_is_still_joined_onto_the_root(
        self, cwd_manager, repo
    ):
        """The untrusted path is unchanged: a subdir from the request body is
        depot-relative, so it must still be joined."""
        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            parent = await cwd_manager.queue_hf_repo_download(
                "org/tiny", destination_dir="nested/sub"
            )

        assert Path(parent.destination_path) == Path("depot/nested/sub")


class TestTrustedDestinationIsStillContained:
    """Using a resolved destination as given must not become a way to leave
    the depot: a trusted destination is verified, just not re-rooted. This is
    what keeps a wrong internal computation (e.g. a model name that slugifies
    into `../`) a refusal rather than an escape.
    """

    def _forbidden_enumerate(self):
        return Mock()

    async def test_trusted_absolute_outside_depot_is_rejected_before_enumeration(
        self, cwd_manager, repo
    ):
        enumerate_mock = self._forbidden_enumerate()
        with patch.object(DownloadManager, "_enumerate_hf_repo", enumerate_mock):
            with pytest.raises(DownloadQueueException, match="escapes the configured directory"):
                await cwd_manager.queue_hf_repo_download(
                    "org/tiny", trusted_destination_dir="/tmp/outside"
                )
        enumerate_mock.assert_not_called()
        assert repo.rows == {}

    async def test_trusted_relative_traversal_is_rejected_before_enumeration(
        self, cwd_manager, repo
    ):
        enumerate_mock = self._forbidden_enumerate()
        with patch.object(DownloadManager, "_enumerate_hf_repo", enumerate_mock):
            with pytest.raises(DownloadQueueException, match="escapes the configured directory"):
                await cwd_manager.queue_hf_repo_download(
                    "org/tiny", trusted_destination_dir="depot/../../outside"
                )
        enumerate_mock.assert_not_called()
        assert repo.rows == {}

    async def test_trusted_symlink_hop_out_of_depot_is_rejected(
        self, cwd_manager, repo, tmp_path
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / "depot" / "hop").symlink_to(outside, target_is_directory=True)

        enumerate_mock = self._forbidden_enumerate()
        with patch.object(DownloadManager, "_enumerate_hf_repo", enumerate_mock):
            with pytest.raises(DownloadQueueException, match="escapes the configured directory"):
                await cwd_manager.queue_hf_repo_download(
                    "org/tiny", trusted_destination_dir="depot/hop/weights"
                )
        enumerate_mock.assert_not_called()
        assert repo.rows == {}

    def test_ensure_local_hf_repo_target_outside_depot_is_rejected(self, cwd_manager, repo):
        """End to end through the sync wrapper, which is what actually marks a
        destination trusted."""
        enumerate_mock = self._forbidden_enumerate()
        with patch.object(DownloadManager, "_enumerate_hf_repo", enumerate_mock):
            with pytest.raises(DownloadQueueException, match="escapes the configured directory"):
                cwd_manager.ensure_local_hf_repo("org/tiny", "/tmp/outside", poll_interval=0.01)
        enumerate_mock.assert_not_called()
        assert repo.rows == {}

    async def test_untrusted_traversal_still_rejected_under_a_relative_depot(
        self, cwd_manager, repo
    ):
        for escape in ("../../outside", "/tmp/outside"):
            enumerate_mock = self._forbidden_enumerate()
            with patch.object(DownloadManager, "_enumerate_hf_repo", enumerate_mock):
                with pytest.raises(
                    DownloadQueueException, match="escapes the configured directory"
                ):
                    await cwd_manager.queue_hf_repo_download(
                        "org/tiny", destination_dir=escape
                    )
            enumerate_mock.assert_not_called()
            assert repo.rows == {}

    async def test_hook_rewriting_a_trusted_destination_makes_it_untrusted(
        self, cwd_manager, repo
    ):
        """A plugin cannot inherit the caller's trust by rewriting the
        destination: its value is depot-relative like any other hook output,
        so it gets joined onto the root."""
        context = Mock()
        context.data = {"destination_dir": "hooked/sub"}
        cwd_manager.plugins.execute_hook.return_value = (context, [])

        with patch.object(DownloadManager, "_enumerate_hf_repo", return_value=list(_FILES)):
            parent = await cwd_manager.queue_hf_repo_download(
                "org/tiny", trusted_destination_dir="depot/clip/org-tiny"
            )

        assert Path(parent.destination_path) == Path("depot/hooked/sub")

    async def test_hook_escaping_from_a_trusted_call_is_rejected(self, cwd_manager, repo):
        context = Mock()
        context.data = {"destination_dir": "../../outside"}
        cwd_manager.plugins.execute_hook.return_value = (context, [])

        enumerate_mock = self._forbidden_enumerate()
        with patch.object(DownloadManager, "_enumerate_hf_repo", enumerate_mock):
            with pytest.raises(DownloadQueueException, match="escapes the configured directory"):
                await cwd_manager.queue_hf_repo_download(
                    "org/tiny", trusted_destination_dir="depot/clip/org-tiny"
                )
        enumerate_mock.assert_not_called()
        assert repo.rows == {}


class TestEnumerateHfRepo:
    def _fake_requests(self, siblings):
        requests_mod = types.ModuleType("requests")
        response = Mock()
        response.json.return_value = {"siblings": siblings}
        response.raise_for_status.return_value = None
        requests_mod.get = Mock(return_value=response)
        return requests_mod

    def test_enumerates_files_with_sizes_and_urls(self, manager):
        siblings = [
            {"rfilename": "model.safetensors", "size": 100},
            {"rfilename": "config.json", "size": 20},
        ]
        requests_mod = self._fake_requests(siblings)
        with patch.dict(sys.modules, {"requests": requests_mod}):
            files = manager._enumerate_hf_repo("org/tiny", None, None)

        assert files == [
            ("model.safetensors", 100, "https://huggingface.co/org/tiny/resolve/main/model.safetensors"),
            ("config.json", 20, "https://huggingface.co/org/tiny/resolve/main/config.json"),
        ]
        call = requests_mod.get.call_args
        assert call.args[0] == "https://huggingface.co/api/models/org/tiny"
        assert call.kwargs["params"] == {"blobs": "true"}
        assert call.kwargs["headers"] == {}

    def test_revision_pins_api_and_resolve_urls(self, manager):
        requests_mod = self._fake_requests([{"rfilename": "a.bin", "size": 1}])
        with patch.dict(sys.modules, {"requests": requests_mod}):
            files = manager._enumerate_hf_repo("org/tiny", "v1.0", None)

        assert requests_mod.get.call_args.args[0] == (
            "https://huggingface.co/api/models/org/tiny/revision/v1.0"
        )
        assert files[0][2] == "https://huggingface.co/org/tiny/resolve/v1.0/a.bin"

    def test_allow_patterns_filter(self, manager):
        siblings = [
            {"rfilename": "model.safetensors", "size": 100},
            {"rfilename": "README.md", "size": 5},
        ]
        requests_mod = self._fake_requests(siblings)
        with patch.dict(sys.modules, {"requests": requests_mod}):
            files = manager._enumerate_hf_repo("org/tiny", None, ["*.safetensors"])

        assert [f[0] for f in files] == ["model.safetensors"]

    def test_provider_token_applies_through_the_seam(self, manager):
        provider = Mock()
        provider.get_download_headers.return_value = {"Authorization": "Bearer sekrit"}
        registry = Mock()
        registry.find_provider_for_url.return_value = provider

        requests_mod = self._fake_requests([{"rfilename": "a.bin", "size": 1}])
        with patch.dict(sys.modules, {"requests": requests_mod}):
            with patch(
                "src.features.providers.registry.get_provider_registry",
                return_value=registry,
            ):
                manager._enumerate_hf_repo("org/gated", None, None)

        assert requests_mod.get.call_args.kwargs["headers"] == {"Authorization": "Bearer sekrit"}

    def test_no_provider_means_no_token(self, manager):
        requests_mod = self._fake_requests([{"rfilename": "a.bin", "size": 1}])
        registry = Mock()
        registry.find_provider_for_url.return_value = None
        with patch.dict(sys.modules, {"requests": requests_mod}):
            with patch(
                "src.features.providers.registry.get_provider_registry",
                return_value=registry,
            ):
                manager._enumerate_hf_repo("org/tiny", None, None)

        assert requests_mod.get.call_args.kwargs["headers"] == {}


class TestEnsureLocalHfRepo:
    """The synchronous wait wrapper the lazy first-use loaders call."""

    def _statuses(self, repo, parent_id, sequence):
        it = iter(sequence)
        real_get = repo.get_by_id

        def get_by_id(download_id):
            row = real_get(download_id)
            if row is not None and download_id == parent_id:
                try:
                    row.status = next(it)
                except StopIteration:
                    pass
            return row

        return get_by_id

    def test_waits_until_completed(self, manager, repo):
        parent = repo.create(Download(type=DownloadType.HF_REPO, filename="org/tiny"))

        async def fake_queue(*args, **kwargs):
            return parent

        with patch.object(manager, "queue_hf_repo_download", side_effect=fake_queue):
            repo.get_by_id = self._statuses(
                repo, parent.id,
                [DownloadStatus.PENDING, DownloadStatus.DOWNLOADING, DownloadStatus.COMPLETED],
            )
            result = manager.ensure_local_hf_repo("org/tiny", "/tmp/target", poll_interval=0.01)

        assert str(result) == "/tmp/target"

    def test_failed_download_raises(self, manager, repo):
        parent = repo.create(Download(
            type=DownloadType.HF_REPO, filename="org/tiny", error_message="dns down",
        ))

        async def fake_queue(*args, **kwargs):
            return parent

        with patch.object(manager, "queue_hf_repo_download", side_effect=fake_queue):
            repo.get_by_id = self._statuses(repo, parent.id, [DownloadStatus.FAILED])
            with pytest.raises(DownloadOperationException, match="dns down"):
                manager.ensure_local_hf_repo("org/tiny", "/tmp/target", poll_interval=0.01)

    def test_timeout_raises(self, manager, repo):
        parent = repo.create(Download(
            type=DownloadType.HF_REPO, filename="org/tiny", status=DownloadStatus.DOWNLOADING,
        ))

        async def fake_queue(*args, **kwargs):
            return parent

        with patch.object(manager, "queue_hf_repo_download", side_effect=fake_queue):
            with pytest.raises(DownloadOperationException, match="Timed out"):
                manager.ensure_local_hf_repo(
                    "org/tiny", "/tmp/target", poll_interval=0.01, timeout=0.05
                )

    def test_queue_failure_propagates(self, manager):
        async def fake_queue(*args, **kwargs):
            raise DownloadQueueException("no such repo")

        with patch.object(manager, "queue_hf_repo_download", side_effect=fake_queue):
            with pytest.raises(DownloadQueueException):
                manager.ensure_local_hf_repo("org/missing", "/tmp/target")
