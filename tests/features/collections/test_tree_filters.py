from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.collections.repository import CollectionRepository
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.library.repository import LibraryRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.platform.util.ids import generate_ulid

HISTORY = "history"
LIBRARY = "library"


class TestTreeFilters(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.collections = CollectionRepository()
        self.generations = GenerationRepository()
        self.library = LibraryRepository()
        self.uploads = UploadRepository()
        self.user = self.create_test_user()
        self.other = self.create_test_user("other_user", "other", "other@example.com")

    def _generation(self, user_id=None, favorite=False):
        generation = Generation(
            id=generate_ulid(),
            preset_id="p",
            form_data={"prompt": "x"},
            user_id=user_id or self.user,
            status="completed",
            preset_version="1.0",
        )
        self.generations.create(generation)
        if favorite:
            self.generations.set_favorite(generation.id, True, generation.user_id)
        return generation.id

    def _upload(self, user_id=None, name=None):
        return self.uploads.create(Upload(
            user_id=user_id or self.user,
            filename=name or f"{generate_ulid()}.png",
            media_type="image",
            mime_type="image/png",
            file_size=10,
        )).id

    def _chain(self, scope, depth, user_id=None):
        user_id = user_id or self.user
        ids = []
        parent = None
        for level in range(depth):
            created = self.collections.create(f"L{level}", user_id, scope, parent_id=parent)
            ids.append(created.id)
            parent = created.id
        return ids

    def _history_ids(self, **kwargs):
        rows = self.generations.get_all(user_id=self.user, **kwargs)
        return {row.id for row in rows}

    def test_history_parent_includes_items_four_levels_down(self):
        chain = self._chain(HISTORY, 5)
        top = self._generation()
        bottom = self._generation()
        self.collections.add_members(chain[0], [top], self.user, HISTORY)
        self.collections.add_members(chain[4], [bottom], self.user, HISTORY)
        self.assertEqual(self._history_ids(collection_id=chain[0]), {top, bottom})
        self.assertEqual(self._history_ids(collection_id=chain[2]), {bottom})

    def test_history_direct_only_excludes_descendants(self):
        chain = self._chain(HISTORY, 4)
        top = self._generation()
        bottom = self._generation()
        self.collections.add_members(chain[0], [top], self.user, HISTORY)
        self.collections.add_members(chain[3], [bottom], self.user, HISTORY)
        self.assertEqual(self._history_ids(collection_id=chain[0], include_descendants=False), {top})

    def test_history_count_matches_listing_with_descendants(self):
        chain = self._chain(HISTORY, 3)
        items = [self._generation() for _ in range(3)]
        for collection_id, item in zip(chain, items):
            self.collections.add_members(collection_id, [item], self.user, HISTORY)
        self.assertEqual(self.generations.count_by_status(user_id=self.user, collection_id=chain[0]), 3)
        self.assertEqual(
            self.generations.count_by_status(user_id=self.user, collection_id=chain[0], include_descendants=False), 1
        )

    def test_history_cycle_does_not_hang(self):
        chain = self._chain(HISTORY, 2)
        item = self._generation()
        self.collections.add_members(chain[1], [item], self.user, HISTORY)
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE collections SET parent_id = ? WHERE id = ?", (chain[1], chain[0]))
        self.assertEqual(self._history_ids(collection_id=chain[0]), {item})

    def test_history_unsorted_only_items_in_no_collection(self):
        chain = self._chain(HISTORY, 1)
        filed = self._generation()
        loose = self._generation()
        self.collections.add_members(chain[0], [filed], self.user, HISTORY)
        self.assertEqual(self._history_ids(unsorted=True), {loose})

    def test_history_unsorted_combines_with_favorites(self):
        chain = self._chain(HISTORY, 1)
        filed_favorite = self._generation(favorite=True)
        loose_favorite = self._generation(favorite=True)
        self._generation()
        self.collections.add_members(chain[0], [filed_favorite], self.user, HISTORY)
        self.assertEqual(self._history_ids(unsorted=True, favorites_only=True), {loose_favorite})

    def test_history_unsorted_ignores_other_users_collections(self):
        other_chain = self._chain(HISTORY, 1, user_id=self.other)
        mine = self._generation()
        self.collections.add_members(other_chain[0], [mine], self.other, HISTORY)
        self.assertEqual(self._history_ids(unsorted=True), {mine})

    def test_history_other_users_collection_returns_nothing(self):
        other_chain = self._chain(HISTORY, 2, user_id=self.other)
        theirs = self._generation(user_id=self.other)
        self.collections.add_members(other_chain[1], [theirs], self.other, HISTORY)
        self.assertEqual(self._history_ids(collection_id=other_chain[0]), set())

    def test_history_descendants_never_cross_owners(self):
        mine = self._chain(HISTORY, 1)
        foreign_child = self.collections.create("Foreign", self.other, HISTORY, parent_id=mine[0])
        theirs = self._generation(user_id=self.other)
        self.collections.add_members(foreign_child.id, [theirs], self.other, HISTORY)
        self.assertEqual(self._history_ids(collection_id=mine[0]), set())

    def test_library_parent_includes_descendants_and_direct_only_does_not(self):
        chain = self._chain(LIBRARY, 4)
        top = self._upload()
        bottom = self._upload()
        self.collections.add_upload_members(chain[0], [top], self.user, LIBRARY)
        self.collections.add_upload_members(chain[3], [bottom], self.user, LIBRARY)
        deep = {u.id for u in self.library.list_items(self.user, collection_id=chain[0])}
        direct = {u.id for u in self.library.list_items(self.user, collection_id=chain[0], include_descendants=False)}
        self.assertEqual(deep, {top, bottom})
        self.assertEqual(direct, {top})
        self.assertEqual(self.library.count_items(self.user, collection_id=chain[0]), 2)

    def test_library_unsorted_and_favorites(self):
        chain = self._chain(LIBRARY, 1)
        filed = self._upload()
        loose = self._upload()
        loose_favorite = self._upload()
        self.collections.add_upload_members(chain[0], [filed], self.user, LIBRARY)
        self.library.set_favorite(loose_favorite, self.user, True)
        unsorted = {u.id for u in self.library.list_items(self.user, unsorted=True)}
        favorites = {u.id for u in self.library.list_items(self.user, favorites_only=True)}
        both = {u.id for u in self.library.list_items(self.user, unsorted=True, favorites_only=True)}
        self.assertEqual(unsorted, {loose, loose_favorite})
        self.assertEqual(favorites, {loose_favorite})
        self.assertEqual(both, {loose_favorite})

    def test_library_favorite_toggle_ignores_other_users_items(self):
        theirs = self._upload(user_id=self.other)
        self.assertFalse(self.library.set_favorite(theirs, self.user, True))
        self.assertEqual([u for u in self.library.list_items(self.other, favorites_only=True)], [])

    def test_library_never_lists_other_users_items_through_a_shared_tree(self):
        chain = self._chain(LIBRARY, 1)
        theirs = self._upload(user_id=self.other)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO collection_uploads (collection_id, upload_id) VALUES (?, ?)", (chain[0], theirs)
            )
        self.assertEqual(self.library.list_items(self.user, collection_id=chain[0]), [])

    def test_collection_counts_roll_up_by_default(self):
        chain = self._chain(HISTORY, 3)
        items = [self._generation() for _ in range(3)]
        for collection_id, item in zip(chain, items):
            self.collections.add_members(collection_id, [item], self.user, HISTORY)
        rolled = {c.id: c.item_count for c in self.collections.list(self.user, HISTORY, include_descendants=True)}
        direct = {c.id: c.item_count for c in self.collections.list(self.user, HISTORY)}
        self.assertEqual([rolled[c] for c in chain], [3, 2, 1])
        self.assertEqual([direct[c] for c in chain], [1, 1, 1])

    def test_collection_counts_count_an_item_once_when_filed_twice(self):
        chain = self._chain(HISTORY, 2)
        item = self._generation()
        self.collections.add_members(chain[0], [item], self.user, HISTORY)
        self.collections.add_members(chain[1], [item], self.user, HISTORY)
        rolled = {c.id: c.item_count for c in self.collections.list(self.user, HISTORY, include_descendants=True)}
        self.assertEqual(rolled[chain[0]], 1)

    def test_collection_counts_skip_other_users_items(self):
        chain = self._chain(HISTORY, 1)
        theirs = self._generation(user_id=self.other)
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO collection_generations (collection_id, generation_id) VALUES (?, ?)",
                (chain[0], theirs),
            )
        rolled = {c.id: c.item_count for c in self.collections.list(self.user, HISTORY, include_descendants=True)}
        self.assertEqual(rolled[chain[0]], 0)

    def test_smart_counts_history(self):
        chain = self._chain(HISTORY, 1)
        filed = self._generation(favorite=True)
        self._generation(favorite=True)
        self._generation()
        self._generation(user_id=self.other)
        self.collections.add_members(chain[0], [filed], self.user, HISTORY)
        self.assertEqual(self.collections.smart_counts(self.user, HISTORY), {"all": 3, "favorites": 2, "unsorted": 2})

    def test_smart_counts_library_ignore_derived_artifacts(self):
        chain = self._chain(LIBRARY, 1)
        filed = self._upload()
        self._upload()
        self.uploads.create(Upload(
            user_id=self.user, filename="mask.png", media_type="image", purpose="derived_artifact"
        ))
        self.collections.add_upload_members(chain[0], [filed], self.user, LIBRARY)
        self.assertEqual(self.collections.smart_counts(self.user, LIBRARY), {"all": 2, "favorites": 0, "unsorted": 1})
