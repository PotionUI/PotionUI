"""Tests for GenerationController._stream_zip_response / _SpooledZipResponse -
the shared machinery that streams an already-built export zip (a
SpooledTemporaryFile) as the HTTP response body without materializing it as
bytes, and closes it on every exit path: normal completion, an exception
raised from the ASGI `send()` itself, or a client disconnect.

These drive the real ASGI `__call__(scope, receive, send)` contract (a fake
send/receive, no live server) rather than draining `body_iterator` directly -
`_SpooledZipResponse` only guarantees the close from inside `__call__`.
"""

import asyncio
import io
import tempfile
import zipfile
from unittest.mock import AsyncMock, Mock

import pytest

import src.features.generation.routes as routes_module
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.routes import GenerationController

_CALL_TIMEOUT = 5.0


def make_controller() -> GenerationController:
    facade = GenerationHistoryFacade(
        generation_repo=Mock(),
        file_service=Mock(),
        plugin_registry=Mock(),
        run_report_repository=Mock(),
    )
    return GenerationController(
        generation_orchestrator=Mock(),
        generation_history_facade=facade,
        file_service=Mock(),
        run_report_recorder=Mock(),
    )


def _spooled_zip(entries: dict) -> tempfile.SpooledTemporaryFile:
    spooled = tempfile.SpooledTemporaryFile()
    with zipfile.ZipFile(spooled, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    spooled.seek(0)
    return spooled


async def _never_disconnect():
    """A `receive` that never returns - the response's own task group
    cancels this side once the send loop finishes, same as a real client
    that stays connected for the whole response."""
    await asyncio.Event().wait()


class _RecordingSend:
    """A `send` that records every ASGI message it's given."""

    def __init__(self):
        self.messages = []

    async def __call__(self, message) -> None:
        self.messages.append(message)

    @property
    def body(self) -> bytes:
        return b"".join(
            m["body"] for m in self.messages if m["type"] == "http.response.body"
        )


async def _call(response, receive=None, send=None):
    send = send or _RecordingSend()
    await asyncio.wait_for(response({}, receive or _never_disconnect, send), timeout=_CALL_TIMEOUT)
    return send


def _contains_connection_reset(exc: BaseException) -> bool:
    """anyio's task group wraps a `send()` failure in a `BaseExceptionGroup`
    (Python 3.11+) rather than letting it propagate bare."""
    if isinstance(exc, ConnectionResetError):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_contains_connection_reset(sub) for sub in exc.exceptions)
    return False


class _RaisingTellFile:
    """Wraps a real spooled file so `.tell()` raises - simulates a failure
    inside `_stream_zip_response`'s measure/construct interval, before any
    `_SpooledZipResponse` exists to take over ownership of closing it."""

    def __init__(self, inner):
        self._inner = inner
        self.closed = False

    def seek(self, *args, **kwargs):
        return self._inner.seek(*args, **kwargs)

    def tell(self):
        raise OSError("simulated failure measuring content length")

    def read(self, *args, **kwargs):
        return self._inner.read(*args, **kwargs)

    def close(self):
        self.closed = True
        self._inner.close()


class TestStreamZipResponseConstructionFailure:
    def test_closes_the_file_when_measuring_content_length_raises(self):
        tracked = _RaisingTellFile(_spooled_zip({"a.txt": b"hello"}))

        with pytest.raises(OSError):
            GenerationController._stream_zip_response(tracked, "export.zip")

        assert tracked.closed is True


class TestStreamZipResponseHeaders:
    def test_sets_content_length_and_disposition_headers(self):
        zip_file = _spooled_zip({"a.txt": b"hello world"})
        zip_file.seek(0, io.SEEK_END)
        expected_length = zip_file.tell()
        zip_file.seek(0)

        response = GenerationController._stream_zip_response(zip_file, "export.zip")

        assert response.headers["content-length"] == str(expected_length)
        assert response.headers["content-disposition"] == 'attachment; filename="export.zip"'
        assert response.media_type == "application/zip"
        zip_file.close()


class TestSpooledZipResponseAsgiCleanup:
    """Drives `_SpooledZipResponse` through the real ASGI `__call__`
    contract, proving `zip_file` is closed on every exit path."""

    async def test_streamed_bytes_reproduce_the_original_archive_and_file_is_closed(self):
        zip_file = _spooled_zip({"gen1/0.png": b"fake-image-bytes" * 100})

        response = GenerationController._stream_zip_response(zip_file, "export.zip")
        send = await _call(response)

        zf = zipfile.ZipFile(io.BytesIO(send.body))
        assert zf.namelist() == ["gen1/0.png"]
        assert zf.read("gen1/0.png") == b"fake-image-bytes" * 100
        assert zip_file.closed

    async def test_closes_file_when_send_raises_before_first_chunk(self):
        """Without __call__'s own `finally`, an exception from `send()` skips
        straight past Starlette's `background` line - nothing would close
        the file."""
        zip_file = _spooled_zip({"a.txt": b"hello"})
        response = GenerationController._stream_zip_response(zip_file, "export.zip")

        async def failing_send(message):
            raise ConnectionResetError("simulated broken pipe")

        with pytest.raises(BaseException) as exc_info:
            await asyncio.wait_for(
                response({}, _never_disconnect, failing_send), timeout=_CALL_TIMEOUT
            )
        assert _contains_connection_reset(exc_info.value)

        assert zip_file.closed

    async def test_closes_file_when_send_raises_after_one_chunk(self, monkeypatch):
        monkeypatch.setattr(routes_module, "_EXPORT_STREAM_CHUNK_BYTES", 4)

        zip_file = _spooled_zip({"a.txt": b"a body longer than one small chunk"})
        response = GenerationController._stream_zip_response(zip_file, "export.zip")

        calls = []

        async def flaky_send(message):
            calls.append(message)
            # 1st call = http.response.start, 2nd call = first body chunk.
            if len(calls) == 2:
                raise ConnectionResetError("simulated broken pipe")

        with pytest.raises(BaseException) as exc_info:
            await asyncio.wait_for(
                response({}, _never_disconnect, flaky_send), timeout=_CALL_TIMEOUT
            )
        assert _contains_connection_reset(exc_info.value)

        assert len(calls) == 2
        assert zip_file.closed

    async def test_closes_file_on_client_disconnect_mid_stream(self):
        zip_file = _spooled_zip({"a.txt": b"hello"})
        response = GenerationController._stream_zip_response(zip_file, "export.zip")

        async def disconnect_immediately():
            return {"type": "http.disconnect"}

        async def hanging_send(message):
            # Never resolves on its own - only the disconnect's cancellation
            # of the sibling send-loop task gets this to return at all.
            await asyncio.Event().wait()

        await asyncio.wait_for(
            response({}, disconnect_immediately, hanging_send), timeout=_CALL_TIMEOUT
        )

        assert zip_file.closed


class TestExportEndpointsUseStreaming:
    async def test_export_generations_returns_streamed_response_backed_by_facade_file(self):
        controller = make_controller()
        zip_file = _spooled_zip({"gen1/0.png": b"data"})
        controller.history_facade.export_zip_async = AsyncMock(
            return_value=(zip_file, "potionui-export.zip")
        )

        response = await controller.export_generations(
            generation_ids=["gen1"], strip_metadata=False, current_user=Mock(id="u1")
        )

        assert response.headers["content-disposition"] == 'attachment; filename="potionui-export.zip"'
        send = await _call(response)
        assert len(send.body) > 0
        assert zip_file.closed

    async def test_export_generation_bundle_returns_streamed_response_backed_by_facade_file(self):
        controller = make_controller()
        zip_file = _spooled_zip({"generation.json": b"{}"})
        controller.history_facade.export_bundle_async = AsyncMock(
            return_value=(zip_file, "potionui-generation-gen1.zip")
        )

        response = await controller.export_generation_bundle("gen1", current_user=Mock(id="u1"))

        assert response.headers["content-disposition"] == 'attachment; filename="potionui-generation-gen1.zip"'
        send = await _call(response)
        assert len(send.body) > 0
        assert zip_file.closed
