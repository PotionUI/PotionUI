"""Serving falls back across sizes.

A row only carries the sizes the profile in force when it was written
rendered, so after a profile change the gallery asks for sizes that were
never produced. Those requests must resolve to the nearest size that exists,
not fail.
"""

import unittest
from pathlib import Path
from unittest.mock import Mock

from src.features.media.file_resolver import FilePathResolver
from src.features.media.records import Upload
from src.platform.settings.settings import Settings


def _record(file_type="IMAGE", small=None, medium=None, large=None):
    record = Mock()
    record.file_type = file_type
    record.thumbnail_small = small
    record.thumbnail_medium = medium
    record.thumbnail_large = large
    return record


class TestFileResolverFallback(unittest.TestCase):

    def setUp(self):
        settings = Mock(spec=Settings)
        settings.get_file_storage_directory.return_value = "/tmp/storage"
        self.resolver = FilePathResolver(settings, Mock())

    def test_the_requested_size_wins_when_it_exists(self):
        record = _record(small="thumbnails/0_small.webp", medium="thumbnails/0_medium.webp")

        self.assertEqual(
            self.resolver.get_thumbnail_path(record, "small"),
            Path("thumbnails/0_small.webp"),
        )

    def test_small_falls_back_to_medium_before_large(self):
        record = _record(medium="thumbnails/0_medium.webp", large="thumbnails/0_large.webp")

        self.assertEqual(
            self.resolver.get_thumbnail_path(record, "small"),
            Path("thumbnails/0_medium.webp"),
        )

    def test_small_falls_back_to_large_when_medium_is_absent(self):
        record = _record(large="thumbnails/0_large.webp")

        self.assertEqual(
            self.resolver.get_thumbnail_path(record, "small"),
            Path("thumbnails/0_large.webp"),
        )

    def test_medium_falls_back_to_large_before_small(self):
        record = _record(small="thumbnails/0_small.webp", large="thumbnails/0_large.webp")

        self.assertEqual(
            self.resolver.get_thumbnail_path(record, "medium"),
            Path("thumbnails/0_large.webp"),
        )

    def test_large_falls_back_to_medium_before_small(self):
        record = _record(small="thumbnails/0_small.webp", medium="thumbnails/0_medium.webp")

        self.assertEqual(
            self.resolver.get_thumbnail_path(record, "large"),
            Path("thumbnails/0_medium.webp"),
        )

    def test_a_row_with_no_thumbnails_at_all_still_resolves_to_nothing(self):
        self.assertIsNone(self.resolver.get_thumbnail_path(_record(), "medium"))

    def test_an_unknown_size_resolves_to_nothing(self):
        record = _record(medium="thumbnails/0_medium.webp")

        self.assertIsNone(self.resolver.get_thumbnail_path(record, "enormous"))

    def test_an_animated_request_returns_the_animated_sibling(self):
        record = _record(file_type="VIDEO", medium="thumbnails/1_medium.jpg")

        self.assertEqual(
            self.resolver.get_thumbnail_path(record, "medium", animated=True),
            Path("thumbnails/1_medium_animated.webp"),
        )

    def test_an_animated_request_falls_back_to_the_static_frame_when_no_encode_exists(self):
        record = _record(file_type="VIDEO", medium="thumbnails/1_medium.jpg")

        result = self.resolver.get_thumbnail_path(
            record, "medium", animated=True, animated_exists=lambda _path: False
        )

        self.assertEqual(result, Path("thumbnails/1_medium.jpg"))

    def test_an_animated_request_for_an_image_is_never_animated(self):
        record = _record(file_type="IMAGE", medium="thumbnails/0_medium.webp")

        self.assertEqual(
            self.resolver.get_thumbnail_path(record, "medium", animated=True),
            Path("thumbnails/0_medium.webp"),
        )

    def test_the_animated_existence_check_is_asked_about_the_resolved_size(self):
        record = _record(file_type="VIDEO", large="thumbnails/1_large.jpg")
        asked = []

        self.resolver.get_thumbnail_path(
            record, "small", animated=True,
            animated_exists=lambda path: asked.append(path) or True,
        )

        self.assertEqual(asked, [Path("thumbnails/1_large_animated.webp")])


class _Driver:
    def __init__(self, present=()):
        self.present = set(present)

    def exists(self, key):
        return key in self.present


class TestUploadThumbnailFallback(unittest.TestCase):
    """`MediaStore._resolve_upload_thumbnail_key` resolves the same way for the
    uploads table, which has no `File` record to share the resolver with."""

    def _store(self, upload, driver):
        from src.features.media.store import MediaStore

        store = MediaStore.__new__(MediaStore)
        store.upload_repo = Mock()
        store.upload_repo.get_by_filename_unscoped.return_value = upload
        store.storage_driver = driver
        return store

    @staticmethod
    def _upload(media_type="image", small=None, medium=None, large=None):
        return Upload(
            user_id="u1",
            filename="pic.png",
            media_type=media_type,
            thumbnail_small=small,
            thumbnail_medium=medium,
            thumbnail_large=large,
        )

    def test_a_missing_size_falls_back_to_the_nearest_present_one(self):
        store = self._store(self._upload(large="thumbnails/pic_large.webp"), _Driver())

        key = store._resolve_upload_thumbnail_key("pic.png", "small", animated=False)

        self.assertEqual(key, "uploads/thumbnails/pic_large.webp")

    def test_medium_prefers_large_over_small(self):
        store = self._store(
            self._upload(small="thumbnails/pic_small.webp", large="thumbnails/pic_large.webp"),
            _Driver(),
        )

        key = store._resolve_upload_thumbnail_key("pic.png", "medium", animated=False)

        self.assertEqual(key, "uploads/thumbnails/pic_large.webp")

    def test_an_animated_request_uses_the_encode_when_it_is_on_disk(self):
        upload = self._upload(media_type="video", medium="thumbnails/clip_medium.jpg")
        store = self._store(upload, _Driver({"uploads/thumbnails/clip_medium_animated.webp"}))

        key = store._resolve_upload_thumbnail_key("pic.png", "medium", animated=True)

        self.assertEqual(key, "uploads/thumbnails/clip_medium_animated.webp")

    def test_an_animated_request_falls_back_to_the_static_frame(self):
        upload = self._upload(media_type="video", medium="thumbnails/clip_medium.jpg")
        store = self._store(upload, _Driver())

        key = store._resolve_upload_thumbnail_key("pic.png", "medium", animated=True)

        self.assertEqual(key, "uploads/thumbnails/clip_medium.jpg")

    def test_an_upload_with_no_thumbnails_resolves_to_nothing(self):
        store = self._store(self._upload(), _Driver())

        self.assertIsNone(store._resolve_upload_thumbnail_key("pic.png", "small", animated=False))

    def test_an_unknown_upload_resolves_to_nothing(self):
        store = self._store(None, _Driver())

        self.assertIsNone(store._resolve_upload_thumbnail_key("gone.png", "small", animated=False))


if __name__ == "__main__":
    unittest.main()
