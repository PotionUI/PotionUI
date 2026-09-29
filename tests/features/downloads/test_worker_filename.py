import asyncio
import pytest
from urllib.parse import quote
from unittest.mock import AsyncMock, Mock

from src.features.downloads.models import NO_PROVIDER, Download, DownloadSettings
from src.features.downloads.worker import DownloadWorker
from tests.features.downloads.fakes import FakeResponse, FakeSession

URL = "https://civitai.com/api/download/models/3361846"


class _Provider:
    provider_id = "civitai"

    def __init__(self, name=None, prepared_url=None):
        self._name = name
        self._prepared_url = prepared_url
        self.name_lookups = []

    async def prepare_download(self, session, url, headers):
        return self._prepared_url or url

    async def resolve_download_filename(self, session, url):
        self.name_lookups.append(url)
        return self._name


def _worker(provider):
    worker_repo = Mock()
    worker_repo.is_destination_claimed.return_value = False
    registry = Mock()
    registry.find_provider_for_url.return_value = provider
    registry.get_provider.return_value = provider
    worker = DownloadWorker(
        settings=DownloadSettings(chunk_size_kb=1),
        repo=worker_repo,
        connection_hub=AsyncMock(),
        provider_registry_factory=lambda: registry,
    )
    return worker


def _download(tmp_path, filename="3361846", url=URL, supplied=False):
    return Download(
        id="d1",
        url=url,
        filename=filename,
        destination_path=str(tmp_path / filename),
        filename_supplied=supplied,
    )


async def _run(worker, download, *responses):
    worker.session = FakeSession(*responses)
    await worker._download_file(download)
    return worker.session


@pytest.mark.asyncio
async def test_provider_metadata_name_wins_over_content_disposition(tmp_path):
    provider = _Provider("Real Model v2.safetensors")
    worker = _worker(provider)
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": 'attachment; filename="other.safetensors"'}))

    assert provider.name_lookups == [URL]
    assert download.filename == "Real Model v2.safetensors"
    assert (tmp_path / "Real Model v2.safetensors").read_bytes() == b"abcdefgh"
    assert not (tmp_path / "3361846").exists()
    worker.repo.update_filename.assert_called_once()


@pytest.mark.asyncio
async def test_content_disposition_extended_utf8_name(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)
    header = "attachment; filename*=UTF-8''My%20Mod%C3%A8l%20%E6%A8%A1%E5%9E%8B.safetensors"

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": header}))

    assert download.filename == "My Modèl 模型.safetensors"
    assert (tmp_path / "My Modèl 模型.safetensors").exists()


@pytest.mark.asyncio
async def test_content_disposition_plain_name(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": 'attachment; filename="lora_v1.safetensors"'}))

    assert download.filename == "lora_v1.safetensors"
    assert (tmp_path / "lora_v1.safetensors").exists()


@pytest.mark.asyncio
async def test_url_name_gets_extension_from_content_type(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse(headers={"Content-Type": "application/zip"}))

    assert download.filename == "3361846.zip"
    assert (tmp_path / "3361846.zip").exists()


@pytest.mark.asyncio
async def test_uninformative_content_type_keeps_url_name(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse())

    assert download.filename == "3361846"
    assert (tmp_path / "3361846").exists()


@pytest.mark.asyncio
async def test_windows_unsafe_header_name_is_sanitised(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": 'attachment; filename="a<b>:c?d*.safetensors. "'}))

    assert download.filename == "a_b__c_d_.safetensors"
    assert (tmp_path / "a_b__c_d_.safetensors").exists()


@pytest.mark.asyncio
async def test_user_typed_name_always_wins(tmp_path):
    provider = _Provider("Provider Name.safetensors")
    worker = _worker(provider)
    download = _download(tmp_path, filename="mine.safetensors", supplied=True)

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": 'attachment; filename="other.safetensors"'}))

    assert provider.name_lookups == []
    assert download.filename == "mine.safetensors"
    assert (tmp_path / "mine.safetensors").exists()
    worker.repo.update_filename.assert_not_called()


@pytest.mark.asyncio
async def test_resume_after_interruption_keeps_the_same_final_name(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)
    header = {"Content-Disposition": 'attachment; filename="lora_v1.safetensors"'}

    worker.session = FakeSession(FakeResponse(headers=header, fail_after=1))
    with pytest.raises(ConnectionError):
        await worker._download_file(download)

    worker.repo.update_filename.assert_called_once_with(
        "d1", "lora_v1.safetensors", str(tmp_path / "lora_v1.safetensors")
    )
    assert (tmp_path / "lora_v1.safetensors.part").read_bytes() == b"abcd"
    assert not (tmp_path / "3361846.part").exists()

    _, filename, destination_path = worker.repo.update_filename.call_args.args
    restored = Download(id="d1", url=URL, filename=filename, destination_path=destination_path)
    session = await _run(
        worker,
        restored,
        FakeResponse(
            headers={"Content-Disposition": 'attachment; filename="different.safetensors"'},
            chunks=[b"efgh"],
            status=206,
        ),
    )

    assert session.requests[0][1]["Range"] == "bytes=4-"
    assert restored.filename == "lora_v1.safetensors"
    assert (tmp_path / "lora_v1.safetensors").read_bytes() == b"abcdefgh"
    assert not (tmp_path / "different.safetensors").exists()


@pytest.mark.asyncio
async def test_part_file_written_under_url_name_moves_with_the_rename(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)
    (tmp_path / "3361846.part").write_bytes(b"abcd")

    session = await _run(
        worker,
        download,
        FakeResponse(headers={"Content-Disposition": 'attachment; filename="lora_v1.safetensors"'}, chunks=[b"efgh"], status=206),
    )

    assert session.requests[0][1]["Range"] == "bytes=4-"
    assert (tmp_path / "lora_v1.safetensors").read_bytes() == b"abcdefgh"
    assert not (tmp_path / "3361846.part").exists()


@pytest.mark.asyncio
async def test_caller_supplied_name_equal_to_the_url_basename_is_never_renamed(tmp_path):
    provider = _Provider("Provider Name.safetensors")
    worker = _worker(provider)
    download = _download(tmp_path, supplied=True)

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": 'attachment; filename="other.safetensors"'}))

    assert provider.name_lookups == []
    assert download.filename == "3361846"
    assert (tmp_path / "3361846").read_bytes() == b"abcdefgh"
    worker.repo.update_filename.assert_not_called()


@pytest.mark.asyncio
async def test_long_multibyte_header_name_is_cut_in_bytes_and_keeps_its_extension(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)
    header = "attachment; filename*=UTF-8''" + quote("模" * 120 + ".safetensors")

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": header}))

    assert download.filename.endswith(".safetensors")
    assert download.filename.startswith("模")
    assert len((download.filename + ".part").encode("utf-8")) <= 255
    assert len(download.filename) <= 200
    assert (tmp_path / download.filename).read_bytes() == b"abcdefgh"
    assert not (tmp_path / (download.filename + ".part")).exists()


@pytest.mark.asyncio
async def test_cdn_response_content_disposition_names_the_file(tmp_path):
    signed = (
        "https://cdn.example.com/signed/blob?sig=1&response-content-disposition="
        + quote('attachment; filename="cdn-name.safetensors"', safe="")
    )
    provider = _Provider(prepared_url=signed)
    worker = _worker(provider)
    download = _download(tmp_path)

    session = await _run(worker, download, FakeResponse(headers={"Content-Disposition": 'attachment; filename="late.safetensors"'}))

    assert session.requests[0][0] == signed
    assert download.filename == "cdn-name.safetensors"
    assert (tmp_path / "cdn-name.safetensors").read_bytes() == b"abcdefgh"
    assert not (tmp_path / "late.safetensors").exists()


@pytest.mark.asyncio
async def test_another_downloads_part_file_is_never_adopted(tmp_path):
    worker = _worker(_Provider("taken.safetensors"))
    download = _download(tmp_path)
    (tmp_path / "taken.safetensors.part").write_bytes(b"zz")

    session = await _run(worker, download, FakeResponse())

    assert "Range" not in session.requests[0][1]
    assert download.filename == "3361846"
    assert (tmp_path / "3361846").read_bytes() == b"abcdefgh"
    assert (tmp_path / "taken.safetensors.part").read_bytes() == b"zz"
    worker.repo.update_filename.assert_not_called()


@pytest.mark.asyncio
async def test_a_header_name_colliding_with_another_part_file_keeps_the_current_name(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)
    (tmp_path / "taken.safetensors.part").write_bytes(b"zz")

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": 'attachment; filename="taken.safetensors"'}))

    assert download.filename == "3361846"
    assert (tmp_path / "3361846").read_bytes() == b"abcdefgh"
    assert (tmp_path / "taken.safetensors.part").read_bytes() == b"zz"


@pytest.mark.asyncio
async def test_an_adopted_name_is_final(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": 'attachment; filename="lora_v1.safetensors"'}))

    assert download.filename_supplied is True


@pytest.mark.asyncio
async def test_two_concurrent_downloads_deriving_the_same_name_do_not_share_it(tmp_path):
    rows = {}
    repo = Mock()

    def update_filename(download_id, filename, destination_path):
        rows[download_id] = destination_path
        return True

    def is_claimed(destination_path, exclude_id=None):
        return any(path == destination_path for row_id, path in rows.items() if row_id != exclude_id)

    repo.update_filename.side_effect = update_filename
    repo.is_destination_claimed.side_effect = is_claimed
    registry = Mock()
    registry.find_provider_for_url.return_value = _Provider("same.safetensors")
    registry.get_provider.return_value = registry.find_provider_for_url.return_value
    worker = DownloadWorker(
        settings=DownloadSettings(chunk_size_kb=1),
        repo=repo,
        connection_hub=AsyncMock(),
        provider_registry_factory=lambda: registry,
    )
    first = Download(id="d1", url=URL, filename="one", destination_path=str(tmp_path / "one"), filename_supplied=False)
    second = Download(id="d2", url=URL + "2", filename="two", destination_path=str(tmp_path / "two"), filename_supplied=False)
    worker.session = FakeSession(FakeResponse(chunks=[b"1111", b"1111"]), FakeResponse(chunks=[b"2222", b"2222"]))

    await asyncio.gather(worker._download_file(first), worker._download_file(second))

    assert (first.filename, second.filename) == ("same.safetensors", "two")
    assert first.destination_path != second.destination_path
    contents = sorted(p.read_bytes() for p in tmp_path.iterdir())
    assert contents == [b"11111111", b"22222222"]
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.asyncio
async def test_a_name_held_by_an_active_row_is_not_adopted_even_without_a_part_file(tmp_path):
    worker = _worker(_Provider("held.safetensors"))
    worker.repo.is_destination_claimed.return_value = True
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse())

    assert download.filename == "3361846"
    assert not (tmp_path / "held.safetensors.part").exists()
    worker.repo.update_filename.assert_not_called()


@pytest.mark.asyncio
async def test_a_finished_file_under_the_derived_name_is_not_overwritten(tmp_path):
    worker = _worker(_Provider("done.safetensors"))
    (tmp_path / "done.safetensors").write_bytes(b"keep")
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse())

    assert (tmp_path / "done.safetensors").read_bytes() == b"keep"
    assert (tmp_path / "3361846").read_bytes() == b"abcdefgh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header_name",
    ["CON.safetensors", "nul", "COM1.txt", "aux.bin", "lpt1.tar.gz", "CON.", "..", "sub/dir.bin", "back\\slash.bin"],
)
async def test_an_unusable_header_name_leaves_the_current_name_in_place(tmp_path, header_name):
    worker = _worker(_Provider())
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": f'attachment; filename="{header_name}"'}))

    assert download.filename == "3361846"
    assert (tmp_path / "3361846").read_bytes() == b"abcdefgh"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["3361846"]
    worker.repo.update_filename.assert_not_called()


@pytest.mark.asyncio
async def test_a_trailing_dot_and_space_are_trimmed_from_an_otherwise_valid_header_name(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)

    await _run(worker, download, FakeResponse(headers={"Content-Disposition": 'attachment; filename="model.safetensors.. "'}))

    assert download.filename == "model.safetensors"
    assert (tmp_path / "model.safetensors").exists()


@pytest.mark.asyncio
async def test_a_part_file_appearing_between_the_collision_check_and_the_claim_is_not_taken_over(tmp_path):
    worker = _worker(_Provider("raced.safetensors"))
    raced_part = tmp_path / "raced.safetensors.part"

    def other_download_starts_writing(destination_path, exclude_id=None):
        raced_part.write_bytes(b"zz")
        return False

    worker.repo.is_destination_claimed.side_effect = other_download_starts_writing
    download = _download(tmp_path)

    session = await _run(worker, download, FakeResponse())

    assert "Range" not in session.requests[0][1]
    assert download.filename == "3361846"
    assert raced_part.read_bytes() == b"zz"
    assert (tmp_path / "3361846").read_bytes() == b"abcdefgh"
    worker.repo.update_filename.assert_not_called()


def test_resolve_provider_derives_from_url_when_none_is_set(tmp_path):
    provider = _Provider()
    worker = _worker(provider)
    assert worker._resolve_provider(_download(tmp_path)) is provider


def test_resolve_provider_uses_the_explicit_id(tmp_path):
    provider = _Provider()
    worker = _worker(provider)
    download = _download(tmp_path)
    download.provider_id = "civitai"
    assert worker._resolve_provider(download) is provider


def test_resolve_provider_honours_explicit_none(tmp_path):
    worker = _worker(_Provider())
    download = _download(tmp_path)
    download.provider_id = NO_PROVIDER
    assert worker._resolve_provider(download) is None
