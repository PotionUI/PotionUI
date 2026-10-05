from src.features.collections.repository import CollectionRepository
from src.features.prompt_database.records import Prompt
from src.features.prompt_database.repository import PromptRepository
from src.features.segments.dto import RichSegment
from src.platform.util.ids import generate_ulid
from tests.fixtures.persistence_base import PersistenceTestBase


class TestPromptCollectionFilters(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.repository = PromptRepository()
        self.collections = CollectionRepository()
        self.user = self.create_test_user("cf-user-1", "cfuser1", "cf1@example.com")
        self.other = self.create_test_user("cf-user-2", "cfuser2", "cf2@example.com")

    def _prompt(self, user, text):
        return self.repository.create(
            Prompt(id=generate_ulid(), user_id=user, segments=[RichSegment(content=text)])
        )

    def _chain(self, user, depth):
        chain = []
        parent = None
        for level in range(depth):
            node = self.collections.create(f"level-{level}", user, "prompts", parent)
            chain.append(node)
            parent = node.id
        return chain

    def _ids(self, **filters):
        return {p.id for p in self.repository.get_all(user_id=self.user, **filters)}

    def test_parent_includes_items_four_levels_down(self):
        chain = self._chain(self.user, 4)
        root_prompt = self._prompt(self.user, "root")
        deep_prompt = self._prompt(self.user, "deep")
        self.collections.add_prompt_members(chain[0].id, [root_prompt.id], self.user, "prompts")
        self.collections.add_prompt_members(chain[3].id, [deep_prompt.id], self.user, "prompts")
        self.assertEqual(self._ids(collection_id=chain[0].id), {root_prompt.id, deep_prompt.id})
        self.assertEqual(self._ids(collection_id=chain[2].id), {deep_prompt.id})

    def test_direct_only_excludes_descendant_items(self):
        chain = self._chain(self.user, 3)
        root_prompt = self._prompt(self.user, "root")
        deep_prompt = self._prompt(self.user, "deep")
        self.collections.add_prompt_members(chain[0].id, [root_prompt.id], self.user, "prompts")
        self.collections.add_prompt_members(chain[2].id, [deep_prompt.id], self.user, "prompts")
        self.assertEqual(
            self._ids(collection_id=chain[0].id, include_descendants=False), {root_prompt.id}
        )
        self.assertEqual(
            self.repository.count(self.user, collection_id=chain[0].id, include_descendants=False), 1
        )
        self.assertEqual(self.repository.count(self.user, collection_id=chain[0].id), 2)

    def test_cycle_in_parent_links_terminates(self):
        chain = self._chain(self.user, 2)
        prompt = self._prompt(self.user, "inside")
        self.collections.add_prompt_members(chain[1].id, [prompt.id], self.user, "prompts")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE collections SET parent_id = ? WHERE id = ?", (chain[1].id, chain[0].id)
            )
        self.assertEqual(self._ids(collection_id=chain[0].id), {prompt.id})

    def test_unsorted_excludes_filed_prompts_and_combines_with_search(self):
        collection = self.collections.create("Filed", self.user, "prompts")
        filed = self._prompt(self.user, "fox filed")
        loose_fox = self._prompt(self.user, "fox loose")
        loose_owl = self._prompt(self.user, "owl loose")
        self.collections.add_prompt_members(collection.id, [filed.id], self.user, "prompts")
        self.assertEqual(self._ids(unsorted=True), {loose_fox.id, loose_owl.id})
        self.assertEqual(self._ids(unsorted=True, q="fox"), {loose_fox.id})
        self.assertEqual(self.repository.count(self.user, unsorted=True), 2)

    def test_unsorted_ignores_other_users_collections(self):
        theirs = self.collections.create("Theirs", self.other, "prompts")
        mine = self._prompt(self.user, "mine")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO collection_prompts (collection_id, prompt_id) VALUES (?, ?)",
                (theirs.id, mine.id),
            )
        self.assertEqual(self._ids(unsorted=True), {mine.id})

    def test_other_users_collection_id_returns_nothing(self):
        theirs = self.collections.create("Theirs", self.other, "prompts")
        theirs_prompt = self._prompt(self.other, "theirs")
        self.collections.add_prompt_members(theirs.id, [theirs_prompt.id], self.other, "prompts")
        self.assertEqual(self._ids(collection_id=theirs.id), set())

    def test_descendants_of_another_users_child_are_not_followed(self):
        parent = self.collections.create("Parent", self.user, "prompts")
        foreign_child = self.collections.create("Foreign", self.other, "prompts", parent.id)
        slipped = self._prompt(self.user, "slipped")
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO collection_prompts (collection_id, prompt_id) VALUES (?, ?)",
                (foreign_child.id, slipped.id),
            )
        self.assertEqual(self._ids(collection_id=parent.id), set())

    def test_favorites_only_and_set_favorite(self):
        first = self._prompt(self.user, "first")
        second = self._prompt(self.user, "second")
        self.assertTrue(self.repository.set_favorite(first.id, self.user, True))
        self.assertEqual(self._ids(favorites_only=True), {first.id})
        self.assertEqual(self.repository.count(self.user, favorites_only=True), 1)
        self.assertEqual(self._ids(), {first.id, second.id})
        self.assertTrue(self.repository.get_all(user_id=self.user, favorites_only=True)[0].is_favorite)

    def test_set_favorite_rejects_other_users_prompt(self):
        theirs = self._prompt(self.other, "theirs")
        self.assertIsNone(self.repository.set_favorite(theirs.id, self.user, True))
        self.assertEqual(
            {p.id for p in self.repository.get_all(user_id=self.other, favorites_only=True)}, set()
        )
