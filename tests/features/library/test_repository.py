"""Tests for LibraryRepository (the filtered read side of `uploads`).

Driven against the real migrated schema (PersistenceTestBase), never a mock:
what is under test here is the SQL - the owner filter baked into every query,
the ALL-of-these-tags semantics, and the fact that a page costs a fixed number
of statements. A configured Mock would answer whatever the test told it to.
"""

import unittest

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.library.repository import LibraryRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
import src.features.library.repository as library_repository_module
import src.features.media.upload_repository as upload_repository_module
import src.features.tags.repository as tag_repository_module


class LibraryRepositoryTestBase(PersistenceTestBase):
    """Scratch DB with the three modules this feature reads through redirected."""

    def setUp(self):
        super().setUp()
        # These modules bind `db` at import time (`from ... import db`), so each
        # has to be pointed at the temp database by name.
        library_repository_module.db = self.db
        upload_repository_module.db = self.db
        tag_repository_module.db = self.db

        self.repo = LibraryRepository()
        self.uploads = UploadRepository()
        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def _upload(self, filename="a.png", user_id=None, media_type="image", original_filename="cat.png"):
        return self.uploads.create(Upload(
            user_id=user_id or self.user_id,
            filename=filename,
            original_filename=original_filename,
            media_type=media_type,
            mime_type="image/png",
            file_size=1024,
        ))

    def _tag(self, name, user_id=None, tag_type="UPLOAD"):
        from src.features.tags.repository import TagRepository
        return TagRepository().create_tag(name, type=tag_type, user_id=user_id or self.user_id)

    def _tag_upload(self, upload_id, tag_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO upload_tags (upload_id, tag_id) VALUES (?, ?)",
                (upload_id, tag_id)
            )

    def _collection(self, name="Folder", user_id=None):
        from src.platform.util.ids import generate_ulid
        collection_id = generate_ulid()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO collections (id, name, user_id) VALUES (?, ?, ?)",
                (collection_id, name, user_id or self.user_id)
            )
        return collection_id

    def _add_to_collection(self, collection_id, upload_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO collection_uploads (collection_id, upload_id) VALUES (?, ?)",
                (collection_id, upload_id)
            )


class TestLibraryRepositoryScoping(LibraryRepositoryTestBase):

    def test_list_only_returns_own_items(self):
        self._upload(filename="mine.png")
        self._upload(filename="theirs.png", user_id=self.other_user_id)

        mine = self.repo.list_items(self.user_id)

        self.assertEqual([u.filename for u in mine], ["mine.png"])
        self.assertEqual(self.repo.count_items(self.user_id), 1)
        self.assertEqual(self.repo.count_items(self.other_user_id), 1)

    def test_list_newest_first(self):
        first = self._upload(filename="first.png")
        second = self._upload(filename="second.png")

        self.assertEqual([u.id for u in self.repo.list_items(self.user_id)], [second.id, first.id])

    def test_media_type_filter(self):
        self._upload(filename="pic.png", media_type="image")
        self._upload(filename="clip.mp4", media_type="video")

        self.assertEqual([u.filename for u in self.repo.list_items(self.user_id, media_type="video")], ["clip.mp4"])
        self.assertEqual(self.repo.count_items(self.user_id, media_type="video"), 1)

    def test_search_matches_original_filename_case_insensitively(self):
        self._upload(filename="a.png", original_filename="Sunset Beach.png")
        self._upload(filename="b.png", original_filename="portrait.png")

        found = self.repo.list_items(self.user_id, search="sunset")

        self.assertEqual([u.filename for u in found], ["a.png"])
        self.assertEqual(self.repo.count_items(self.user_id, search="sunset"), 1)

    def test_pagination(self):
        for i in range(5):
            self._upload(filename=f"f{i}.png")

        page1 = self.repo.list_items(self.user_id, limit=2, offset=0)
        page2 = self.repo.list_items(self.user_id, limit=2, offset=2)

        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        self.assertFalse({u.id for u in page1} & {u.id for u in page2})
        # The count ignores the page window.
        self.assertEqual(self.repo.count_items(self.user_id), 5)

    def test_collection_filter(self):
        inside = self._upload(filename="in.png")
        self._upload(filename="out.png")
        collection_id = self._collection()
        self._add_to_collection(collection_id, inside.id)

        found = self.repo.list_items(self.user_id, collection_id=collection_id)

        self.assertEqual([u.filename for u in found], ["in.png"])
        self.assertEqual(self.repo.count_items(self.user_id, collection_id=collection_id), 1)

    def test_tag_filter_requires_all_tags(self):
        both = self._upload(filename="both.png")
        one = self._upload(filename="one.png")
        cats = self._tag("cats")
        dogs = self._tag("dogs")
        self._tag_upload(both.id, cats.id)
        self._tag_upload(both.id, dogs.id)
        self._tag_upload(one.id, cats.id)

        either = self.repo.list_items(self.user_id, tag_ids=[cats.id])
        intersection = self.repo.list_items(self.user_id, tag_ids=[cats.id, dogs.id])

        self.assertEqual({u.filename for u in either}, {"both.png", "one.png"})
        self.assertEqual([u.filename for u in intersection], ["both.png"])
        self.assertEqual(self.repo.count_items(self.user_id, tag_ids=[cats.id, dogs.id]), 1)

    def test_filters_combine(self):
        target = self._upload(filename="target.mp4", media_type="video", original_filename="holiday clip.mp4")
        self._upload(filename="decoy.mp4", media_type="video", original_filename="holiday clip.mp4")
        tag = self._tag("keep")
        self._tag_upload(target.id, tag.id)

        found = self.repo.list_items(
            self.user_id, media_type="video", search="holiday", tag_ids=[tag.id]
        )

        self.assertEqual([u.filename for u in found], ["target.mp4"])

    def test_media_type_counts(self):
        self._upload(filename="a.png", media_type="image")
        self._upload(filename="b.png", media_type="image")
        self._upload(filename="c.mp4", media_type="video")
        self._upload(filename="theirs.png", media_type="image", user_id=self.other_user_id)

        self.assertEqual(self.repo.media_type_counts(self.user_id), {"image": 2, "video": 1})


if __name__ == '__main__':
    unittest.main()
