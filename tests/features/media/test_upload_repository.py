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

    def _replace_args(self, created, **overrides):
        values = dict(
            upload_id=created.id,
            user_id=self.user_id,
            expected_filename=created.filename,
            filename="replacement.png",
            mime_type="image/png",
            width=10,
            height=20,
            duration_seconds=None,
            fps=None,
            file_size=99,
            content_hash="newhash",
            thumbnail_small="thumbnails/r_small.webp",
            thumbnail_medium=None,
            thumbnail_large=None,
            thumbnail_profile="profile-2",
        )
        values.update(overrides)
        return values

    def test_replace_file_rewrites_file_hash_and_thumbnails_together_keeping_the_row_identity(self):
        created = self.repo.create(self._upload(
            thumbnail_small="thumbnails/old_small.webp",
            thumbnail_medium="thumbnails/old_medium.webp",
            thumbnail_profile="profile-1",
            content_hash="oldhash",
        ))

        updated = self.repo.replace_file(**self._replace_args(created))

        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.filename, "replacement.png")
        self.assertEqual((updated.width, updated.height, updated.file_size), (10, 20, 99))
        self.assertEqual(updated.content_hash, "newhash")
        self.assertEqual(updated.thumbnail_small, "thumbnails/r_small.webp")
        self.assertIsNone(updated.thumbnail_medium)
        self.assertEqual(updated.thumbnail_profile, "profile-2")
        self.assertEqual(updated.original_filename, "cat.png")
        self.assertEqual(updated.media_type, "image")

    def test_replace_file_renames_the_item_when_a_display_name_is_given(self):
        created = self.repo.create(self._upload())

        updated = self.repo.replace_file(**self._replace_args(created, original_filename="cat.webp"))

        self.assertEqual(updated.original_filename, "cat.webp")
        self.assertEqual(self.repo.get_by_id(created.id, self.user_id).original_filename, "cat.webp")

    def test_replace_file_refuses_when_the_filename_is_no_longer_the_expected_one(self):
        created = self.repo.create(self._upload(content_hash="oldhash"))

        result = self.repo.replace_file(**self._replace_args(created, expected_filename="someone-else.png"))

        self.assertIsNone(result)
        untouched = self.repo.get_by_id(created.id, self.user_id)
        self.assertEqual(untouched.filename, created.filename)
        self.assertEqual(untouched.content_hash, "oldhash")
        self.assertEqual(untouched.width, 800)

    def test_replace_file_is_scoped_to_the_owner(self):
        created = self.repo.create(self._upload())

        result = self.repo.replace_file(**self._replace_args(created, user_id=self.other_user_id))

        self.assertIsNone(result)
        self.assertEqual(self.repo.get_by_id(created.id, self.user_id).filename, created.filename)

    def test_replace_file_with_a_second_stale_writer_only_lets_the_first_win(self):
        created = self.repo.create(self._upload())

        first = self.repo.replace_file(**self._replace_args(created, filename="first.png"))
        second = self.repo.replace_file(**self._replace_args(created, filename="second.png"))

        self.assertEqual(first.filename, "first.png")
        self.assertIsNone(second)
        self.assertEqual(self.repo.get_by_id(created.id, self.user_id).filename, "first.png")

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
