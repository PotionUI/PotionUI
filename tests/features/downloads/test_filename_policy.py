"""The one filename policy every download name passes through.

Guards the two holes the containment work left open: a *filename* was never
checked at all, so an explicit `filename` (API body, internal caller, or a
`download.before_queue` hook's rewrite) and a provider's mid-flight rename
could both walk out of the destination directory that `_resolve_contained_dir`
had just been careful to compute; and `extract_filename_from_url` ran
`os.path.basename` before `unquote`, so an encoded traversal segment survived
the basename whole and decoded into `../` afterwards.

Companion to `TestQueueModelDownloadDestinationResolution` in
`test_queue.py`, which covers the *directory* half.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

from src.features.downloads.exceptions import (
    DownloadQueueException,
    UnsafeFilenameException,
)
from src.features.downloads.models import Download, DownloadSettings, DownloadType
from src.features.downloads.queue import DownloadQueue
from src.features.downloads.utils import (
    derived_download_name,
    extract_filename_from_url,
    safe_download_name,
    verify_file_target,
)
from src.features.downloads.worker import DownloadWorker


UNSAFE_NAMES = [
    "/etc/cron.d/evil",
    "..",
    ".",
    "../../etc/passwd",
    "sub/../../escape.bin",
    "",
    "%2e%2e%2fescape.bin",
    "%2e%2e%5cescape.bin",
    "..\\..\\windows\\system32\\evil.dll",
    "\\\\server\\share\\evil.bin",
    "C:\\Windows\\evil.dll",
    "model\x00.safetensors",
    "model\n.safetensors",
    "CON",
    "nul.safetensors",
    "COM1.bin",
    "lpt9",
    "model.safetensors.",
    "model.safetensors ",
]


# Separators that only appear once the name is percent-decoded. Safe to keep
# as a literal name in a directory, refused where the name must be one segment.
ENCODED_SEPARATOR_NAMES = ["a%2fb.bin", "a%5cb.bin"]


class TestSafeDownloadName:
    """The policy itself. Every shape here has to be refused whether it
    arrives as a model filename, a media filename, a hook's rewrite or a
    provider's rename - they all call this one function."""

    @pytest.mark.parametrize("name", UNSAFE_NAMES)
    def test_unsafe_names_are_refused(self, name):
        with pytest.raises(UnsafeFilenameException):
            safe_download_name(name)

    @pytest.mark.parametrize("name", UNSAFE_NAMES)
    def test_unsafe_names_are_refused_even_when_a_subpath_is_allowed(self, name):
        with pytest.raises(UnsafeFilenameException):
            safe_download_name(name, single_segment=False)

    @pytest.mark.parametrize("name", ENCODED_SEPARATOR_NAMES)
    def test_an_encoded_separator_cannot_pass_as_one_segment(self, name):
        """`os.path.basename` never split these, so a single-segment caller
        would have taken them whole - the decode-and-recheck is what catches
        them."""
        with pytest.raises(UnsafeFilenameException):
            safe_download_name(name)

        assert safe_download_name(name, single_segment=False) == name

    @pytest.mark.parametrize(
        "name",
        [
            "model.safetensors",
            "my model.safetensors",
            "my%20model.safetensors",
            "sd_xl.base-1.0.safetensors",
            "console.json",
            "..hidden.bin",
        ],
    )
    def test_ordinary_names_pass_through_unchanged(self, name):
        assert safe_download_name(name) == name

    def test_a_subpath_needs_single_segment_off(self):
        with pytest.raises(UnsafeFilenameException):
            safe_download_name("text_encoder/model.safetensors")

        assert (
            safe_download_name("text_encoder/model.safetensors", single_segment=False)
            == "text_encoder/model.safetensors"
        )

    def test_an_unsafe_derived_name_is_dropped_rather_than_raised(self):
        assert derived_download_name("../../etc/passwd") is None
        assert derived_download_name(None) is None
        assert derived_download_name("model.safetensors") == "model.safetensors"

    def test_the_refusal_names_the_filename(self):
        with pytest.raises(UnsafeFilenameException, match="evil.bin"):
            safe_download_name("../evil.bin")


class TestExtractFilenameFromUrl:
    """`unquote` before `os.path.basename`, not after."""

    def test_encoded_traversal_in_the_path_collapses_to_its_last_segment(self):
        assert (
            extract_filename_from_url("https://cdn.example.com/foo%2F..%2F..%2Fetc%2Fcron.sh")
            == "cron.sh"
        )

    def test_encoded_windows_traversal_in_the_path_is_dropped(self):
        """`os.path.basename` does not split on a backslash on POSIX, so the
        decoded name arrives whole and is refused rather than trimmed."""
        assert extract_filename_from_url("https://cdn.example.com/foo%5C..%5Cevil.dll") is None

    def test_a_traversing_content_disposition_is_dropped(self):
        url = (
            "https://cdn.example.com/file"
            "?response-content-disposition=attachment%3B%20filename%3D%22..%2F..%2Fetc%2Fpasswd%22"
        )
        assert extract_filename_from_url(url) is None

    def test_a_control_character_in_the_content_disposition_is_dropped(self):
        url = (
            "https://cdn.example.com/file"
            "?response-content-disposition=attachment%3B%20filename%3D%22ev%0Ail.bin%22"
        )
        assert extract_filename_from_url(url) is None

    def test_an_ordinary_name_still_comes_through(self):
        assert (
            extract_filename_from_url("https://cdn.example.com/models/my%20model.safetensors")
            == "my model.safetensors"
        )


def _settings(models_dir=None, file_storage=None):
    sm = Mock()
    sm.get_setting.side_effect = lambda key, default=None: {
        'models_dir': models_dir,
        'file_storage_directory': file_storage,
    }.get(key, default)
    return sm


@pytest.fixture
def hook_data():
    """The `before_queue` context a plugin can mutate. Tests seed keys into it
    to stand in for a plugin's rewrite."""
    return {}


@pytest.fixture
def plugin_registry(hook_data):
    registry = Mock()
    context = Mock()
    context.data = hook_data
    registry.execute_hook.return_value = (context, [])
    return registry


@pytest.fixture
def depot(tmp_path):
    return tmp_path / "depot"


@pytest.fixture
def manager(plugin_registry, depot, tmp_path):
    repo = Mock()
    repo.create.side_effect = lambda d: d
    manager = DownloadQueue(
        download_repository=repo,
        plugin_registry=plugin_registry,
        settings=_settings(models_dir=str(depot), file_storage=str(tmp_path / "media")),
        connection_hub=AsyncMock(),
    )
    manager.worker = AsyncMock()
    manager.worker.get_queue_position.return_value = 0
    return manager


class TestExplicitFilenameThroughTheQueue:
    """An explicit filename is untrusted wherever it came from, and an unsafe
    one fails loudly (a `DownloadQueueException`, so the queue routes answer
    400) rather than being silently renamed."""

    async def _model(self, manager, **kwargs):
        with patch.object(manager, 'conn', AsyncMock()):
            return await manager.queue_model_download(
                url='https://example.com/model.safetensors', **kwargs
            )

    async def _media(self, manager, **kwargs):
        with patch.object(manager, 'conn', AsyncMock()):
            return await manager.queue_media_download(
                url='https://example.com/clip.mp4', **kwargs
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["../../etc/cron.d/evil", "/etc/cron.d/evil", ".."])
    async def test_model_download_refuses_an_escaping_filename(self, manager, name):
        with pytest.raises(UnsafeFilenameException):
            await self._model(manager, filename=name)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["../../etc/cron.d/evil", "/etc/cron.d/evil", ".."])
    async def test_media_download_refuses_an_escaping_filename(self, manager, name):
        with pytest.raises(UnsafeFilenameException):
            await self._media(manager, filename=name)

    @pytest.mark.asyncio
    async def test_the_refusal_is_a_download_queue_exception(self, manager):
        """The routes map `DownloadQueueException` to 400 - an unsafe filename
        must reach the client as a refusal, not a 500."""
        with pytest.raises(DownloadQueueException):
            await self._model(manager, filename="../../etc/cron.d/evil")

    @pytest.mark.asyncio
    async def test_nothing_is_written_to_history_when_a_filename_is_refused(self, manager):
        with pytest.raises(UnsafeFilenameException):
            await self._model(manager, filename="../../etc/cron.d/evil")

        manager.repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_hook_rewritten_filename_is_refused_too(self, manager, hook_data):
        """A `before_queue` hook can rewrite the filename, so a plugin's value
        is no more trusted than the request body's."""
        hook_data["filename"] = "../../etc/cron.d/evil"

        with pytest.raises(UnsafeFilenameException):
            await self._model(manager, filename="model.safetensors")

    @pytest.mark.asyncio
    async def test_an_ordinary_filename_still_lands_in_the_depot(self, manager, depot):
        result = await self._model(manager, filename="model.safetensors", model_type="checkpoint")

        assert Path(result.destination_path) == depot / "checkpoints" / "model.safetensors"

    @pytest.mark.asyncio
    async def test_an_intentional_relative_filename_keeps_its_subdirectory(self, manager, depot):
        """The preset test-suite resolver passes a Hugging Face repo-relative
        path as `filename` (`src/features/preset_suite/resolver.py`); it may
        descend, it may not escape."""
        result = await self._model(manager, filename="split_files/vae/ae.safetensors")

        assert Path(result.destination_path) == depot / "split_files" / "vae" / "ae.safetensors"


class TestGroupedRepoChildPaths:
    """A grouped Hugging Face job's per-file paths are intentional subpaths,
    contained against the resolved destination rather than flattened."""

    def _files(self, *names):
        return [(name, 10, f"https://huggingface.co/org/tiny/resolve/main/{name}") for name in names]

    @pytest.mark.asyncio
    async def test_a_nested_repo_file_keeps_its_directory(self, manager, depot):
        with patch.object(manager, 'conn', AsyncMock()), \
                patch.object(manager, '_enumerate_hf_repo',
                             return_value=self._files("text_encoder/model.safetensors")):
            await manager.queue_hf_repo_download("org/tiny")

        created = [call.args[0] for call in manager.repo.create.call_args_list]
        child = next(d for d in created if d.type == DownloadType.MODEL)
        assert Path(child.destination_path) == (
            depot / "org--tiny" / "text_encoder" / "model.safetensors"
        )

    @pytest.mark.asyncio
    async def test_a_traversing_repo_file_is_refused(self, manager):
        with patch.object(manager, 'conn', AsyncMock()), \
                patch.object(manager, '_enumerate_hf_repo',
                             return_value=self._files("../../etc/cron.d/evil")):
            with pytest.raises(UnsafeFilenameException):
                await manager.queue_hf_repo_download("org/tiny")


@pytest.fixture
def worker():
    return DownloadWorker(
        settings=DownloadSettings(max_concurrent_downloads=1, chunk_size_kb=1),
        repo=Mock(),
        connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None,
    )


class TestProviderRewrittenFilename:
    """A provider that resolves a download to a CDN URL may surface a new
    filename. It renames the file inside the directory the queue resolved - it
    cannot move the download."""

    @pytest.mark.asyncio
    async def test_a_traversing_rewrite_is_refused(self, worker, tmp_path):
        download = Download(
            filename="model.safetensors",
            url="https://example.com/model.safetensors",
            destination_path=str(tmp_path / "checkpoints" / "model.safetensors"),
        )
        (tmp_path / "checkpoints").mkdir()

        with pytest.raises(UnsafeFilenameException):
            await worker._update_download_filename(download, "../../etc/cron.d/evil")

        assert download.destination_path == str(tmp_path / "checkpoints" / "model.safetensors")
        worker.repo.update_filename.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_encoded_traversing_rewrite_is_refused(self, worker, tmp_path):
        download = Download(
            filename="model.safetensors",
            url="https://example.com/model.safetensors",
            destination_path=str(tmp_path / "checkpoints" / "model.safetensors"),
        )
        (tmp_path / "checkpoints").mkdir()

        with pytest.raises(UnsafeFilenameException):
            await worker._update_download_filename(download, "%2e%2e%2fescape.safetensors")

    @pytest.mark.asyncio
    async def test_an_ordinary_rewrite_renames_inside_the_same_directory(self, worker, tmp_path):
        download = Download(
            filename="12345",
            url="https://example.com/api/download/12345",
            destination_path=str(tmp_path / "checkpoints" / "12345"),
        )
        (tmp_path / "checkpoints").mkdir()

        await worker._update_download_filename(download, "real_name.safetensors")

        assert download.filename == "real_name.safetensors"
        assert download.destination_path == str(tmp_path / "checkpoints" / "real_name.safetensors")


class _FakeResponse:
    def __init__(self, status, chunks=(), headers=None):
        self.status = status
        self.reason = "OK"
        self.headers = headers or {}
        self.content = self
        self._chunks = list(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            yield chunk


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.headers_seen = None

    def get(self, url, headers=None):
        self.headers_seen = headers
        return self._response


class TestWorkerWriteTargets:
    """The write/resume/rename boundary. A destination directory that is an
    admin-configured symlink into shared storage is the supported layout; a
    file or `.part` that is itself a symlink out of that directory is not."""

    @pytest.mark.asyncio
    async def test_a_plain_download_writes_and_renames(self, worker, tmp_path):
        dest = tmp_path / "depot" / "model.safetensors"
        dest.parent.mkdir(parents=True)
        download = Download(
            filename="model.safetensors",
            url="https://example.com/model.safetensors",
            destination_path=str(dest),
        )
        worker.session = _FakeSession(_FakeResponse(200, [b"abc"], {"Content-Length": "3"}))

        assert await worker._download_file(download) is True
        assert dest.read_bytes() == b"abc"

    @pytest.mark.asyncio
    async def test_a_resume_appends_to_an_existing_part_file(self, worker, tmp_path):
        dest = tmp_path / "depot" / "model.safetensors"
        dest.parent.mkdir(parents=True)
        (tmp_path / "depot" / "model.safetensors.part").write_bytes(b"ab")
        download = Download(
            filename="model.safetensors",
            url="https://example.com/model.safetensors",
            destination_path=str(dest),
        )
        worker.session = _FakeSession(
            _FakeResponse(206, [b"c"], {"Content-Length": "1", "Content-Range": "bytes 2-2/3"})
        )

        assert await worker._download_file(download) is True
        assert worker.session.headers_seen["Range"] == "bytes=2-"
        assert dest.read_bytes() == b"abc"

    @pytest.mark.asyncio
    async def test_a_symlinked_destination_directory_is_accepted(self, worker, tmp_path):
        """`models/diffusion_models -> /mnt/ssd2/models/diffusion_models` is a
        real install's layout, not an escape."""
        shared = tmp_path / "shared-storage" / "diffusion_models"
        shared.mkdir(parents=True)
        depot = tmp_path / "depot"
        depot.mkdir()
        (depot / "diffusion_models").symlink_to(shared, target_is_directory=True)

        download = Download(
            filename="model.safetensors",
            url="https://example.com/model.safetensors",
            destination_path=str(depot / "diffusion_models" / "model.safetensors"),
        )
        worker.session = _FakeSession(_FakeResponse(200, [b"abc"], {"Content-Length": "3"}))

        assert await worker._download_file(download) is True
        assert (shared / "model.safetensors").read_bytes() == b"abc"

    @pytest.mark.asyncio
    async def test_a_part_file_symlinked_out_of_the_directory_is_refused(self, worker, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        victim = outside / "authorized_keys"
        victim.write_bytes(b"keep me")

        dest = tmp_path / "depot" / "model.safetensors"
        dest.parent.mkdir(parents=True)
        Path(str(dest) + ".part").symlink_to(victim)

        download = Download(
            filename="model.safetensors",
            url="https://example.com/model.safetensors",
            destination_path=str(dest),
        )
        worker.session = _FakeSession(_FakeResponse(200, [b"abc"], {"Content-Length": "3"}))

        with pytest.raises(UnsafeFilenameException):
            await worker._download_file(download)

        assert victim.read_bytes() == b"keep me"

    @pytest.mark.asyncio
    async def test_a_destination_symlinked_out_of_the_directory_is_refused(self, worker, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        victim = outside / "authorized_keys"
        victim.write_bytes(b"keep me")

        dest = tmp_path / "depot" / "model.safetensors"
        dest.parent.mkdir(parents=True)
        dest.symlink_to(victim)

        download = Download(
            filename="model.safetensors",
            url="https://example.com/model.safetensors",
            destination_path=str(dest),
        )
        worker.session = _FakeSession(_FakeResponse(200, [b"abc"], {"Content-Length": "3"}))

        with pytest.raises(UnsafeFilenameException):
            await worker._download_file(download)

        assert victim.read_bytes() == b"keep me"

    @pytest.mark.asyncio
    async def test_the_416_already_complete_branch_refuses_an_escaping_part_file(
        self, worker, tmp_path
    ):
        """HTTP 416 renames the `.part` straight onto the destination without
        writing a byte - the one path that skips the download loop entirely."""
        outside = tmp_path / "outside"
        outside.mkdir()
        victim = outside / "authorized_keys"
        victim.write_bytes(b"keep me")

        dest = tmp_path / "depot" / "model.safetensors"
        dest.parent.mkdir(parents=True)
        part = Path(str(dest) + ".part")
        part.symlink_to(victim)

        download = Download(
            filename="model.safetensors",
            url="https://example.com/model.safetensors",
            destination_path=str(dest),
        )
        worker.session = _FakeSession(_FakeResponse(416))

        with pytest.raises(UnsafeFilenameException):
            await worker._download_file(download)

        assert victim.exists()
        assert not dest.exists()


class TestVerifyFileTarget:
    """The write-time check on its own, including the case the worker's
    directory-level checks cannot see: a target that is not a child of the
    approved directory at all."""

    def test_a_file_outside_the_approved_directory_is_refused(self, tmp_path):
        approved = tmp_path / "depot"
        approved.mkdir()
        (tmp_path / "elsewhere").mkdir()

        with pytest.raises(UnsafeFilenameException):
            verify_file_target(tmp_path / "elsewhere" / "model.safetensors", approved)

    def test_a_child_of_the_approved_directory_is_accepted(self, tmp_path):
        approved = tmp_path / "depot"
        approved.mkdir()

        verify_file_target(approved / "model.safetensors", approved)

    def test_a_symlink_inside_the_approved_directory_is_accepted(self, tmp_path):
        approved = tmp_path / "depot"
        approved.mkdir()
        (approved / "real.safetensors").write_bytes(b"x")
        (approved / "alias.safetensors").symlink_to(approved / "real.safetensors")

        verify_file_target(approved / "alias.safetensors", approved)

    def test_an_unsafe_basename_is_refused(self, tmp_path):
        approved = tmp_path / "depot"
        approved.mkdir()

        with pytest.raises(UnsafeFilenameException):
            verify_file_target(Path(os.path.join(str(approved), "model.safetensors ")), approved)
