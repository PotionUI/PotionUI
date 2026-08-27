"""Tests for GenerationHistoryFacade.export_zip (bulk export as zip)."""

import io
import zipfile
from pathlib import Path

import pytest
from unittest.mock import Mock
from PIL import Image

from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.exceptions import GenerationNotFoundException
from src.platform.filesystem.file_store import FileStore


def _make_file(relative_path, file_type='IMAGE', mime_type='image/png'):
    """Create a File-like mock with the fields export_zip touches."""
    f = Mock()
    f.file_path = relative_path
    f.file_type = file_type
    f.mime_type = mime_type
    return f


class TestExportZip:
    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_plugins = Mock()

    def _manager(self, storage_root):
        """A manager wired with a real FileStore over `storage_root` - export
        reads bytes back through `self.file_service.local_copy_of`, so the
        double needs to actually resolve keys to files, not just record calls."""
        return GenerationHistoryFacade(
            generation_repo=self.mock_repo,
            file_service=FileStore(str(storage_root)),
            plugin_registry=self.mock_plugins,
        )

    def _write_image_with_metadata(self, path, size=(16, 16), color=(255, 0, 0)):
        """Write a PNG with embedded text metadata to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new('RGB', size, color)
        from PIL.PngImagePlugin import PngInfo
        meta = PngInfo()
        meta.add_text("workflow", "secret-workflow-json")
        meta.add_text("prompt", "a red square")
        img.save(path, format='PNG', pnginfo=meta)

    def test_export_zip_contains_expected_entries(self, tmp_path):
        # Two generations, each with one image file at its real storage key
        img1 = tmp_path / "generations/2025-01-01/gen1/0.png"
        img2 = tmp_path / "generations/2025-01-01/gen2/0.png"
        self._write_image_with_metadata(img1)
        self._write_image_with_metadata(img2, color=(0, 255, 0))

        # Ownership check passes
        self.mock_repo.get_by_id.return_value = Mock()

        # Map generation ids -> their final files
        files_map = {
            "gen1": [_make_file("generations/2025-01-01/gen1/0.png")],
            "gen2": [_make_file("generations/2025-01-01/gen2/0.png")],
        }
        self.mock_repo.get_files.side_effect = (
            lambda gid, user_id=None, is_final=None: files_map[gid]
        )

        manager = self._manager(tmp_path)
        data, filename = manager.export_zip(["gen1", "gen2"], "user-1")

        assert filename == "potionui-export.zip"
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = set(zf.namelist())
        assert names == {"gen1/0.png", "gen2/0.png"}
        # Files are non-empty and openable as images
        for name in names:
            img = Image.open(io.BytesIO(zf.read(name)))
            img.load()

    def test_export_zip_skips_missing_files(self, tmp_path):
        img1 = tmp_path / "generations/2025-01-01/gen1/0.png"
        self._write_image_with_metadata(img1)

        self.mock_repo.get_by_id.return_value = Mock()
        files_map = {
            "gen1": [
                _make_file("generations/2025-01-01/gen1/0.png"),
                _make_file("generations/2025-01-01/gen1/1.png"),  # missing on disk
            ],
        }
        self.mock_repo.get_files.side_effect = (
            lambda gid, user_id=None, is_final=None: files_map[gid]
        )

        manager = self._manager(tmp_path)
        data, _ = manager.export_zip(["gen1"], "user-1")

        zf = zipfile.ZipFile(io.BytesIO(data))
        assert zf.namelist() == ["gen1/0.png"]

    def test_export_zip_strip_metadata_removes_text_chunks(self, tmp_path):
        img1 = tmp_path / "generations/2025-01-01/gen1/0.png"
        self._write_image_with_metadata(img1)

        # Sanity: the source really has the metadata
        with Image.open(img1) as src:
            assert src.text.get("workflow") == "secret-workflow-json"

        self.mock_repo.get_by_id.return_value = Mock()
        self.mock_repo.get_files.return_value = [
            _make_file("generations/2025-01-01/gen1/0.png")
        ]

        manager = self._manager(tmp_path)
        data, _ = manager.export_zip(
            ["gen1"], "user-1", strip_metadata=True
        )

        zf = zipfile.ZipFile(io.BytesIO(data))
        assert zf.namelist() == ["gen1/0.png"]

        exported = Image.open(io.BytesIO(zf.read("gen1/0.png")))
        exported.load()
        # Still a valid image of same size
        assert exported.size == (16, 16)
        # Metadata text chunks are gone
        assert not getattr(exported, "text", {})

    def test_export_zip_video_copied_as_is_even_with_strip(self, tmp_path):
        video = tmp_path / "generations/2025-01-01/gen1/0.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        raw = b"\x00\x00\x00\x18ftypmp42fake-video-bytes"
        video.write_bytes(raw)

        self.mock_repo.get_by_id.return_value = Mock()
        self.mock_repo.get_files.return_value = [
            _make_file(
                "generations/2025-01-01/gen1/0.mp4",
                file_type='VIDEO',
                mime_type='video/mp4',
            )
        ]

        manager = self._manager(tmp_path)
        data, _ = manager.export_zip(
            ["gen1"], "user-1", strip_metadata=True
        )

        zf = zipfile.ZipFile(io.BytesIO(data))
        assert zf.namelist() == ["gen1/0.mp4"]
        assert zf.read("gen1/0.mp4") == raw

    def test_export_zip_raises_when_generation_not_owned(self, tmp_path):
        self.mock_repo.get_by_id.return_value = None  # not found / not owned

        manager = self._manager(tmp_path)
        with pytest.raises(GenerationNotFoundException):
            manager.export_zip(["gen-missing"], "user-1")
