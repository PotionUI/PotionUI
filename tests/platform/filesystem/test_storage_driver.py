"""Contract tests run against both `LocalFileStorageDriver` and
`S3FileStorageDriver` (the latter over an in-process fake S3 built on
`httpx.MockTransport` - never a real network call) so the two drivers are
provably interchangeable from a caller's point of view."""

import errno
import os
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.platform.filesystem import storage_driver
from src.platform.filesystem.storage_driver import LocalFileStorageDriver, StorageKeyError, validate_key
from src.platform.filesystem.s3_driver import S3FileStorageDriver


class FakeS3Backend:
    """An in-memory object store that answers like the S3 REST API for the
    subset of operations the driver uses - just enough to prove the driver's
    request construction and response handling round-trip correctly."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        method = request.method

        if "Authorization" not in request.headers:
            return httpx.Response(403, content=b"missing Authorization header")

        if method == "PUT":
            self.objects[path] = request.read()
            return httpx.Response(200)
        if method == "GET":
            if path not in self.objects:
                return httpx.Response(404)
            return httpx.Response(200, content=self.objects[path])
        if method == "HEAD":
            if path not in self.objects:
                return httpx.Response(404)
            return httpx.Response(
                200, headers={"content-length": str(len(self.objects[path]))}
            )
        if method == "DELETE":
            self.objects.pop(path, None)
            return httpx.Response(204)
        return httpx.Response(400)


def make_s3_driver(**overrides) -> S3FileStorageDriver:
    backend = FakeS3Backend()
    client = httpx.Client(transport=httpx.MockTransport(backend.handler))
    kwargs = dict(
        bucket="test-bucket",
        access_key_id="AKIDEXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        path_style=True,
        client=client,
    )
    kwargs.update(overrides)
    driver = S3FileStorageDriver(**kwargs)
    driver._test_backend = backend  # for assertions on wire-level requests
    return driver


@pytest.fixture(params=["local", "s3"])
def driver(request, tmp_path):
    if request.param == "local":
        return LocalFileStorageDriver(str(tmp_path))
    return make_s3_driver()


class TestDriverContract:
    def test_put_then_get_bytes_round_trips(self, driver):
        written = driver.put_bytes("uploads/hello.txt", b"hello world")
        assert written == len(b"hello world")
        assert driver.get_bytes("uploads/hello.txt") == b"hello world"

    def test_exists_and_size(self, driver):
        assert driver.exists("uploads/missing.txt") is False
        assert driver.size("uploads/missing.txt") is None

        driver.put_bytes("uploads/present.txt", b"1234567890")
        assert driver.exists("uploads/present.txt") is True
        assert driver.size("uploads/present.txt") == 10

    def test_get_bytes_missing_key_raises_file_not_found(self, driver):
        with pytest.raises(FileNotFoundError):
            driver.get_bytes("uploads/does-not-exist.png")

    def test_get_stream_round_trips_and_missing_key_raises(self, driver):
        data = b"x" * 200_000
        driver.put_bytes("generations/2026-01-01/g1/0.png", data)

        chunks = list(driver.get_stream("generations/2026-01-01/g1/0.png", chunk_size=1024))
        assert b"".join(chunks) == data
        assert len(chunks) > 1  # actually chunked, not one giant read

        with pytest.raises(FileNotFoundError):
            list(driver.get_stream("generations/2026-01-01/g1/missing.png"))

    def test_delete_reports_whether_something_was_removed(self, driver):
        assert driver.delete("uploads/never-existed.txt") is False

        driver.put_bytes("uploads/to-delete.txt", b"bye")
        assert driver.delete("uploads/to-delete.txt") is True
        assert driver.exists("uploads/to-delete.txt") is False

    def test_put_file_streams_from_disk(self, driver, tmp_path):
        source = tmp_path / "source.bin"
        source.write_bytes(b"streamed-from-disk" * 100)

        written = driver.put_file("uploads/streamed.bin", source)
        assert written == source.stat().st_size
        assert driver.get_bytes("uploads/streamed.bin") == source.read_bytes()

    @pytest.mark.parametrize(
        "bad_key",
        [
            "/absolute/path.png",
            "../escape.png",
            "uploads/../../escape.png",
            "",
        ],
    )
    def test_rejects_traversal_and_absolute_keys(self, driver, bad_key):
        with pytest.raises((StorageKeyError, ValueError)):
            driver.put_bytes(bad_key, b"data")


class TestLocalPath:
    def test_local_driver_returns_a_real_path(self, tmp_path):
        driver = LocalFileStorageDriver(str(tmp_path))
        driver.put_bytes("uploads/a.png", b"x")
        path = driver.local_path("uploads/a.png")
        assert path is not None
        assert path.read_bytes() == b"x"

    def test_s3_driver_has_no_local_path(self):
        driver = make_s3_driver()
        driver.put_bytes("uploads/a.png", b"x")
        assert driver.local_path("uploads/a.png") is None


class TestValidateKey:
    def test_accepts_ordinary_relative_keys(self):
        assert validate_key("generations/2026-01-01/g1/0.png") == "generations/2026-01-01/g1/0.png"

    @pytest.mark.parametrize(
        "bad_key",
        ["/etc/passwd", "../../etc/passwd", "a/../../b", "a\x00b", ""],
    )
    def test_rejects_unsafe_keys(self, bad_key):
        with pytest.raises(StorageKeyError):
            validate_key(bad_key)


class TestLocalDriverContainment:
    def test_resolved_path_never_escapes_base_dir_even_with_sibling_prefix_trick(self, tmp_path):
        # A sibling directory whose name merely extends the base
        # (".../standard-evil" for base ".../standard") must not pass a
        # naive `str.startswith` containment check.
        base = tmp_path / "storage"
        base.mkdir()
        sibling = tmp_path / "storage-evil"
        sibling.mkdir()
        (sibling / "secret.txt").write_bytes(b"should not be reachable")

        driver = LocalFileStorageDriver(str(base))
        with pytest.raises(StorageKeyError):
            driver._resolve("../storage-evil/secret.txt")


class TestS3DriverAddressing:
    def test_path_style_url_includes_bucket_in_path(self):
        driver = make_s3_driver(path_style=True)
        driver.put_bytes("uploads/a.png", b"x")
        request = driver._test_backend.requests[-1]
        assert request.url.path == "/test-bucket/uploads/a.png"

    def test_virtual_hosted_style_puts_bucket_in_host(self):
        driver = make_s3_driver(path_style=False)
        assert driver.host == "test-bucket.s3.us-east-1.amazonaws.com"
        assert driver._canonical_uri("uploads/a.png") == "/uploads/a.png"

    def test_prefix_is_prepended_to_every_key(self):
        driver = make_s3_driver(prefix="potionui/prod")
        driver.put_bytes("uploads/a.png", b"x")
        request = driver._test_backend.requests[-1]
        assert request.url.path == "/test-bucket/potionui/prod/uploads/a.png"

    def test_every_request_carries_a_valid_authorization_header(self):
        driver = make_s3_driver()
        driver.put_bytes("uploads/a.png", b"x")
        driver.get_bytes("uploads/a.png")
        driver.size("uploads/a.png")
        driver.delete("uploads/a.png")
        for request in driver._test_backend.requests:
            auth = request.headers["Authorization"]
            assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
            assert "Signature=" in auth


class _ReplaceRecorder:
    def __init__(self, refusals):
        self.refusals = list(refusals)
        self.calls = []
        self.raised = []

    def __call__(self, source, destination):
        self.calls.append((Path(source), Path(destination)))
        if self.refusals:
            error = self.refusals.pop(0)
            self.raised.append(error)
            raise error
        for _ in range(20):
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                time.sleep(0.05)
        os.replace(source, destination)


def _refusal(winerror=32):
    return PermissionError(errno.EACCES, "Access is denied or the file is in use", None, winerror)


@pytest.fixture
def replace_seam(monkeypatch):
    sleeps = []

    def install(refusals, windows=True):
        recorder = _ReplaceRecorder(refusals)
        monkeypatch.setattr(storage_driver, "os", SimpleNamespace(**{**vars(os), "replace": recorder}))
        monkeypatch.setattr(storage_driver, "time", SimpleNamespace(sleep=sleeps.append))
        if windows is not None:
            monkeypatch.setattr(storage_driver, "_is_windows", lambda: windows)
        return recorder

    install.sleeps = sleeps
    return install


def _part_files(tmp_path):
    return sorted(p.name for p in tmp_path.rglob("*.part"))


class TestLocalDriverPublishRename:
    @pytest.mark.parametrize("winerror", [32, 5])
    def test_a_rename_refused_once_on_windows_is_tried_again_after_a_short_wait_and_publishes(self, tmp_path, replace_seam, winerror):
        recorder = replace_seam([_refusal(winerror)])
        source = tmp_path / "out.wav"
        source.write_bytes(b"RIFF" * 1000)
        driver = LocalFileStorageDriver(str(tmp_path / "storage"))

        written = driver.put_file("uploads/result.wav", source)

        assert written == 4000
        assert driver.get_bytes("uploads/result.wav") == b"RIFF" * 1000
        assert len(recorder.calls) == 2
        assert recorder.calls[0] == recorder.calls[1]
        assert replace_seam.sleeps == [0.05]
        assert _part_files(tmp_path) == []

    def test_put_bytes_tries_a_refused_rename_again_on_windows_too(self, tmp_path, replace_seam):
        recorder = replace_seam([_refusal(), _refusal()])
        driver = LocalFileStorageDriver(str(tmp_path))

        driver.put_bytes("generations/2026-01-01/g1/0.png", b"png")

        assert driver.get_bytes("generations/2026-01-01/g1/0.png") == b"png"
        assert len(recorder.calls) == 3
        assert replace_seam.sleeps == [0.05, 0.05]
        assert _part_files(tmp_path) == []

    def test_a_rename_refused_on_every_attempt_raises_the_last_refusal_unchanged_and_leaves_no_part_file(self, tmp_path, replace_seam):
        recorder = replace_seam([_refusal() for _ in range(11)])
        source = tmp_path / "out.wav"
        source.write_bytes(b"data")
        driver = LocalFileStorageDriver(str(tmp_path / "storage"))

        with pytest.raises(PermissionError) as raised:
            driver.put_file("uploads/result.wav", source)

        assert raised.value is recorder.raised[-1]
        assert len(recorder.calls) == 10
        assert replace_seam.sleeps == [0.05] * 9
        assert driver.exists("uploads/result.wav") is False
        assert _part_files(tmp_path) == []

    def test_a_rename_that_succeeds_at_once_neither_waits_nor_renames_twice(self, tmp_path, replace_seam):
        recorder = replace_seam([])
        driver = LocalFileStorageDriver(str(tmp_path))

        driver.put_bytes("uploads/a.png", b"x")

        assert len(recorder.calls) == 1
        assert replace_seam.sleeps == []

    def test_off_windows_a_refused_rename_raises_at_once(self, tmp_path, replace_seam):
        refusal = _refusal()
        recorder = replace_seam([refusal], windows=False)
        source = tmp_path / "out.wav"
        source.write_bytes(b"data")
        driver = LocalFileStorageDriver(str(tmp_path / "storage"))

        with pytest.raises(PermissionError) as raised:
            driver.put_file("uploads/result.wav", source)

        assert raised.value is refusal
        assert len(recorder.calls) == 1
        assert replace_seam.sleeps == []
        assert _part_files(tmp_path) == []

    def test_the_platform_the_tests_run_on_decides_whether_a_refused_rename_is_tried_again(self, tmp_path, replace_seam):
        refusal = _refusal()
        recorder = replace_seam([refusal], windows=None)
        driver = LocalFileStorageDriver(str(tmp_path))

        if os.name == "nt":
            driver.put_bytes("uploads/a.png", b"x")
            assert driver.get_bytes("uploads/a.png") == b"x"
            assert len(recorder.calls) == 2
            assert replace_seam.sleeps == [0.05]
        else:
            with pytest.raises(PermissionError) as raised:
                driver.put_bytes("uploads/a.png", b"x")
            assert raised.value is refusal
            assert len(recorder.calls) == 1
            assert replace_seam.sleeps == []
        assert _part_files(tmp_path) == []

    @pytest.mark.parametrize(
        "failure",
        [
            OSError(errno.EXDEV, "Invalid cross-device link"),
            FileNotFoundError(errno.ENOENT, "No such file or directory"),
            IsADirectoryError(errno.EISDIR, "Is a directory"),
        ],
        ids=["cross-device", "not-found", "is-a-directory"],
    )
    def test_on_windows_a_rename_failure_other_than_a_refusal_raises_at_once(self, tmp_path, replace_seam, failure):
        recorder = replace_seam([failure])
        driver = LocalFileStorageDriver(str(tmp_path))

        with pytest.raises(OSError) as raised:
            driver.put_bytes("uploads/a.png", b"x")

        assert raised.value is failure
        assert len(recorder.calls) == 1
        assert replace_seam.sleeps == []
        assert _part_files(tmp_path) == []

    @pytest.mark.skipif(os.name != "nt", reason="a sharing violation is a Windows file-locking behaviour")
    def test_on_windows_a_part_file_another_handle_holds_at_rename_time_is_published_once_that_handle_closes(self, tmp_path, monkeypatch):
        held = []
        sleeps = []
        real_replace = os.replace

        def replace_while_held(source, destination):
            if not held:
                held.append(open(source, "rb"))
            real_replace(source, destination)

        def release(seconds):
            sleeps.append(seconds)
            for handle in held:
                handle.close()
            time.sleep(seconds)

        monkeypatch.setattr(storage_driver, "os", SimpleNamespace(**{**vars(os), "replace": replace_while_held}))
        monkeypatch.setattr(storage_driver, "time", SimpleNamespace(sleep=release))
        source = tmp_path / "out.wav"
        source.write_bytes(b"RIFF" * 1000)
        driver = LocalFileStorageDriver(str(tmp_path / "storage"))

        driver.put_file("uploads/result.wav", source)

        assert driver.get_bytes("uploads/result.wav") == b"RIFF" * 1000
        assert sleeps[0] == 0.05
        assert _part_files(tmp_path) == []
