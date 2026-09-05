"""Tests for GenerationController._stream_zip_response - the shared helper
that streams an already-built export zip (a SpooledTemporaryFile) as the HTTP
response body without materializing it as bytes, and closes it afterward.
"""

import io
import tempfile
import zipfile
from unittest.mock import AsyncMock, Mock

from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.routes import GenerationController


def make_controller() -> GenerationController:
    facade = GenerationHistoryFacade(
        generation_repo=Mock(),
        file_service=Mock(),
        plugin_registry=Mock(),
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


async def _drain(body_iterator) -> bytes:
    chunks = []
    async for chunk in body_iterator:
        chunks.append(chunk)
    return b"".join(chunks)


class TestStreamZipResponse:
    def test_sets_content_length_and_disposition_headers(self):
        zip_file = _spooled_zip({"a.txt": b"hello world"})
        zip_file.seek(0, 2)
        expected_length = zip_file.tell()
        zip_file.seek(0)

        response = GenerationController._stream_zip_response(zip_file, "export.zip")

        assert response.headers["content-length"] == str(expected_length)
        assert response.headers["content-disposition"] == 'attachment; filename="export.zip"'
        assert response.media_type == "application/zip"

    async def test_streamed_bytes_reproduce_the_original_archive(self):
        zip_file = _spooled_zip({"gen1/0.png": b"fake-image-bytes" * 100})

        response = GenerationController._stream_zip_response(zip_file, "export.zip")
        streamed = await _drain(response.body_iterator)

        zf = zipfile.ZipFile(io.BytesIO(streamed))
        assert zf.namelist() == ["gen1/0.png"]
        assert zf.read("gen1/0.png") == b"fake-image-bytes" * 100

    async def test_spooled_file_is_closed_after_full_consumption(self):
        """Without the generator's `finally` close, the spooled file (and its
        backing temp file, once rolled over) would leak past the response."""
        zip_file = _spooled_zip({"a.txt": b"content"})

        response = GenerationController._stream_zip_response(zip_file, "export.zip")
        assert not zip_file.closed

        await _drain(response.body_iterator)

        assert zip_file.closed

    async def test_background_task_close_is_idempotent_after_generator_already_closed(self):
        zip_file = _spooled_zip({"a.txt": b"content"})

        response = GenerationController._stream_zip_response(zip_file, "export.zip")
        await _drain(response.body_iterator)
        assert zip_file.closed

        # Starlette always runs `background` after the response completes -
        # closing an already-closed file must not raise.
        await response.background()
        assert zip_file.closed

    async def test_background_task_closes_file_if_generator_never_ran(self):
        """Covers the client-disconnect path: the streaming generator may
        never be driven to completion, so the background task is the only
        thing that reliably closes the file."""
        zip_file = _spooled_zip({"a.txt": b"content"})

        response = GenerationController._stream_zip_response(zip_file, "export.zip")
        assert not zip_file.closed

        await response.background()

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
        streamed = await _drain(response.body_iterator)
        assert len(streamed) > 0
        assert zip_file.closed

    async def test_export_generation_bundle_returns_streamed_response_backed_by_facade_file(self):
        controller = make_controller()
        zip_file = _spooled_zip({"generation.json": b"{}"})
        controller.history_facade.export_bundle_async = AsyncMock(
            return_value=(zip_file, "potionui-generation-gen1.zip")
        )

        response = await controller.export_generation_bundle("gen1", current_user=Mock(id="u1"))

        assert response.headers["content-disposition"] == 'attachment; filename="potionui-generation-gen1.zip"'
        streamed = await _drain(response.body_iterator)
        assert len(streamed) > 0
        assert zip_file.closed
