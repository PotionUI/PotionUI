from tests.features.inspirations.test_repository import InspirationRepositoryTestBase
from tests.features.inspirations.test_routes import InspirationRoutesTestBase


class TestFeedSortingRepository(InspirationRepositoryTestBase):

    def _seed(self):
        a = self._inspiration(title="banana", created_at="2026-01-02 00:00:00")
        b = self._inspiration(title="Apple", created_at="2026-01-03 00:00:00")
        c = self._inspiration(title="cherry", created_at="2026-01-01 00:00:00")
        self.repo.create_save(self.user_id, a.id)
        self.repo.create_save(self.other_user_id, a.id)
        self.repo.create_save(self.user_id, c.id)
        self.repo.create_comment(c.id, self.user_id, "x")
        self.repo.create_comment(c.id, self.other_user_id, "y")
        self.repo.create_comment(b.id, self.user_id, "z")

    def _titles(self, **kwargs):
        items, _ = self.repo.list_feed(self.user_id, **kwargs)
        return [i.title for i in items]

    def test_default_is_newest_first(self):
        self._seed()
        self.assertEqual(self._titles(), ["Apple", "banana", "cherry"])

    def test_created_both_directions(self):
        self._seed()
        self.assertEqual(self._titles(sort_by="created", sort_dir="asc"), ["cherry", "banana", "Apple"])
        self.assertEqual(self._titles(sort_by="created", sort_dir="desc"), ["Apple", "banana", "cherry"])

    def test_title_is_case_insensitive(self):
        self._seed()
        self.assertEqual(self._titles(sort_by="title"), ["Apple", "banana", "cherry"])
        self.assertEqual(self._titles(sort_by="title", sort_dir="desc"), ["cherry", "banana", "Apple"])

    def test_saved_most_saved_first(self):
        self._seed()
        self.assertEqual(self._titles(sort_by="saved"), ["banana", "cherry", "Apple"])
        self.assertEqual(self._titles(sort_by="saved", sort_dir="asc"), ["Apple", "cherry", "banana"])

    def test_comments_most_commented_first(self):
        self._seed()
        self.assertEqual(self._titles(sort_by="comments"), ["cherry", "Apple", "banana"])
        self.assertEqual(self._titles(sort_by="comments", sort_dir="asc"), ["banana", "Apple", "cherry"])

    def test_ties_paginate_without_duplicates_or_gaps(self):
        for _ in range(7):
            self._inspiration(title="same", created_at="2026-01-01 00:00:00")
        for sort_by in ("created", "title", "saved", "comments"):
            for direction in ("asc", "desc"):
                seen = []
                for offset in (0, 3, 6):
                    items, total = self.repo.list_feed(
                        self.user_id, limit=3, offset=offset, sort_by=sort_by, sort_dir=direction
                    )
                    seen += [i.id for i in items]
                self.assertEqual(total, 7)
                self.assertEqual(len(seen), 7)
                self.assertEqual(len(set(seen)), 7)

    def test_sort_composes_with_filters(self):
        self._seed()
        items, total = self.repo.list_feed(self.user_id, query="an", sort_by="title")
        self.assertEqual(([i.title for i in items], total), (["banana"], 1))


class TestFeedSortingRoutes(InspirationRoutesTestBase):

    def test_sort_params_reach_the_feed_and_bad_values_are_422(self):
        for title in ("banana", "Apple"):
            self._publish(title=title)
        response = self.client.get("/api/inspirations", params={"sort_by": "title"})
        self.assertEqual([i["title"] for i in response.json()["data"]["items"]], ["Apple", "banana"])
        response = self.client.get("/api/inspirations", params={"sort_by": "title", "sort_dir": "desc"})
        self.assertEqual([i["title"] for i in response.json()["data"]["items"]], ["banana", "Apple"])
        bad = self.client.get("/api/inspirations", params={"sort_by": "color"})
        self.assertEqual(bad.status_code, 422)
        for allowed in ("created", "title", "saved", "comments"):
            self.assertIn(allowed, bad.text)
        self.assertEqual(self.client.get("/api/inspirations", params={"sort_dir": "up"}).status_code, 422)
