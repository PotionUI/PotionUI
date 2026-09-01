"""Tests for UploadRepository (migration 087).

Exercised against the real migrated schema (PersistenceTestBase) rather than
mocks, since the whole point of this repository is the ownership scoping
baked into every query - a mock would just echo back whatever the test told
it to return.
"""

import unittest

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository


class TestUploadRepository(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = UploadRepository()
        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def _upload(self, **overrides) -> Upload:
        defaults = dict(
            user_id=self.user_id,
            filename="abc123.png",
            media_type="image",
            original_filename="cat.png",
            mime_type="image/png",
            width=800,
            height=600,
            file_size=2048,
        )
        defaults.update(overrides)
        return Upload(**defaults)

    def test_create_returns_persisted_row(self):
        created = self.repo.create(self._upload())

        self.assertIsNotNone(created)
        self.assertIsNotNone(created.id)
        self.assertEqual(created.user_id, self.user_id)
        self.assertEqual(created.filename, "abc123.png")
        self.assertEqual(created.original_filename, "cat.png")
        self.assertEqual(created.media_type, "image")
        self.assertEqual(created.mime_type, "image/png")
        self.assertEqual(created.width, 800)
        self.assertEqual(created.height, 600)
        self.assertEqual(created.file_size, 2048)
        self.assertIsNotNone(created.created_at)

    def test_get_by_filename_scoped_to_owner(self):
        self.repo.create(self._upload(filename="mine.png"))

        found = self.repo.get_by_filename("mine.png", self.user_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.filename, "mine.png")

        # A different user asking for the same filename gets nothing back -
        # not an error, not a peek at whose it is.
        not_found = self.repo.get_by_filename("mine.png", self.other_user_id)
        self.assertIsNone(not_found)

    def test_get_by_filename_nonexistent(self):
        self.assertIsNone(self.repo.get_by_filename("does-not-exist.png", self.user_id))

    def test_list_for_user_only_returns_own_uploads(self):
        self.repo.create(self._upload(filename="mine1.png"))
        self.repo.create(self._upload(filename="mine2.png"))
        self.repo.create(self._upload(filename="theirs.png", user_id=self.other_user_id))

        mine = self.repo.list_for_user(self.user_id)
        filenames = {u.filename for u in mine}

        self.assertEqual(len(mine), 2)
        self.assertEqual(filenames, {"mine1.png", "mine2.png"})

    def test_list_for_user_newest_first(self):
        first = self.repo.create(self._upload(filename="first.png"))
        second = self.repo.create(self._upload(filename="second.png"))

        results = self.repo.list_for_user(self.user_id)

        self.assertEqual([u.id for u in results], [second.id, first.id])

    def test_list_for_user_filters_by_media_type(self):
        self.repo.create(self._upload(filename="pic.png", media_type="image"))
        self.repo.create(self._upload(filename="clip.mp4", media_type="video"))

        images = self.repo.list_for_user(self.user_id, media_type="image")
        videos = self.repo.list_for_user(self.user_id, media_type="video")

        self.assertEqual([u.filename for u in images], ["pic.png"])
        self.assertEqual([u.filename for u in videos], ["clip.mp4"])

    def test_list_for_user_pagination(self):
        for i in range(5):
            self.repo.create(self._upload(filename=f"file_{i}.png"))

        page1 = self.repo.list_for_user(self.user_id, limit=2, offset=0)
        page2 = self.repo.list_for_user(self.user_id, limit=2, offset=2)

        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        self.assertNotEqual({u.id for u in page1}, {u.id for u in page2})

    def test_count_for_user(self):
        self.repo.create(self._upload(filename="a.png", media_type="image"))
        self.repo.create(self._upload(filename="b.mp4", media_type="video"))
        self.repo.create(self._upload(filename="c.png", media_type="image", user_id=self.other_user_id))

        self.assertEqual(self.repo.count_for_user(self.user_id), 2)
        self.assertEqual(self.repo.count_for_user(self.user_id, media_type="image"), 1)
        self.assertEqual(self.repo.count_for_user(self.other_user_id), 1)

    def test_delete_scoped_to_owner(self):
        self.repo.create(self._upload(filename="mine.png"))

        # The owner of a *different* filename can't delete this one.
        deleted_by_other = self.repo.delete("mine.png", self.other_user_id)
        self.assertFalse(deleted_by_other)
        self.assertIsNotNone(self.repo.get_by_filename("mine.png", self.user_id))

        deleted_by_owner = self.repo.delete("mine.png", self.user_id)
        self.assertTrue(deleted_by_owner)
        self.assertIsNone(self.repo.get_by_filename("mine.png", self.user_id))

    def test_delete_nonexistent(self):
        self.assertFalse(self.repo.delete("nonexistent.png", self.user_id))

    def test_create_persists_thumbnail_paths(self):
        created = self.repo.create(self._upload(
            filename="thumbed.png",
            thumbnail_small="thumbnails/x_small.webp",
            thumbnail_medium="thumbnails/x_medium.webp",
            thumbnail_large="thumbnails/x_large.webp",
        ))

        self.assertEqual(created.thumbnail_small, "thumbnails/x_small.webp")
        self.assertEqual(created.thumbnail_medium, "thumbnails/x_medium.webp")
        self.assertEqual(created.thumbnail_large, "thumbnails/x_large.webp")

    def test_create_without_thumbnails_leaves_columns_null(self):
        created = self.repo.create(self._upload(filename="untouched.png"))

        self.assertIsNone(created.thumbnail_small)
        self.assertIsNone(created.thumbnail_medium)
        self.assertIsNone(created.thumbnail_large)

    def test_set_thumbnail_paths_updates_row(self):
        created = self.repo.create(self._upload(filename="clip.mp4", media_type="video"))
        self.assertIsNone(created.thumbnail_small)

        updated = self.repo.set_thumbnail_paths(
            created.id, "thumbnails/x_small.webp", "thumbnails/x_medium.webp", "thumbnails/x_large.webp"
        )
        self.assertTrue(updated)

        found = self.repo.get_by_id(created.id, self.user_id)
        self.assertEqual(found.thumbnail_small, "thumbnails/x_small.webp")
        self.assertEqual(found.thumbnail_medium, "thumbnails/x_medium.webp")
        self.assertEqual(found.thumbnail_large, "thumbnails/x_large.webp")

    def test_set_thumbnail_paths_nonexistent_id(self):
        self.assertFalse(self.repo.set_thumbnail_paths("does-not-exist", "a", "b", "c"))

    def test_get_by_filename_unscoped_ignores_owner(self):
        self.repo.create(self._upload(filename="anyones.png", user_id=self.other_user_id))

        # Unlike `get_by_filename`, no owner is required - this backs the
        # already-unauthenticated `/api/media/uploads/{filename}` route.
        found = self.repo.get_by_filename_unscoped("anyones.png")
        self.assertIsNotNone(found)
        self.assertEqual(found.filename, "anyones.png")

    def test_get_by_filename_unscoped_nonexistent(self):
        self.assertIsNone(self.repo.get_by_filename_unscoped("does-not-exist.png"))


if __name__ == '__main__':
    unittest.main()
