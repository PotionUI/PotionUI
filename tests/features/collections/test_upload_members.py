"""Tests for collections holding library uploads.

Uploads live in the 'library' scope tree; generations live in the 'history'
scope tree (migration 137) - the assertions that matter are that a Library
collection's `item_count` still comes from the `collection_uploads` junction
and that a caller cannot file an upload they do not own, or into the wrong
scope's tree.
"""

import unittest

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.collections.repository import CollectionRepository
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.platform.util.ids import generate_ulid

import src.features.media.upload_repository as upload_repository_module

HISTORY = "history"
LIBRARY = "library"


class TestCollectionUploadMembers(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        upload_repository_module.db = self.db

        self.repo = CollectionRepository()
        self.gen_repo = GenerationRepository()
        self.uploads = UploadRepository()
        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def _generation(self, user_id=None) -> str:
        generation = Generation(
            id=generate_ulid(),
            preset_id="test_preset",
            form_data={"prompt": "test"},
            user_id=user_id or self.user_id,
            status="completed",
            preset_version="1.0",
        )
        self.gen_repo.create(generation)
        return generation.id

    def _upload(self, filename="a.png", user_id=None) -> Upload:
        return self.uploads.create(Upload(
            user_id=user_id or self.user_id,
            filename=filename,
            original_filename=filename,
            media_type="image",
            mime_type="image/png",
            file_size=10,
        ))

    def test_add_and_list_upload_members(self):
        collection = self.repo.create("Folder", self.user_id, LIBRARY)
        upload = self._upload()

        added = self.repo.add_upload_members(collection.id, [upload.id], self.user_id, LIBRARY)

        self.assertEqual(added, 1)
        self.assertEqual([c.id for c in self.repo.get_for_upload(upload.id)], [collection.id])

    def test_add_is_idempotent(self):
        collection = self.repo.create("Folder", self.user_id, LIBRARY)
        upload = self._upload()

        self.repo.add_upload_members(collection.id, [upload.id], self.user_id, LIBRARY)
        second = self.repo.add_upload_members(collection.id, [upload.id], self.user_id, LIBRARY)

        self.assertEqual(second, 0)
        self.assertEqual(self.repo.get_by_id(collection.id, LIBRARY).item_count, 1)

    def test_add_refuses_an_upload_owned_by_someone_else(self):
        collection = self.repo.create("Folder", self.user_id, LIBRARY)
        theirs = self._upload(filename="theirs.png", user_id=self.other_user_id)

        added = self.repo.add_upload_members(collection.id, [theirs.id], self.user_id, LIBRARY)

        self.assertEqual(added, 0)
        self.assertEqual(self.repo.get_for_upload(theirs.id), [])

    def test_add_refuses_a_collection_owned_by_someone_else(self):
        theirs = self.repo.create("Their Folder", self.other_user_id, LIBRARY)
        mine = self._upload()

        added = self.repo.add_upload_members(theirs.id, [mine.id], self.user_id, LIBRARY)

        self.assertEqual(added, 0)
        self.assertEqual(self.repo.get_for_upload(mine.id), [])

    def test_add_refuses_wrong_scope(self):
        """A History-scope collection cannot accept library uploads."""
        history_folder = self.repo.create("History Folder", self.user_id, HISTORY)
        upload = self._upload()

        added = self.repo.add_upload_members(history_folder.id, [upload.id], self.user_id, LIBRARY)

        self.assertEqual(added, 0)
        self.assertEqual(self.repo.get_for_upload(upload.id), [])

    def test_remove_upload_members(self):
        collection = self.repo.create("Folder", self.user_id, LIBRARY)
        upload = self._upload()
        self.repo.add_upload_members(collection.id, [upload.id], self.user_id, LIBRARY)

        removed = self.repo.remove_upload_members(collection.id, [upload.id])

        self.assertEqual(removed, 1)
        self.assertEqual(self.repo.get_by_id(collection.id, LIBRARY).item_count, 0)

    def test_item_count_of_uploads_only(self):
        collection = self.repo.create("Folder", self.user_id, LIBRARY)
        self.repo.add_upload_members(
            collection.id,
            [self._upload("a.png").id, self._upload("b.png").id, self._upload("c.png").id],
            self.user_id,
            LIBRARY,
        )

        self.assertEqual(self.repo.get_by_id(collection.id, LIBRARY).item_count, 3)
        listed = {c.id: c for c in self.repo.list(self.user_id, LIBRARY)}
        self.assertEqual(listed[collection.id].item_count, 3)

    def test_item_count_of_generations_only_is_unchanged(self):
        collection = self.repo.create("Folder", self.user_id, HISTORY)
        self.repo.add_members(collection.id, [self._generation(), self._generation()], self.user_id, HISTORY)

        self.assertEqual(self.repo.get_by_id(collection.id, HISTORY).item_count, 2)
        self.assertEqual(self.repo.list(self.user_id, HISTORY)[0].item_count, 2)

    def test_deleting_the_collection_drops_upload_memberships(self):
        collection = self.repo.create("Folder", self.user_id, LIBRARY)
        upload = self._upload()
        self.repo.add_upload_members(collection.id, [upload.id], self.user_id, LIBRARY)

        self.repo.delete(collection.id, self.user_id, LIBRARY)

        self.assertEqual(self.repo.get_for_upload(upload.id), [])
        # The upload itself is untouched - a folder is a grouping, not an owner.
        self.assertIsNotNone(self.uploads.get_by_id(upload.id, self.user_id))


if __name__ == '__main__':
    unittest.main()
