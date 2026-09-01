"""Tags of type UPLOAD and the `upload_tags` junction (migration 115).

UPLOAD joins MODEL and GENERATION in the shared `tags` table rather than
getting a vocabulary of its own, so what needs proving is that the new type is
user-scoped like GENERATION (not global like MODEL), that its counts are
reported against the right junction, and that the bulk read the library list
path depends on really is one query.
"""

import unittest
from unittest.mock import patch

from tests.fixtures.persistence_base import PersistenceTestBase
from tests.fixtures.query_counter import CountingDb
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.features.tags.repository import TagRepository



class TestUploadTags(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = TagRepository()
        self.uploads = UploadRepository()
        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def _upload(self, filename="a.png", user_id=None) -> Upload:
        return self.uploads.create(Upload(
            user_id=user_id or self.user_id,
            filename=filename,
            original_filename=filename,
            media_type="image",
            mime_type="image/png",
            file_size=10,
        ))

    def test_upload_tags_are_scoped_to_their_owner(self):
        mine = self.repo.create_tag("cats", type="UPLOAD", user_id=self.user_id)
        theirs = self.repo.create_tag("cats", type="UPLOAD", user_id=self.other_user_id)

        # Same name, same type, different owner - two distinct tags, not a clash.
        self.assertNotEqual(mine.id, theirs.id)
        self.assertEqual(
            [t.id for t in self.repo.get_all_tags(type="UPLOAD", user_id=self.user_id)],
            [mine.id]
        )

    def test_an_upload_tag_and_a_generation_tag_can_share_a_name(self):
        upload_tag = self.repo.create_tag("cats", type="UPLOAD", user_id=self.user_id)
        generation_tag = self.repo.create_tag("cats", type="GENERATION", user_id=self.user_id)

        self.assertNotEqual(upload_tag.id, generation_tag.id)

    def test_set_upload_tags_replaces(self):
        upload = self._upload()
        first = self.repo.create_tag("first", type="UPLOAD", user_id=self.user_id)
        second = self.repo.create_tag("second", type="UPLOAD", user_id=self.user_id)

        self.repo.set_upload_tags(upload.id, [first.id])
        self.repo.set_upload_tags(upload.id, [second.id])

        self.assertEqual([t.name for t in self.repo.get_upload_tags(upload.id)], ["second"])

    def test_usage_count_reads_the_upload_junction(self):
        upload = self._upload()
        tag = self.repo.create_tag("cats", type="UPLOAD", user_id=self.user_id)
        self.repo.set_upload_tags(upload.id, [tag.id])

        counts = self.repo.get_tags_with_counts(type="UPLOAD", user_id=self.user_id)

        self.assertEqual([(t.name, t.usage_count) for t in counts], [("cats", 1)])

    def test_bulk_read_covers_every_id_and_costs_one_query(self):
        tagged = self._upload("tagged.png")
        untagged = self._upload("untagged.png")
        tag = self.repo.create_tag("cats", type="UPLOAD", user_id=self.user_id)
        self.repo.set_upload_tags(tagged.id, [tag.id])

        counting = CountingDb(self.db)
        with patch("src.platform.database.database.db", counting):
            result = self.repo.get_upload_tags_bulk([tagged.id, untagged.id])

        self.assertEqual([t.name for t in result[tagged.id]], ["cats"])
        self.assertEqual(result[untagged.id], [])
        self.assertEqual(
            len(counting.statements), 1,
            f"expected one query, got: {counting.statements}"
        )

    def test_deleting_a_tag_drops_its_upload_associations(self):
        upload = self._upload()
        tag = self.repo.create_tag("cats", type="UPLOAD", user_id=self.user_id)
        self.repo.set_upload_tags(upload.id, [tag.id])

        self.repo.delete_tag(tag.id)

        self.assertEqual(self.repo.get_upload_tags(upload.id), [])


if __name__ == '__main__':
    unittest.main()
