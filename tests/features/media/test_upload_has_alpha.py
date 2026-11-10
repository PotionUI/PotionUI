"""`uploads.has_alpha` end to end: probe at upload, repository writes, replace path."""

import asyncio
import io
import types
import unittest
from unittest.mock import patch

from PIL import Image

from src.features.library import operations
from src.features.library.mappers import upload_to_item
from src.features.media import ImageProcessor, MediaStore, MediaTypeResolver
from src.features.media.records import Upload
from tests.features.library.test_operations import LibraryTestBase


class _NoopPlugins:
    def execute_hook(self, hook_name, initial_data=None, **kwargs):
        return types.SimpleNamespace(data=dict(initial_data or {})), []


def _png(mode, color):
    buffer = io.BytesIO()
    Image.new(mode, (4, 4), color).save(buffer, "PNG")
    return buffer.getvalue()


def _jpg():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buffer, "JPEG")
    return buffer.getvalue()


class TestUploadHasAlpha(LibraryTestBase):

    def setUp(self):
        super().setUp()
        self.store = MediaStore(
            file_resolver=self.file_resolver,
            image_processor=ImageProcessor(),
            media_type_resolver=MediaTypeResolver(),
            file_repository=self.file_repo,
            generation_repository=None,
            settings=None,
            file_service=self.file_store,
            plugin_registry=_NoopPlugins(),
            upload_repository=self.upload_repo,
            storage_driver=self.storage_driver,
        )

    def _upload(self, data, name, content_type):
        result = asyncio.run(self.store.upload_media(data, name, content_type, user_id=self.user_id))
        return self.upload_repo.get_by_filename(result.filename, self.user_id)

    def _row(self, **overrides):
        values = dict(user_id=self.user_id, filename="r.png", media_type="image")
        values.update(overrides)
        return self.upload_repo.create(Upload(**values))

    def _replace(self, row, **overrides):
        values = dict(
            upload_id=row.id,
            user_id=self.user_id,
            expected_filename=row.filename,
            filename="new.png",
            mime_type="image/png",
            width=4,
            height=4,
            duration_seconds=None,
            fps=None,
            file_size=10,
            content_hash="h",
            thumbnail_small=None,
            thumbnail_medium=None,
            thumbnail_large=None,
            thumbnail_profile=None,
        )
        values.update(overrides)
        return self.upload_repo.replace_file(**values)

    def test_opaque_png_is_recorded_without_alpha(self):
        row = self._upload(_png("RGB", (1, 2, 3)), "a.png", "image/png")

        self.assertIs(row.has_alpha, False)

    def test_rgba_png_is_recorded_with_alpha(self):
        row = self._upload(_png("RGBA", (1, 2, 3, 40)), "a.png", "image/png")

        self.assertIs(row.has_alpha, True)

    def test_jpeg_is_recorded_without_alpha(self):
        row = self._upload(_jpg(), "a.jpg", "image/jpeg")

        self.assertIs(row.has_alpha, False)

    def test_video_and_audio_leave_alpha_unknown(self):
        with patch("src.features.media.store.media_probe") as probe, \
             patch.object(self.store, "_schedule_upload_video_thumbnails") as schedule:
            probe.get_video_dimensions.return_value = (4, 4)
            probe.get_video_duration_fps.return_value = (1.0, 24.0)
            probe.get_audio_duration_seconds.return_value = 1.0
            video = self._upload(b"not really a video", "a.mp4", "video/mp4")
            audio = self._upload(b"not really audio", "a.wav", "audio/wav")

        schedule.assert_called_once()
        self.assertEqual(schedule.call_args.args[2], video.id)
        self.assertIsNone(video.has_alpha)
        self.assertIsNone(audio.has_alpha)

    def test_an_unreadable_image_leaves_alpha_unknown(self):
        row = self._upload(b"garbage that is not a png", "a.png", "image/png")

        self.assertIsNone(row.has_alpha)

    def test_upload_info_reports_alpha_for_an_existing_upload(self):
        row = self._upload(_png("RGBA", (1, 2, 3, 40)), "a.png", "image/png")

        info = self.store.get_upload_info(row.filename, self.user_id)

        self.assertIs(info.has_alpha, True)

    def test_create_round_trips_each_alpha_state(self):
        self.assertIsNone(self._row(filename="n.png").has_alpha)
        self.assertIs(self._row(filename="t.png", has_alpha=True).has_alpha, True)
        self.assertIs(self._row(filename="f.png", has_alpha=False).has_alpha, False)

    def test_replace_file_overwrites_a_stale_alpha_flag(self):
        row = self._row(has_alpha=False)

        updated = self._replace(row, has_alpha=True)

        self.assertIs(updated.has_alpha, True)
        self.assertIs(self.upload_repo.get_by_id(row.id, self.user_id).has_alpha, True)

    def test_replace_file_can_clear_alpha_back_to_opaque_or_unknown(self):
        row = self._row(has_alpha=True)

        opaque = self._replace(row, has_alpha=False)
        unknown = self._replace(opaque, filename="newer.png", has_alpha=None)

        self.assertIs(opaque.has_alpha, False)
        self.assertIsNone(unknown.has_alpha)

    def test_update_file_overwrites_alpha(self):
        row = self._row(has_alpha=False)

        updated = self.upload_repo.update_file(
            row.id, self.user_id, "u.png", "image/png", 4, 4, None, None, 10, has_alpha=True
        )

        self.assertIs(updated.has_alpha, True)

    def test_library_item_serialises_alpha(self):
        row = self._row(has_alpha=True)

        item = upload_to_item(row, [])

        self.assertIs(item.has_alpha, True)
        self.assertIs(item.model_dump()["has_alpha"], True)
        self.assertIsNone(upload_to_item(self._row(filename="n.png"), []).has_alpha)


class TestCopyFromGenerationAlpha(LibraryTestBase):

    def _copy(self, alpha, suffix=".png"):
        _, file_record, _ = self._generated_file(suffix=suffix)
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE files SET has_alpha = ? WHERE id = ?", (int(alpha), file_record.id))
        return operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

    def test_copy_takes_alpha_from_the_source_file_row(self):
        self.assertIs(self._copy(True).has_alpha, True)
        self.assertIs(self._copy(False).has_alpha, False)


if __name__ == "__main__":
    unittest.main()
