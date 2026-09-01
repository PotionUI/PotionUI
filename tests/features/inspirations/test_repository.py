"""Tests for InspirationRepository - the feed's read side.

Driven against the real migrated schema (PersistenceTestBase), never a mock:
what is under test is the SQL - the query/author_id/collection_id/saved
filters, and the comment_count/save_count/saved_by_me projection every feed
card depends on. A configured Mock would answer whatever the test told it to.
"""

import unittest

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.inspirations.dto import inspiration_to_dto
from src.features.inspirations.records import Inspiration
from src.features.inspirations.repository import InspirationRepository


class InspirationRepositoryTestBase(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = InspirationRepository()
        self.user_id = self.create_test_user()
        self.other_user_id = self.create_test_user("other_user", "other", "other@example.com")

    def _inspiration(self, user_id=None, title="Untitled", description=None, created_at=None):
        insp = self.repo.create(Inspiration(
            id="",
            user_id=user_id or self.user_id,
            title=title,
            description=description,
            media=[{"filename": "a.png", "type": "image", "width": 512, "height": 512}],
            params_snapshot={"form_data": {}, "preview": []},
        ))
        if created_at:
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE inspirations SET created_at = ? WHERE id = ?", (created_at, insp.id)
                )
        return insp


class TestFeedOrderingAndCounts(InspirationRepositoryTestBase):

    def test_newest_first(self):
        first = self._inspiration(title="First", created_at="2026-01-01 00:00:00")
        second = self._inspiration(title="Second", created_at="2026-01-02 00:00:00")

        items, total = self.repo.list_feed(self.user_id)

        self.assertEqual(total, 2)
        self.assertEqual([i.id for i in items], [second.id, first.id])

    def test_comment_and_save_counts(self):
        insp = self._inspiration()
        self.repo.create_comment(insp.id, self.user_id, "one")
        self.repo.create_comment(insp.id, self.other_user_id, "two")
        self.repo.create_save(self.other_user_id, insp.id)

        items, _ = self.repo.list_feed(self.user_id)

        self.assertEqual(items[0].comment_count, 2)
        self.assertEqual(items[0].save_count, 1)

    def test_saved_by_me_is_per_viewer(self):
        insp = self._inspiration()
        self.repo.create_save(self.other_user_id, insp.id)

        mine = self.repo.get_by_id(insp.id, viewer_id=self.user_id)
        theirs = self.repo.get_by_id(insp.id, viewer_id=self.other_user_id)

        self.assertFalse(mine.saved_by_me)
        self.assertTrue(theirs.saved_by_me)

    def test_dto_projects_author_media_and_counts(self):
        insp = self._inspiration(title="Card")
        self.repo.create_comment(insp.id, self.other_user_id, "nice")

        dto = inspiration_to_dto(self.repo.get_by_id(insp.id, viewer_id=self.user_id))

        self.assertEqual(dto["title"], "Card")
        self.assertEqual(dto["author"]["id"], self.user_id)
        self.assertEqual(dto["author"]["username"], "testuser")
        self.assertEqual(dto["comment_count"], 1)
        self.assertEqual(dto["media"][0]["url"], f"/api/media/inspirations/{insp.id}/a.png")


class TestFeedFiltering(InspirationRepositoryTestBase):

    def test_query_matches_title_description_or_author(self):
        self._inspiration(title="Sunset over water")
        self._inspiration(title="Unrelated", description="mentions sunset in the body")
        self._inspiration(title="Nothing matching")

        items, total = self.repo.list_feed(self.user_id, query="sunset")

        self.assertEqual(total, 2)

    def test_author_id_filters_to_one_author(self):
        self._inspiration(user_id=self.user_id, title="Mine")
        self._inspiration(user_id=self.other_user_id, title="Theirs")

        items, total = self.repo.list_feed(self.user_id, author_id=self.other_user_id)

        self.assertEqual(total, 1)
        self.assertEqual(items[0].title, "Theirs")

    def test_saved_filters_to_the_viewers_own_saves(self):
        saved = self._inspiration(title="Saved")
        not_saved = self._inspiration(title="Not saved")
        self.repo.create_save(self.user_id, saved.id)

        items, total = self.repo.list_feed(self.user_id, saved=True)

        self.assertEqual(total, 1)
        self.assertEqual(items[0].id, saved.id)

    def test_collection_id_is_scoped_to_the_viewers_own_collection(self):
        insp = self._inspiration(title="In a collection")
        collection = self.repo.create_collection(self.user_id, "Favorites")
        self.repo.add_item(collection.id, insp.id)

        mine = self.repo.list_feed(self.user_id, collection_id=collection.id)
        # Someone else's collection_id (or a collection that isn't theirs)
        # yields an empty page rather than an error.
        others = self.repo.list_feed(self.other_user_id, collection_id=collection.id)

        self.assertEqual(mine[1], 1)
        self.assertEqual(others[1], 0)


class TestCollectionCycleGuard(InspirationRepositoryTestBase):

    def test_creates_cycle_detects_self_and_descendants(self):
        parent = self.repo.create_collection(self.user_id, "Parent")
        child = self.repo.create_collection(self.user_id, "Child", parent.id)

        self.assertTrue(self.repo.creates_cycle(parent.id, parent.id))
        self.assertTrue(self.repo.creates_cycle(parent.id, child.id))
        self.assertFalse(self.repo.creates_cycle(child.id, parent.id))


if __name__ == "__main__":
    unittest.main()
