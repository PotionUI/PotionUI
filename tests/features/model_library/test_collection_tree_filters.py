from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository
from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.platform.util.ids import generate_ulid


class TestModelCollectionTreeFilters(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.collections = ModelCollectionRepository()
        self.models = ModelRepository()
        self.meta = UserModelMetaRepository()
        self.user = self.create_test_user()
        self.other = self.create_test_user("other_user", "other", "other@example.com")
        self.root = self.collections.create("root", self.user)
        self.mid = self.collections.create("mid", self.user, self.root.id)
        self.deep = self.collections.create("deep", self.user, self.mid.id)
        self.deepest = self.collections.create("deepest", self.user, self.deep.id)
        self.in_root = self._model("in_root")
        self.in_deepest = self._model("in_deepest")
        self.loose = self._model("loose")
        self.collections.add_members(self.root.id, [self.in_root], self.user)
        self.collections.add_members(self.deepest.id, [self.in_deepest], self.user)

    def _model(self, name):
        model = Model(id=generate_ulid(), filename=f"{name}.safetensors", file_size=1, model_type="checkpoint")
        return self.models.create(model).id

    def _ids(self, **filters):
        return {m.id for m in self.models.get_all(library_user_id=self.user, **filters)}

    def test_parent_includes_items_three_levels_down(self):
        self.assertEqual(self._ids(collection_id=self.root.id), {self.in_root, self.in_deepest})

    def test_direct_only_excludes_descendants(self):
        self.assertEqual(self._ids(collection_id=self.root.id, include_descendants=False), {self.in_root})

    def test_middle_collection_includes_only_its_subtree(self):
        self.assertEqual(self._ids(collection_id=self.mid.id), {self.in_deepest})

    def test_unsorted_lists_models_in_no_collection(self):
        self.assertEqual(self._ids(unsorted=True), {self.loose})

    def test_unsorted_ignores_other_users_collections(self):
        theirs = self.collections.create("theirs", self.other)
        self.collections.add_members(theirs.id, [self.loose], self.other)
        self.assertEqual(self._ids(unsorted=True), {self.loose})

    def test_unsorted_combines_with_favorites(self):
        self.meta.set_favorite(self.user, self.loose, True)
        self.meta.set_favorite(self.user, self.in_root, True)
        self.assertEqual(self._ids(unsorted=True, favorites_only=True), {self.loose})

    def test_count_total_follows_descendant_rules(self):
        self.assertEqual(self.models.count_total(library_user_id=self.user, collection_id=self.root.id), 2)
        self.assertEqual(
            self.models.count_total(library_user_id=self.user, collection_id=self.root.id, include_descendants=False), 1
        )
        self.assertEqual(self.models.count_total(library_user_id=self.user, unsorted=True), 1)

    def test_rolled_up_counts_include_descendants(self):
        counts = {c.id: c.item_count for c in self.collections.list(self.user)}
        self.assertEqual(counts[self.root.id], 2)
        self.assertEqual(counts[self.mid.id], 1)
        self.assertEqual(counts[self.deepest.id], 1)

    def test_direct_counts_exclude_descendants(self):
        counts = {c.id: c.item_count for c in self.collections.list(self.user, include_descendants=False)}
        self.assertEqual(counts[self.root.id], 1)
        self.assertEqual(counts[self.mid.id], 0)

    def test_item_in_two_branches_counts_once(self):
        self.collections.add_members(self.mid.id, [self.in_deepest], self.user)
        counts = {c.id: c.item_count for c in self.collections.list(self.user)}
        self.assertEqual(counts[self.root.id], 2)

    def test_collection_list_is_only_the_owners(self):
        self.collections.create("theirs", self.other)
        ids = {c.id for c in self.collections.list(self.user)}
        self.assertEqual(ids, {self.root.id, self.mid.id, self.deep.id, self.deepest.id})

    def test_other_users_counts_ignore_this_users_items(self):
        theirs = self.collections.create("theirs", self.other)
        counts = {c.id: c.item_count for c in self.collections.list(self.other)}
        self.assertEqual(counts[theirs.id], 0)

    def test_smart_counts(self):
        self.meta.set_favorite(self.user, self.in_root, True)
        self.assertEqual(self.collections.smart_counts(self.user), {"all": 3, "favorites": 1, "unsorted": 1})

    def test_cycle_in_parent_ids_terminates(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE model_collections SET parent_id = ? WHERE id = ?", (self.deepest.id, self.root.id))
        self.assertEqual(self._ids(collection_id=self.root.id), {self.in_root, self.in_deepest})
        counts = {c.id: c.item_count for c in self.collections.list(self.user)}
        self.assertEqual(counts[self.root.id], 2)
