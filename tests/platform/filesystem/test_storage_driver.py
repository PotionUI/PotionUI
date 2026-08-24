"""Contract tests run against both `LocalFileStorageDriver` and
`S3FileStorageDriver` (the latter over an in-process fake S3 built on
`httpx.MockTransport` - never a real network call) so the two drivers are
provably interchangeable from a caller's point of view."""

from pathlib import Path

import httpx
import pytest

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

    def test_generate_presigned_url_is_well_formed(self):
        driver = make_s3_driver()
        url = driver.generate_presigned_url("uploads/a.png", expires_in=600)
        assert url.startswith("https://")
        assert "X-Amz-Signature=" in url
        assert "X-Amz-Expires=600" in url
        assert "/test-bucket/uploads/a.png" in url
