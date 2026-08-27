"""Tests for the `AssetFetcher` methods on DownloadQueue.

These are the seam pipes fetch weights through. What matters here is that a
pipeline-initiated fetch cannot land outside the configured depot, that an
already-present asset costs nothing, and that failures surface as the
platform-layer error type a pipe is actually able to catch (a pipe cannot
import `src.features.downloads.exceptions`).

No test here downloads anything: the worker is an AsyncMock and the repository
is a Mock whose records report whatever terminal status the test wants.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from src.features.downloads.queue import DownloadQueue
from src.features.downloads.models import Download, DownloadStatus, DownloadType
from src.platform.assets import AssetFetchError, AssetFetcher, asset_subdir

_URL = "https://example.com/weights/head.pth"


@pytest.fixture
def mock_repository():
    repo = Mock()
    repo.get_all.return_value = []
    repo.count_by_status.return_value = {}
    repo.count_total.return_value = 0
    repo.create.side_effect = lambda download: download
    return repo


@pytest.fixture
def mock_plugin_registry():
    registry = Mock()
    context = Mock()
    context.data = {}
    registry.execute_hook.return_value = (context, [])
    return registry


def _settings(models_dir):
    sm = Mock()
    sm.get_setting.side_effect = lambda key, default=None: {
        'models_dir': models_dir,
        'file_storage_directory': None,
    }.get(key, default)
    return sm


@pytest.fixture
def depot(tmp_path):
    """A depot that is NOT the CWD, so a regression to CWD-relative
    resolution shows up as a path mismatch rather than passing by accident."""
    path = tmp_path / "custom-depot"
    path.mkdir()
    return path


@pytest.fixture
def manager(mock_repository, mock_plugin_registry, depot):
    manager = DownloadQueue(
        download_repository=mock_repository,
        plugin_registry=mock_plugin_registry,
        settings=_settings(str(depot)),
        connection_hub=AsyncMock(),
    )
    manager.worker = AsyncMock()
    # Plain Mock: `get_queue_position` is called, not awaited, so an AsyncMock
    # child would hand back an un-awaited coroutine.
    manager.worker.get_queue_position = Mock(return_value=0)
    return manager


def _terminal(status, error_message=None):
    """Make the repository report `status` for whatever job was just queued."""
    record = Download(
        type=DownloadType.MODEL,
        url=_URL,
        destination_path="ignored",
        filename="head.pth",
        status=status,
        error_message=error_message,
    )
    return record


class TestEnsureAssetFileContainment:
    """A pipeline-initiated fetch must not be able to leave the depot.

    Each case asserts the escape is refused *before* anything is queued or
    fetched. Without that, the assertions would also pass on a build with no
    containment at this layer at all - `queue_model_download` re-resolves the
    destination and would refuse it a step later, and an unreachable repo id
    fails enumeration for reasons that have nothing to do with containment.
    """

    @pytest.mark.parametrize(
        "subdir",
        ["../../etc", "/etc/cron.d", "inpaint/../../../../tmp", "..", "a/../.."],
    )
    def test_escaping_subdir_is_refused_before_queueing(
        self, manager, mock_repository, subdir
    ):
        with pytest.raises(AssetFetchError):
            manager.ensure_asset_file(_URL, subdir=subdir)

        mock_repository.create.assert_not_called()
        manager.worker.enqueue.assert_not_called()

    @pytest.mark.parametrize("subdir", ["../../etc", "/etc/cron.d", ".."])
    def test_escaping_repo_subdir_is_refused_before_fetching(self, manager, subdir):
        """The repo path needs its own case: `queue_hf_repo_download` does not
        re-resolve the destination, so containment here is the only check."""
        fetched = []
        manager.ensure_local_hf_repo = lambda *a, **kw: fetched.append(a) or Path("/tmp")

        with pytest.raises(AssetFetchError):
            manager.ensure_asset_repo("org/model", subdir=subdir)

        assert fetched == []

    def test_escaping_repo_subdir_creates_no_directory(self, manager, tmp_path):
        """`ensure_asset_repo` mkdirs its destination; the refusal must come
        first, or a traversal would create a directory outside the depot."""
        outside = tmp_path / "outside-the-depot"
        manager.ensure_local_hf_repo = lambda *a, **kw: Path("/tmp")

        with pytest.raises(AssetFetchError):
            manager.ensure_asset_repo("org/model", subdir=f"../{outside.name}")

        assert not outside.exists()


class TestEnsureAssetFile:
    def test_present_file_is_returned_without_queueing(self, manager, depot):
        target = depot / "inpaint" / "head.pth"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"weights")

        result = manager.ensure_asset_file(_URL, subdir="inpaint")

        assert result == target
        manager.worker.enqueue.assert_not_called()

    def test_missing_file_is_queued_and_awaited(self, manager, mock_repository, depot):
        mock_repository.get_by_id.return_value = _terminal(DownloadStatus.COMPLETED)

        result = manager.ensure_asset_file(_URL, subdir="inpaint", poll_interval=0.01)

        assert result == depot / "inpaint" / "head.pth"
        manager.worker.enqueue.assert_awaited_once()

    def test_queued_destination_is_inside_the_depot(self, manager, mock_repository, depot):
        mock_repository.get_by_id.return_value = _terminal(DownloadStatus.COMPLETED)

        manager.ensure_asset_file(_URL, subdir="inpaint", poll_interval=0.01)

        queued = mock_repository.create.call_args[0][0]
        resolved = Path(queued.destination_path).resolve()
        assert depot.resolve() in resolved.parents
        assert resolved.name == "head.pth"

    def test_explicit_filename_overrides_the_url_basename(self, manager, mock_repository, depot):
        mock_repository.get_by_id.return_value = _terminal(DownloadStatus.COMPLETED)

        result = manager.ensure_asset_file(
            _URL, subdir="inpaint", filename="renamed.pth", poll_interval=0.01
        )

        assert result == depot / "inpaint" / "renamed.pth"

    def test_returned_path_follows_a_hook_that_rewrote_the_destination(
        self, manager, mock_repository, mock_plugin_registry, depot
    ):
        """The caller gets where the bytes landed, not where they were asked
        to land - a `download.before_queue` hook may redirect within the depot."""
        mock_repository.get_by_id.return_value = _terminal(DownloadStatus.COMPLETED)
        context = Mock()
        context.data = {"destination_dir": "elsewhere"}
        mock_plugin_registry.execute_hook.return_value = (context, [])

        result = manager.ensure_asset_file(_URL, subdir="inpaint", poll_interval=0.01)

        assert result == depot / "elsewhere" / "head.pth"

    def test_failed_download_raises_the_platform_error(self, manager, mock_repository):
        mock_repository.get_by_id.return_value = _terminal(
            DownloadStatus.FAILED, error_message="connection reset"
        )

        with pytest.raises(AssetFetchError) as exc:
            manager.ensure_asset_file(_URL, subdir="inpaint", poll_interval=0.01)

        assert "connection reset" in str(exc.value)

    def test_cancelled_download_raises_the_platform_error(self, manager, mock_repository):
        mock_repository.get_by_id.return_value = _terminal(DownloadStatus.CANCELLED)

        with pytest.raises(AssetFetchError):
            manager.ensure_asset_file(_URL, subdir="inpaint", poll_interval=0.01)

    def test_vanished_record_raises_rather_than_looping_forever(self, manager, mock_repository):
        mock_repository.get_by_id.return_value = None

        with pytest.raises(AssetFetchError):
            manager.ensure_asset_file(_URL, subdir="inpaint", poll_interval=0.01)

    def test_undeducible_filename_is_refused(self, manager):
        with pytest.raises(AssetFetchError):
            manager.ensure_asset_file("https://example.com/", subdir="inpaint")


class TestEnsureAssetRepo:
    def test_all_expected_files_present_skips_the_fetch(self, manager, depot):
        target = depot / "tts" / "org-model"
        target.mkdir(parents=True)
        (target / "config.json").write_text("{}")
        (target / "pytorch_model.bin").write_bytes(b"w")

        result = manager.ensure_asset_repo(
            "org/model", subdir="tts/org-model",
            files=["config.json", "pytorch_model.bin"],
        )

        assert result == target
        manager.worker.enqueue.assert_not_called()

    def test_one_missing_expected_file_triggers_the_fetch(self, manager, depot):
        """A partially-populated directory must not read as present - the
        failure mode is a library loading half a checkpoint."""
        target = depot / "tts" / "org-model"
        target.mkdir(parents=True)
        (target / "config.json").write_text("{}")

        calls = []
        manager.ensure_local_hf_repo = lambda *args, **kwargs: calls.append(args) or target

        manager.ensure_asset_repo(
            "org/model", subdir="tts/org-model",
            files=["config.json", "pytorch_model.bin"],
        )

        assert len(calls) == 1

    def test_empty_directory_is_not_present_when_no_files_named(self, manager, depot):
        target = depot / "tts" / "org-model"
        target.mkdir(parents=True)

        calls = []
        manager.ensure_local_hf_repo = lambda *args, **kwargs: calls.append(args) or target

        manager.ensure_asset_repo("org/model", subdir="tts/org-model")

        assert len(calls) == 1

    def test_non_empty_directory_is_present_when_no_files_named(self, manager, depot):
        target = depot / "tts" / "org-model"
        target.mkdir(parents=True)
        (target / "anything.bin").write_bytes(b"w")

        result = manager.ensure_asset_repo("org/model", subdir="tts/org-model")

        assert result == target
        manager.worker.enqueue.assert_not_called()

    def test_named_files_become_the_download_filter(self, manager, depot):
        """Only the named files are fetched - the annotators/TTS repos are far
        larger than any one caller needs."""
        captured = {}

        def _fake(repo_id, target_dir, **kwargs):
            captured.update(kwargs)
            return Path(target_dir)

        manager.ensure_local_hf_repo = _fake

        manager.ensure_asset_repo(
            "org/model", subdir="tts/org-model", files=["config.json"]
        )

        assert captured["allow_patterns"] == ["config.json"]

    def test_destination_is_the_returned_depot_directory(self, manager, depot):
        captured = {}

        def _fake(repo_id, target_dir, **kwargs):
            captured["target_dir"] = target_dir
            return Path(target_dir)

        manager.ensure_local_hf_repo = _fake

        result = manager.ensure_asset_repo("org/model", subdir="tts/org-model")

        assert Path(captured["target_dir"]) == depot / "tts" / "org-model"
        assert result == depot / "tts" / "org-model"


class TestPortConformance:
    def test_download_manager_satisfies_the_asset_fetcher_port(self, manager):
        """The whole point of the port: `src/pipelines/` types against this
        protocol and the concrete manager is injected across the boundary."""
        assert isinstance(manager, AssetFetcher)

    def test_asset_subdir_matches_the_established_slug_convention(self):
        assert asset_subdir("tts", "maya-research/maya1") == "tts/maya-research-maya1"
        assert asset_subdir("annotators", "lllyasviel/Annotators") == (
            "annotators/lllyasviel-annotators"
        )
