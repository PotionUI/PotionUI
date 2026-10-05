from tests.features.inspirations.test_repository import InspirationRepositoryTestBase


class TestInspirationCollectionTreeFilters(InspirationRepositoryTestBase):

    def setUp(self):
        super().setUp()
        self.root = self.repo.create_collection(self.user_id, "root")
        self.mid = self.repo.create_collection(self.user_id, "mid", self.root.id)
        self.deep = self.repo.create_collection(self.user_id, "deep", self.mid.id)
        self.deepest = self.repo.create_collection(self.user_id, "deepest", self.deep.id)
        self.in_root = self._inspiration(title="in_root")
        self.in_deepest = self._inspiration(title="in_deepest")
        self.loose = self._inspiration(title="loose")
        self.repo.add_item(self.root.id, self.in_root.id)
        self.repo.add_item(self.deepest.id, self.in_deepest.id)

    def _ids(self, viewer=None, **filters):
        items, total = self.repo.list_feed(viewer or self.user_id, **filters)
        self.assertEqual(total, len(items))
        return {i.id for i in items}

    def _save(self, *items):
        with self.db.get_cursor() as cursor:
            for item in items:
                cursor.execute(
                    "INSERT INTO inspiration_saves (user_id, inspiration_id) VALUES (?, ?)",
                    (self.user_id, item.id),
                )

    def test_parent_includes_items_three_levels_down(self):
        self.assertEqual(self._ids(collection_id=self.root.id), {self.in_root.id, self.in_deepest.id})

    def test_direct_only_excludes_descendants(self):
        self.assertEqual(self._ids(collection_id=self.root.id, include_descendants=False), {self.in_root.id})

    def test_unsorted_lists_items_in_no_collection(self):
        self.assertEqual(self._ids(unsorted=True), {self.loose.id})

    def test_unsorted_is_per_viewer(self):
        self.assertEqual(
            self._ids(viewer=self.other_user_id, unsorted=True),
            {self.in_root.id, self.in_deepest.id, self.loose.id},
        )

    def test_unsorted_combines_with_saved(self):
        self._save(self.loose, self.in_root)
        self.assertEqual(self._ids(unsorted=True, saved=True), {self.loose.id})

    def test_foreign_collection_yields_empty_page(self):
        self.assertEqual(self._ids(viewer=self.other_user_id, collection_id=self.root.id), set())

    def test_foreign_collection_yields_empty_with_descendants_off(self):
        self.assertEqual(
            self._ids(viewer=self.other_user_id, collection_id=self.root.id, include_descendants=False), set()
        )

    def test_rolled_up_counts(self):
        counts = {c.id: c.item_count for c in self.repo.list_collections(self.user_id)}
        self.assertEqual(counts[self.root.id], 2)
        self.assertEqual(counts[self.mid.id], 1)

    def test_direct_counts(self):
        counts = {c.id: c.item_count for c in self.repo.list_collections(self.user_id, include_descendants=False)}
        self.assertEqual(counts[self.root.id], 1)
        self.assertEqual(counts[self.mid.id], 0)

    def test_collection_list_is_only_the_owners(self):
        self.repo.create_collection(self.other_user_id, "theirs")
        self.assertEqual(len(self.repo.list_collections(self.user_id)), 4)

    def test_smart_counts(self):
        self._save(self.loose)
        self.assertEqual(self.repo.smart_counts(self.user_id), {"all": 3, "favorites": 1, "unsorted": 1})

    def test_cycle_in_parent_ids_terminates(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE inspiration_collections SET parent_id = ? WHERE id = ?", (self.deepest.id, self.root.id)
            )
        self.assertEqual(self._ids(collection_id=self.root.id), {self.in_root.id, self.in_deepest.id})
