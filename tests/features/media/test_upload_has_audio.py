import asyncio
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src.features.library import operations
from src.features.library.mappers import upload_to_item
from src.features.media import ImageProcessor, MediaStore, MediaTypeResolver
from src.features.media.records import Upload
from src.platform.util.video_stream import AUDIO_PROBE_FAILED, AudioProbeResult
from tests.features.library.test_operations import LibraryTestBase
from tests.platform.media_tools.media_factory import ffmpeg_available, make_audio, make_image, make_video

needs_ffmpeg = unittest.skipUnless(ffmpeg_available(), "ffmpeg is not available")


class _NoopPlugins:
    def execute_hook(self, hook_name, initial_data=None, **kwargs):
        return types.SimpleNamespace(data=dict(initial_data or {})), []


class TestUploadHasAudio(LibraryTestBase):

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
        self.made = Path(self.temp_dir) / "made"
        self.made.mkdir(exist_ok=True)

    def _upload(self, path, content_type):
        with patch.object(self.store, "_schedule_upload_video_thumbnails"):
            result = asyncio.run(
                self.store.upload_media(path.read_bytes(), path.name, content_type, user_id=self.user_id)
            )
        return self.upload_repo.get_by_filename(result.filename, self.user_id)

    def _row(self, **overrides):
        values = dict(user_id=self.user_id, filename="r.mp4", media_type="video")
        values.update(overrides)
        return self.upload_repo.create(Upload(**values))

    def _replace(self, row, **overrides):
        values = dict(
            upload_id=row.id,
            user_id=self.user_id,
            expected_filename=row.filename,
            filename="new.mp4",
            mime_type="video/mp4",
            width=4,
            height=4,
            duration_seconds=1.0,
            fps=10.0,
            file_size=10,
            content_hash="h",
            thumbnail_small=None,
            thumbnail_medium=None,
            thumbnail_large=None,
            thumbnail_profile=None,
        )
        values.update(overrides)
        return self.upload_repo.replace_file(**values)

    @needs_ffmpeg
    def test_a_video_with_a_sound_track_is_recorded_with_sound(self):
        row = self._upload(make_video(self.made / "talk.mp4", audio=True), "video/mp4")

        self.assertIs(row.has_audio, True)

    @needs_ffmpeg
    def test_a_silent_video_is_recorded_without_sound(self):
        row = self._upload(make_video(self.made / "quiet.mp4", audio=False), "video/mp4")

        self.assertIs(row.has_audio, False)

    @needs_ffmpeg
    def test_a_sound_file_is_recorded_with_sound(self):
        row = self._upload(make_audio(self.made / "tone.wav"), "audio/wav")

        self.assertIs(row.has_audio, True)

    def test_an_image_leaves_sound_unknown(self):
        row = self._upload(make_image(self.made / "a.png", (4, 4)), "image/png")

        self.assertIsNone(row.has_audio)

    @needs_ffmpeg
    def test_a_failed_sound_probe_leaves_sound_unknown_and_the_upload_succeeds(self):
        failed = AudioProbeResult(AUDIO_PROBE_FAILED, "ffprobe timed out")
        with patch("src.platform.util.video_stream.probe_audio_stream", return_value=failed):
            row = self._upload(make_video(self.made / "talk.mp4", audio=True), "video/mp4")

        self.assertIsNotNone(row)
        self.assertIsNone(row.has_audio)
        self.assertEqual((row.width, row.height), (64, 48))

    def test_a_sound_probe_that_raises_leaves_sound_unknown_and_the_upload_succeeds(self):
        with patch("src.features.media.store.media_probe") as probe, \
             patch("src.features.media.store.audio_presence", side_effect=OSError("no ffprobe")) as presence:
            probe.get_video_dimensions.return_value = (4, 4)
            probe.get_video_duration_fps.return_value = (1.0, 24.0)
            fake = self.made / "a.mp4"
            fake.write_bytes(b"not really a video")
            row = self._upload(fake, "video/mp4")

        self.assertIsNotNone(row)
        self.assertIsNone(row.has_audio)
        presence.assert_called_once()

    def test_the_sound_probe_runs_off_the_event_loop_thread(self):
        seen = []
        with patch("src.features.media.store.media_probe") as probe, \
             patch("src.features.media.store.audio_presence", side_effect=lambda path: seen.append(threading.get_ident())):
            probe.get_video_dimensions.return_value = (4, 4)
            probe.get_video_duration_fps.return_value = (1.0, 24.0)
            fake = self.made / "b.mp4"
            fake.write_bytes(b"not really a video either")
            self._upload(fake, "video/mp4")

        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], threading.get_ident())

    def test_create_round_trips_each_sound_state(self):
        self.assertIsNone(self._row(filename="n.mp4").has_audio)
        self.assertIs(self._row(filename="t.mp4", has_audio=True).has_audio, True)
        self.assertIs(self._row(filename="f.mp4", has_audio=False).has_audio, False)

    def test_replace_file_overwrites_a_stale_sound_flag(self):
        row = self._row(has_audio=True)

        updated = self._replace(row, has_audio=False)

        self.assertIs(updated.has_audio, False)
        self.assertIs(self.upload_repo.get_by_id(row.id, self.user_id).has_audio, False)

    def test_replace_file_without_a_sound_value_makes_it_unknown_rather_than_keeping_it(self):
        row = self._row(has_audio=True)

        updated = self._replace(row)

        self.assertIsNone(updated.has_audio)

    def test_update_file_without_a_sound_value_makes_it_unknown_rather_than_keeping_it(self):
        row = self._row(has_audio=True)

        updated = self.upload_repo.update_file(
            row.id, self.user_id, "u.mp4", "video/mp4", 4, 4, 1.0, 10.0, 10
        )

        self.assertIsNone(updated.has_audio)

    def test_library_item_serialises_sound(self):
        item = upload_to_item(self._row(has_audio=False), [])

        self.assertIs(item.has_audio, False)
        self.assertIs(item.model_dump()["has_audio"], False)
        self.assertIsNone(upload_to_item(self._row(filename="n.mp4"), []).has_audio)


class TestCopyFromGenerationSound(LibraryTestBase):

    def _copy(self, path, file_type, mime_type):
        _, file_record, on_disk = self._generated_file(content=path.read_bytes(), suffix=path.suffix)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE files SET file_type = ?, mime_type = ? WHERE id = ?", (file_type, mime_type, file_record.id)
            )
        return operations.copy_generation_file(self.collaborators, file_record.id, self.user_id)

    def made(self):
        folder = Path(self.temp_dir) / "made"
        folder.mkdir(exist_ok=True)
        return folder

    @needs_ffmpeg
    def test_copying_a_generated_video_records_whether_it_has_sound(self):
        talking = self._copy(make_video(self.made() / "talk.mp4", audio=True), "VIDEO", "video/mp4")
        silent = self._copy(make_video(self.made() / "quiet.mp4", audio=False), "VIDEO", "video/mp4")

        self.assertIs(talking.has_audio, True)
        self.assertIs(silent.has_audio, False)

    @needs_ffmpeg
    def test_copying_a_generated_sound_records_sound(self):
        copied = self._copy(make_audio(self.made() / "tone.wav"), "AUDIO", "audio/wav")

        self.assertIs(copied.has_audio, True)

    def test_copying_a_generated_image_leaves_sound_unknown(self):
        copied = self._copy(make_image(self.made() / "a.png", (4, 4)), "IMAGE", "image/png")

        self.assertIsNone(copied.has_audio)


if __name__ == "__main__":
    unittest.main()
