"""An upload with no usable extension is typed by its CONTENT, not as '.png'.

`_process_uploaded_file` used to default an extension-less upload to '.png' and
type it IMAGE regardless of what the bytes were, so an unnamed video/audio/mesh
was recorded as an image: wrong player, wrong thumbnailer, wrong serve MIME, and
`Image.open` attempted on a video. Driven through the real `upload_generations`
entry point, mirroring TestUploadGenerationsFileTypes.
"""

import io

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from PIL import Image

from src.features.generation.history_facade import GenerationHistoryFacade


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color="green").save(buf, format="PNG")
    return buf.getvalue()


# Minimal but real container headers - enough for signature detection, which is
# all the upload path inspects.
MP4_BYTES = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 32
WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
GLB_BYTES = b"glTF\x02\x00\x00\x00" + b"\x00" * 32


class TestExtensionlessUploadTyping:
    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_plugins = Mock()

        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.mock_repo.create.return_value = Mock()

        created_file = Mock()
        created_file.to_dict.return_value = {}
        self.mock_repo.add_file.return_value = created_file

    def _make_manager(self, tmp_path):
        mock_file_service = Mock()
        mock_file_service.get_full_path.side_effect = lambda rel: str(tmp_path / rel)
        return GenerationHistoryFacade(
            generation_repo=self.mock_repo,
            file_service=mock_file_service,
            plugin_registry=self.mock_plugins,
        )

    async def _upload(self, tmp_path, filename, content, content_type):
        manager = self._make_manager(tmp_path)
        mock_file = MagicMock()
        mock_file.filename = filename
        mock_file.content_type = content_type
        mock_file.read = AsyncMock(return_value=content)
        await manager.upload_generations([mock_file], [], "user-123")
        return self.mock_repo.add_file.call_args[0][1]

    @pytest.mark.asyncio
    async def test_extensionless_video_is_typed_video(self, tmp_path):
        record = await self._upload(tmp_path, "clip", MP4_BYTES, "video/mp4")

        assert record.file_type == "VIDEO"
        assert record.file_path.endswith(".mp4")
        assert record.mime_type == "video/mp4"
        assert record.thumbnail_small is None

    @pytest.mark.asyncio
    async def test_extensionless_audio_is_typed_audio(self, tmp_path):
        record = await self._upload(tmp_path, "track", WAV_BYTES, "audio/wav")

        assert record.file_type == "AUDIO"
        assert record.file_path.endswith(".wav")
        assert record.mime_type == "audio/wav"

    @pytest.mark.asyncio
    async def test_extensionless_mesh_is_typed_mesh(self, tmp_path):
        record = await self._upload(tmp_path, "model", GLB_BYTES, "model/gltf-binary")

        assert record.file_type == "MESH"
        assert record.file_path.endswith(".glb")
        assert record.width is None

    @pytest.mark.asyncio
    async def test_unrecognised_extension_is_decided_by_content(self, tmp_path):
        """`.bin` is claimed by no registry, so it fell through to IMAGE too."""
        record = await self._upload(tmp_path, "payload.bin", MP4_BYTES, "video/mp4")

        assert record.file_type == "VIDEO"
        assert record.file_path.endswith(".mp4")

    @pytest.mark.asyncio
    async def test_extensionless_image_still_gets_dimensions_and_thumbnails(self, tmp_path):
        record = await self._upload(tmp_path, "photo", _png_bytes(), "image/png")

        assert record.file_type == "IMAGE"
        assert record.file_path.endswith(".png")
        assert record.width == 16
        assert record.height == 16
        assert record.thumbnail_small is not None

    @pytest.mark.asyncio
    async def test_declared_extension_still_wins_over_content(self, tmp_path):
        """Sniffing only fills a gap: a recognised extension is left alone."""
        record = await self._upload(tmp_path, "photo.png", _png_bytes(), "image/png")

        assert record.file_path.endswith(".png")
        assert record.file_type == "IMAGE"

    @pytest.mark.asyncio
    async def test_unrecognisable_bytes_keep_the_png_last_resort(self, tmp_path):
        record = await self._upload(
            tmp_path, "mystery", b"\x00\x01\x02\x03nothing here", "image/png"
        )

        assert record.file_path.endswith(".png")
        assert record.file_type == "IMAGE"
