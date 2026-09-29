import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from src.features.downloads.models import Download, DownloadSettings
from src.features.downloads.worker import DownloadWorker

URL = "https://civitai.com/api/download/models/3361846"


class _Content:
    def __init__(self, chunks, fail_after=None):
        self._chunks = chunks
        self._fail_after = fail_after

    async def iter_chunked(self, size):
        for index, chunk in enumerate(self._chunks):
            if self._fail_after is not None and index >= self._fail_after:
                raise ConnectionError("interrupted")
            yield chunk


class _Response:
    def __init__(self, headers=None, chunks=(b"abcd", b"efgh"), fail_after=None, status=200):
        self.status = status
        self.reason = "OK"
        self.headers = {"Content-Type": "application/octet-stream", **(headers or {})}
        self.content = _Content(list(chunks), fail_after)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _Session:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []

    def get(self, url, headers=None, **kwargs):
        self.requests.append((url, dict(headers or {})))
        return self._responses.pop(0)


class _Provider:
    provider_id = "civitai"

    def __init__(self, name=None):
        self._name = name
        self.name_lookups = []

    async def prepare_download(self, session, url, headers):
        return url

    async def resolve_download_filename(self, session, url):
        self.name_lookups.append(url)
        return self._name


def _worker(tmp_path, provider):
    registry = Mock()
    registry.find_provider_for_url.return_value = provider
    registry.get_provider.return_value = provider
    worker = DownloadWorker(
        settings=DownloadSettings(chunk_size_kb=1),
        repo=Mock(),
        connection_hub=AsyncMock(),
        provider_registry_factory=lambda: registry,
    )
    return worker


def _download(tmp_path, filename="3361846", url=URL):
    return Download(
        id="d1",
        url=url,
        filename=filename,
        destination_path=str(tmp_path / filename),
    )


async def _run(worker, download, *responses):
    worker.session = _Session(*responses)
    await worker._download_file(download)
    return worker.session


@pytest.mark.asyncio
async def test_provider_metadata_name_wins_over_content_disposition(tmp_path):
    provider = _Provider("Real Model v2.safetensors")
    worker = _worker(tmp_path, provider)
    download = _download(tmp_path)

    await _run(worker, download, _Response({"Content-Disposition": 'attachment; filename="other.safetensors"'}))

    assert provider.name_lookups == [URL]
    assert download.filename == "Real Model v2.safetensors"
    assert (tmp_path / "Real Model v2.safetensors").read_bytes() == b"abcdefgh"
    assert not (tmp_path / "3361846").exists()
    worker.repo.update_filename.assert_called_once()


@pytest.mark.asyncio
async def test_content_disposition_extended_utf8_name(tmp_path):
    worker = _worker(tmp_path, _Provider())
    download = _download(tmp_path)
    header = "attachment; filename*=UTF-8''My%20Mod%C3%A8l%20%E6%A8%A1%E5%9E%8B.safetensors"

    await _run(worker, download, _Response({"Content-Disposition": header}))

    assert download.filename == "My Modèl 模型.safetensors"
    assert (tmp_path / "My Modèl 模型.safetensors").exists()


@pytest.mark.asyncio
async def test_content_disposition_plain_name(tmp_path):
    worker = _worker(tmp_path, _Provider())
    download = _download(tmp_path)

    await _run(worker, download, _Response({"Content-Disposition": 'attachment; filename="lora_v1.safetensors"'}))

    assert download.filename == "lora_v1.safetensors"
    assert (tmp_path / "lora_v1.safetensors").exists()


@pytest.mark.asyncio
async def test_url_name_gets_extension_from_content_type(tmp_path):
    worker = _worker(tmp_path, _Provider())
    download = _download(tmp_path)

    await _run(worker, download, _Response({"Content-Type": "application/zip"}))

    assert download.filename == "3361846.zip"
    assert (tmp_path / "3361846.zip").exists()


@pytest.mark.asyncio
async def test_uninformative_content_type_keeps_url_name(tmp_path):
    worker = _worker(tmp_path, _Provider())
    download = _download(tmp_path)

    await _run(worker, download, _Response())

    assert download.filename == "3361846"
    assert (tmp_path / "3361846").exists()


@pytest.mark.asyncio
async def test_windows_unsafe_header_name_is_sanitised(tmp_path):
    worker = _worker(tmp_path, _Provider())
    download = _download(tmp_path)

    await _run(worker, download, _Response({"Content-Disposition": 'attachment; filename="a<b>:c?d*.safetensors. "'}))

    assert download.filename == "a_b__c_d_.safetensors"
    assert (tmp_path / "a_b__c_d_.safetensors").exists()


@pytest.mark.asyncio
async def test_user_typed_name_always_wins(tmp_path):
    provider = _Provider("Provider Name.safetensors")
    worker = _worker(tmp_path, provider)
    download = _download(tmp_path, filename="mine.safetensors")

    await _run(worker, download, _Response({"Content-Disposition": 'attachment; filename="other.safetensors"'}))

    assert provider.name_lookups == []
    assert download.filename == "mine.safetensors"
    assert (tmp_path / "mine.safetensors").exists()
    worker.repo.update_filename.assert_not_called()


@pytest.mark.asyncio
async def test_resume_after_interruption_keeps_the_same_final_name(tmp_path):
    worker = _worker(tmp_path, _Provider())
    download = _download(tmp_path)
    header = {"Content-Disposition": 'attachment; filename="lora_v1.safetensors"'}

    worker.session = _Session(_Response(header, fail_after=1))
    with pytest.raises(ConnectionError):
        await worker._download_file(download)

    assert download.filename == "lora_v1.safetensors"
    assert (tmp_path / "lora_v1.safetensors.part").read_bytes() == b"abcd"
    assert not (tmp_path / "3361846.part").exists()

    session = await _run(
        worker,
        download,
        _Response({**header, "Content-Disposition": 'attachment; filename="different.safetensors"'}, chunks=[b"efgh"], status=206),
    )

    assert session.requests[0][1]["Range"] == "bytes=4-"
    assert download.filename == "lora_v1.safetensors"
    assert (tmp_path / "lora_v1.safetensors").read_bytes() == b"abcdefgh"
    assert not (tmp_path / "different.safetensors").exists()


@pytest.mark.asyncio
async def test_part_file_written_under_url_name_moves_with_the_rename(tmp_path):
    worker = _worker(tmp_path, _Provider())
    download = _download(tmp_path)
    (tmp_path / "3361846.part").write_bytes(b"abcd")

    session = await _run(
        worker,
        download,
        _Response({"Content-Disposition": 'attachment; filename="lora_v1.safetensors"'}, chunks=[b"efgh"], status=206),
    )

    assert session.requests[0][1]["Range"] == "bytes=4-"
    assert (tmp_path / "lora_v1.safetensors").read_bytes() == b"abcdefgh"
    assert not (tmp_path / "3361846.part").exists()
