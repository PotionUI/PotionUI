"""The thumbnail regeneration job over a scratch storage tree.

Real rows, a real local storage driver, real PIL images; only the video
generator is stubbed, because ffmpeg is not available in this container.
"""

import io
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from src.features.generation.file_repository import FileRepository
from src.features.generation.records import File
from src.features.generation.thumbnail_profile import PROFILES, profile_hash
from src.features.media.records import Upload
from src.features.media.thumbnail_regeneration import ThumbnailJobRunning, ThumbnailRegeneration
from src.features.media.upload_repository import UploadRepository
from src.platform.filesystem.storage_driver import LocalFileStorageDriver
from tests.fixtures.persistence_base import PersistenceTestBase


def _settings_for(profile):
    settings = Mock()
    values = {
        "thumbnail_sizes": list(profile.sizes),
        "thumbnail_video_fps": profile.video_fps,
        "thumbnail_video_seconds": profile.video_seconds,
        "thumbnail_video_quality": profile.video_quality,
        "thumbnail_image_quality": profile.image_quality,
    }
    settings.get_setting.side_effect = lambda key, default=None, user_id=None: values.get(key, default)
    return settings


def _png_bytes(size=(1200, 900)):
    buf = io.BytesIO()
    Image.new("RGB", size, "teal").save(buf, format="PNG")
    return buf.getvalue()


class ThumbnailRegenerationTestBase(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.storage_dir = Path(tempfile.mkdtemp())
        self.driver = LocalFileStorageDriver(str(self.storage_dir))
        self.file_repo = FileRepository()
        self.upload_repo = UploadRepository()
        self.user_id = self.create_test_user()

    def tearDown(self):
        shutil.rmtree(self.storage_dir, ignore_errors=True)
        super().tearDown()

    def _regeneration(self, profile):
        return ThumbnailRegeneration(
            settings=_settings_for(profile),
            storage_driver=self.driver,
            file_repository=self.file_repo,
            upload_repository=self.upload_repo,
        )

    def _run_to_completion(self, regeneration, timeout=20.0):
        job = regeneration.start()
        deadline = time.monotonic() + timeout
        while job.status in ("running", "cancelling") and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertNotIn(job.status, ("running", "cancelling"), "regeneration did not finish in time")
        return job

    def _add_image(self, name="0.png", base="generations/2026-01-01/gen1", **thumbnails):
        key = f"{base}/{name}"
        self.driver.put_bytes(key, _png_bytes())
        return self.file_repo.create(File(
            file_path=key,
            file_type="IMAGE",
            user_id=self.user_id,
            is_final=True,
            **thumbnails,
        ))

    def _add_upload(self, filename="pic.png", **thumbnails):
        self.driver.put_bytes(f"uploads/{filename}", _png_bytes())
        return self.upload_repo.create(Upload(
            user_id=self.user_id,
            filename=filename,
            media_type="image",
            **thumbnails,
        ))


class TestStaleAccounting(ThumbnailRegenerationTestBase):

    def test_rows_written_before_profiles_existed_are_stale(self):
        self._add_image()
        self._add_upload()

        self.assertEqual(self._regeneration(PROFILES["balanced"]).stale_count(), 2)

    def test_a_row_already_at_the_current_hash_is_not_stale(self):
        self._add_image(thumbnail_profile=profile_hash(PROFILES["balanced"]))

        self.assertEqual(self._regeneration(PROFILES["balanced"]).stale_count(), 0)

    def test_changing_the_profile_makes_a_matching_row_stale_again(self):
        self._add_image(thumbnail_profile=profile_hash(PROFILES["balanced"]))

        self.assertEqual(self._regeneration(PROFILES["compact"]).stale_count(), 1)

    def test_counts_report_images_videos_and_uploads_separately(self):
        self._add_image()
        self.file_repo.create(File(
            file_path="generations/2026-01-01/gen1/1.mp4",
            file_type="VIDEO",
            user_id=self.user_id,
            is_final=True,
        ))
        self._add_upload()

        counts = self._regeneration(PROFILES["balanced"]).counts()

        self.assertEqual(counts["images"], 2)
        self.assertEqual(counts["videos"], 1)
        self.assertEqual(counts["uploads"], 1)
        self.assertEqual(counts["stale"], 3)

    def test_a_non_final_file_is_never_regenerated(self):
        self.file_repo.create(File(
            file_path="generations/2026-01-01/gen1/preview.png",
            file_type="IMAGE",
            user_id=self.user_id,
            is_final=False,
        ))

        self.assertEqual(self._regeneration(PROFILES["balanced"]).stale_count(), 0)


class TestRegenerationRun(ThumbnailRegenerationTestBase):

    def test_an_image_row_is_rerendered_and_stamped_with_the_new_hash(self):
        record = self._add_image()
        regeneration = self._regeneration(PROFILES["balanced"])

        job = self._run_to_completion(regeneration)

        self.assertEqual(job.status, "done")
        self.assertEqual((job.total, job.done, job.failed), (1, 1, 0))
        stored = self.file_repo.get_by_id(record.id)
        self.assertEqual(stored.thumbnail_medium, "thumbnails/0_medium.webp")
        self.assertEqual(stored.thumbnail_profile, profile_hash(PROFILES["balanced"]))
        self.assertTrue((self.storage_dir / "generations/2026-01-01/gen1/thumbnails/0_medium.webp").exists())
        self.assertEqual(regeneration.stale_count(), 0)

    def test_sizes_the_new_profile_drops_are_deleted_from_disk(self):
        base = "generations/2026-01-01/gen1"
        for size in ("small", "medium", "large"):
            self.driver.put_bytes(f"{base}/thumbnails/0_{size}.webp", b"old")
        record = self._add_image(
            thumbnail_small="thumbnails/0_small.webp",
            thumbnail_medium="thumbnails/0_medium.webp",
            thumbnail_large="thumbnails/0_large.webp",
            thumbnail_profile=profile_hash(PROFILES["full"]),
        )

        self._run_to_completion(self._regeneration(PROFILES["compact"]))

        stored = self.file_repo.get_by_id(record.id)
        self.assertEqual(stored.thumbnail_small, "thumbnails/0_small.webp")
        self.assertIsNone(stored.thumbnail_medium)
        self.assertIsNone(stored.thumbnail_large)
        self.assertTrue((self.storage_dir / base / "thumbnails/0_small.webp").exists())
        self.assertFalse((self.storage_dir / base / "thumbnails/0_medium.webp").exists())
        self.assertFalse((self.storage_dir / base / "thumbnails/0_large.webp").exists())

    def test_a_dropped_video_size_takes_its_animated_encode_with_it(self):
        base = "generations/2026-01-01/gen1"
        self.driver.put_bytes(f"{base}/1.mp4", b"not really a video")
        for size in ("small", "medium"):
            self.driver.put_bytes(f"{base}/thumbnails/1_{size}.jpg", b"old")
            self.driver.put_bytes(f"{base}/thumbnails/1_{size}_animated.webp", b"old-animated")
        record = self.file_repo.create(File(
            file_path=f"{base}/1.mp4",
            file_type="VIDEO",
            user_id=self.user_id,
            is_final=True,
            thumbnail_small="thumbnails/1_small.jpg",
            thumbnail_medium="thumbnails/1_medium.jpg",
        ))

        def fake_video(video_path, driver, base_key, counter, profile):
            written = {}
            for size, _width in profile.widths():
                relative = f"thumbnails/{counter}_{size}.jpg"
                driver.put_bytes(f"{base_key}/{relative}", b"new")
                driver.put_bytes(f"{base_key}/thumbnails/{counter}_{size}_animated.webp", b"new-animated")
                written[size] = relative
            return written

        with patch(
            "src.features.media.thumbnail_regeneration.generate_video_thumbnails",
            side_effect=fake_video,
        ):
            self._run_to_completion(self._regeneration(PROFILES["compact"]))

        stored = self.file_repo.get_by_id(record.id)
        self.assertEqual(stored.thumbnail_small, "thumbnails/1_small.jpg")
        self.assertIsNone(stored.thumbnail_medium)
        self.assertTrue((self.storage_dir / base / "thumbnails/1_small_animated.webp").exists())
        self.assertFalse((self.storage_dir / base / "thumbnails/1_medium.jpg").exists())
        self.assertFalse((self.storage_dir / base / "thumbnails/1_medium_animated.webp").exists())

    def test_an_upload_row_is_rerendered_too(self):
        upload = self._add_upload()

        job = self._run_to_completion(self._regeneration(PROFILES["balanced"]))

        self.assertEqual((job.total, job.done), (1, 1))
        stored = self.upload_repo.get_by_id(upload.id, self.user_id)
        self.assertEqual(stored.thumbnail_medium, "thumbnails/pic_medium.webp")
        self.assertEqual(stored.thumbnail_profile, profile_hash(PROFILES["balanced"]))
        self.assertTrue((self.storage_dir / "uploads/thumbnails/pic_medium.webp").exists())

    def test_a_row_whose_source_is_gone_fails_and_stays_stale(self):
        record = self.file_repo.create(File(
            file_path="generations/2026-01-01/gen1/missing.png",
            file_type="IMAGE",
            user_id=self.user_id,
            is_final=True,
        ))
        regeneration = self._regeneration(PROFILES["balanced"])

        job = self._run_to_completion(regeneration)

        self.assertEqual(job.status, "done")
        self.assertEqual((job.done, job.failed), (0, 1))
        self.assertEqual(job.errors[0]["file_id"], record.id)
        self.assertIsNotNone(job.last_error)
        self.assertIsNone(self.file_repo.get_by_id(record.id).thumbnail_profile)
        self.assertEqual(regeneration.stale_count(), 1)

    def test_one_bad_row_does_not_stop_the_rest(self):
        self.file_repo.create(File(
            file_path="generations/2026-01-01/gen1/missing.png",
            file_type="IMAGE",
            user_id=self.user_id,
            is_final=True,
        ))
        good = self._add_image(name="2.png")

        job = self._run_to_completion(self._regeneration(PROFILES["balanced"]))

        self.assertEqual((job.total, job.done, job.failed), (2, 1, 1))
        self.assertIsNotNone(self.file_repo.get_by_id(good.id).thumbnail_profile)

    def test_only_the_last_twenty_errors_are_kept(self):
        for index in range(25):
            self.file_repo.create(File(
                file_path=f"generations/2026-01-01/gen1/missing{index}.png",
                file_type="IMAGE",
                user_id=self.user_id,
                is_final=True,
            ))

        job = self._run_to_completion(self._regeneration(PROFILES["balanced"]))

        self.assertEqual(job.failed, 25)
        self.assertEqual(len(job.errors), 20)

    def test_a_second_start_while_one_runs_is_refused(self):
        self._add_image()
        regeneration = self._regeneration(PROFILES["balanced"])
        gate = threading.Event()

        original = ThumbnailRegeneration._regenerate_file

        def slow(self, record, profile, target):
            gate.wait(5)
            return original(self, record, profile, target)

        with patch.object(ThumbnailRegeneration, "_regenerate_file", slow):
            regeneration.start()
            with self.assertRaises(ThumbnailJobRunning):
                regeneration.start()
            gate.set()

        deadline = time.monotonic() + 10
        while regeneration.current().status in ("running", "cancelling") and time.monotonic() < deadline:
            time.sleep(0.02)

    def test_cancel_stops_after_the_current_file(self):
        for index in range(6):
            self._add_image(name=f"{index}.png")
        regeneration = self._regeneration(PROFILES["balanced"])
        started = threading.Event()
        release = threading.Event()

        original = ThumbnailRegeneration._regenerate_file

        def gated(self, record, profile, target):
            result = original(self, record, profile, target)
            started.set()
            release.wait(5)
            return result

        with patch.object(ThumbnailRegeneration, "_regenerate_file", gated):
            job = regeneration.start()
            self.assertTrue(started.wait(10))
            regeneration.cancel()
            self.assertEqual(job.status, "cancelling")
            release.set()

            deadline = time.monotonic() + 10
            while job.status == "cancelling" and time.monotonic() < deadline:
                time.sleep(0.02)

        self.assertEqual(job.status, "cancelled")
        self.assertLess(job.done, job.total)
        self.assertGreater(regeneration.stale_count(), 0)

    def test_cancel_without_a_running_job_reports_nothing(self):
        self.assertIsNone(self._regeneration(PROFILES["balanced"]).cancel())

    def test_no_job_has_run_yet(self):
        self.assertIsNone(self._regeneration(PROFILES["balanced"]).current())


class TestUsageMeasurement(ThumbnailRegenerationTestBase):

    def test_static_and_animated_bytes_are_counted_separately(self):
        base = "generations/2026-01-01/gen1"
        self.driver.put_bytes(f"{base}/thumbnails/0_medium.webp", b"x" * 100)
        self.driver.put_bytes(f"{base}/thumbnails/0_medium_animated.webp", b"y" * 900)
        self.driver.put_bytes("uploads/thumbnails/pic_medium.webp", b"z" * 50)
        # Not a thumbnail - the original output must not be counted.
        self.driver.put_bytes(f"{base}/0.png", b"q" * 5000)

        usage = self._regeneration(PROFILES["balanced"]).usage()

        self.assertEqual(usage["static_bytes"], 150)
        self.assertEqual(usage["animated_bytes"], 900)
        self.assertEqual(usage["total_bytes"], 1050)
        self.assertIsNotNone(usage["measured_at"])

    def test_a_measurement_is_cached_rather_than_rewalked(self):
        regeneration = self._regeneration(PROFILES["balanced"])
        self.driver.put_bytes("generations/a/thumbnails/0_medium.webp", b"x" * 10)

        first = regeneration.usage()
        self.driver.put_bytes("generations/a/thumbnails/0_large.webp", b"x" * 10)

        self.assertEqual(regeneration.usage()["total_bytes"], first["total_bytes"])

    def test_a_driver_with_no_local_files_reports_nothing(self):
        remote = Mock()
        remote.local_path.return_value = None
        regeneration = self._regeneration(PROFILES["balanced"])
        regeneration.storage_driver = remote

        self.assertIsNone(regeneration.usage())


if __name__ == "__main__":
    unittest.main()
