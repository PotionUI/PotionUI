import ssl
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from src.features.downloads import worker as worker_module
from src.features.downloads.models import Download, DownloadSettings, DownloadStatus, DownloadType
from src.features.downloads.worker import DownloadWorker

URL = "https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/main/flux.safetensors"


def _worker():
    repo = Mock()
    repo.get_all.return_value = []
    repo.get_active.return_value = []
    repo.get_pending.return_value = []
    repo.increment_retry.return_value = 1
    repo.get_by_id.return_value = Download(
        id="d1", type=DownloadType.MODEL, filename="flux.safetensors", url=URL,
        destination_path="/models/flux.safetensors",
    )
    worker = DownloadWorker(
        settings=DownloadSettings(auto_retry_failed=True, max_retries=3, chunk_size_kb=1),
        repo=repo,
        connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None,
    )
    return worker, repo


def _fail_with(monkeypatch, error):
    async def fake(self, download):
        raise error

    monkeypatch.setattr(DownloadWorker, "_download_file", fake)


def _certificate_error():
    key = Mock(host="huggingface.co", port=443, is_ssl=True, ssl=True)
    os_error = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"
    )
    return aiohttp.ClientConnectorCertificateError(key, os_error)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _certificate_error(),
        ssl.SSLCertVerificationError(1, "certificate verify failed"),
        RuntimeError("wrapped: CERTIFICATE_VERIFY_FAILED somewhere"),
    ],
)
async def test_certificate_errors_fail_without_retry(monkeypatch, error):
    worker, repo = _worker()
    _fail_with(monkeypatch, error)

    await worker._process_download("d1")

    repo.increment_retry.assert_not_called()
    assert worker.queue.empty()
    status, message = repo.update_status.call_args.args[1], repo.update_status.call_args.args[2]
    assert status == DownloadStatus.FAILED
    assert "Could not verify the secure connection to huggingface.co" in message
    assert "SSL_CERT_FILE" in message
    assert "CERTIFICATE_VERIFY_FAILED" not in message
    assert "SSLCertVerificationError" not in message


@pytest.mark.asyncio
async def test_plain_errors_still_retry(monkeypatch):
    worker, repo = _worker()
    _fail_with(monkeypatch, ConnectionError("reset"))

    await worker._process_download("d1")

    repo.increment_retry.assert_called_once_with("d1")
    assert worker.queue.qsize() == 1
    assert all(c.args[1] != DownloadStatus.FAILED for c in repo.update_status.call_args_list)


@pytest.mark.asyncio
async def test_failed_message_reaches_the_connection_hub(monkeypatch):
    worker, repo = _worker()
    _fail_with(monkeypatch, _certificate_error())

    await worker._process_download("d1")

    sent = worker.conn.send_download_status.await_args.kwargs["error_message"]
    assert sent == repo.update_status.call_args.args[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("retries_used, auto_retry", [(4, True), (1, False)])
async def test_plain_errors_fail_with_original_message_when_retries_are_spent(monkeypatch, retries_used, auto_retry):
    worker, repo = _worker()
    worker.settings.auto_retry_failed = auto_retry
    repo.increment_retry.return_value = retries_used
    _fail_with(monkeypatch, ConnectionError("reset by peer"))

    await worker._process_download("d1")

    assert worker.queue.empty()
    assert repo.update_status.call_args.args[1:] == (DownloadStatus.FAILED, "reset by peer")


@pytest.mark.asyncio
@pytest.mark.parametrize("exception_host, expected", [(None, "huggingface.co"), ("cdn.example.net", "cdn.example.net")])
async def test_message_names_the_exception_host_else_the_url_host(monkeypatch, exception_host, expected):
    seen = []
    monkeypatch.setattr(worker_module, "certificate_failure_message", lambda host: seen.append(host) or "msg")
    worker, repo = _worker()
    error = _certificate_error() if exception_host else ssl.SSLCertVerificationError(1, "x")
    if exception_host:
        error._conn_key.host = exception_host
    _fail_with(monkeypatch, error)

    await worker._process_download("d1")

    assert seen == [expected]


@pytest.mark.asyncio
async def test_start_builds_the_session_with_the_shared_connector(monkeypatch):
    sentinel = object()
    captured = {}

    class _Session:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def close(self):
            return None

    monkeypatch.setattr(worker_module, "aiohttp_connector", lambda: sentinel)
    monkeypatch.setattr(worker_module.aiohttp, "ClientSession", _Session)
    worker, repo = _worker()

    await worker.start()
    try:
        assert captured["connector"] is sentinel
    finally:
        await worker.stop()
