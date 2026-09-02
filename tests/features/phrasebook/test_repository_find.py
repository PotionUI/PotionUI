"""The find candidate fetchers and the bulk writers on both phrasebook
repositories against a real SQLite schema."""
from datetime import datetime

from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookValue
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from tests.fixtures.persistence_base import PersistenceTestBase


class PhrasebookRepositoryBase(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        self.categories = PhrasebookCategoryRepository()
        self.values = PhrasebookValueRepository()
        self.user_id = self.create_test_user()
        self.other_user = self.create_test_user("other", "other", "other@example.com")

    def category(self, cat_id, name, path, description="", user_id=None, active=True):
        assert self.categories.create(PhrasebookCategory(
            id=cat_id, name=name, path=path, parent_id=None, description=description,
            user_id=user_id or self.user_id,
            created_at=datetime.now(), updated_at=datetime.now(),
        ))
        if not active:
            self.categories.update_active_state(cat_id, user_id or self.user_id, False)

    def value(self, val_id, cat_id, label, text, user_id=None, active=True, sort_order=0):
        assert self.values.create(PhrasebookValue(
            id=val_id, category_id=cat_id, label=label, value=text, sort_order=sort_order,
            user_id=user_id or self.user_id,
            created_at=datetime.now(), updated_at=datetime.now(),
        ))
        if not active:
            self.values.update_active_state(val_id, user_id or self.user_id, False)


class TestCategoryListForFind(PhrasebookRepositoryBase):
    def test_substring_prefilter_is_case_insensitive_across_name_path_description(self):
        self.category("c1", "Dogs", "animals.dogs")
        self.category("c2", "Pets", "pets", description="Mostly DOG photos")
        self.category("c3", "Breeds", "hotdog.breeds")
        self.category("c4", "Cats", "animals.cats")

        ids = [c.id for c in self.categories.list_for_find(self.user_id, "dog")]

        self.assertEqual(ids, ["c1", "c3", "c2"])

    def test_no_substring_returns_everything_ordered_by_path(self):
        self.category("b", "B", "b")
        self.category("a", "A", "a")

        self.assertEqual([c.id for c in self.categories.list_for_find(self.user_id, None)], ["a", "b"])

    def test_escapes_like_wildcards(self):
        self.category("literal", "100% dog", "pct.dog")
        self.category("plain", "100 dog", "plain.dog")
        self.category("under", "snake_case", "snake.case")
        self.category("nounder", "snakeXcase", "snakex.case")

        self.assertEqual([c.id for c in self.categories.list_for_find(self.user_id, "100%")], ["literal"])
        self.assertEqual([c.id for c in self.categories.list_for_find(self.user_id, "snake_")], ["under"])

    def test_path_prefix_restricts_to_the_subtree(self):
        self.category("root", "Animals", "animals")
        self.category("child", "Dogs", "animals.dogs")
        self.category("sibling", "Animalsx", "animalsx")
        self.category("other", "Food", "food")

        ids = [c.id for c in self.categories.list_for_find(self.user_id, None, path_prefix="animals")]

        self.assertEqual(ids, ["root", "child"])

    def test_include_inactive_false_drops_inactive(self):
        self.category("on", "Dog on", "dog.on")
        self.category("off", "Dog off", "dog.off", active=False)

        self.assertEqual({c.id for c in self.categories.list_for_find(self.user_id, "dog")}, {"on", "off"})
        self.assertEqual(
            [c.id for c in self.categories.list_for_find(self.user_id, "dog", include_inactive=False)],
            ["on"],
        )

    def test_scoped_to_user(self):
        self.category("mine", "Dog", "dog")
        self.category("theirs", "Dog theirs", "dog.theirs", user_id=self.other_user)

        self.assertEqual([c.id for c in self.categories.list_for_find(self.user_id, "dog")], ["mine"])


class TestValueListForFind(PhrasebookRepositoryBase):
    def setUp(self):
        super().setUp()
        self.category("cat", "Animals", "animals")
        self.category("sub", "Dogs", "animals.dogs")
        self.category("cat-off", "Retired", "retired", active=False)

    def test_substring_prefilter_on_label_and_value_with_category_info(self):
        self.value("v1", "cat", "Puppy", "a small DOG")
        self.value("v2", "cat", "Doghouse", "wooden shelter")
        self.value("v3", "cat", "Kitten", "a small cat")

        hits = self.values.list_for_find(self.user_id, "dog")

        self.assertEqual([h["id"] for h in hits], ["v2", "v1"])
        by_id = {h["id"]: h for h in hits}
        self.assertEqual(by_id["v1"]["category_id"], "cat")
        self.assertEqual(by_id["v1"]["category_path"], "animals")
        self.assertEqual(by_id["v1"]["category_name"], "Animals")
        self.assertTrue(by_id["v1"]["category_is_active"])

    def test_ordered_by_label_case_insensitively_then_category_path(self):
        self.value("b", "cat", "beta", "x")
        self.value("a-sub", "sub", "Alpha", "x")
        self.value("a-cat", "cat", "alpha", "x")

        self.assertEqual([h["id"] for h in self.values.list_for_find(self.user_id, None)], ["a-cat", "a-sub", "b"])

    def test_inactive_flags_and_include_inactive_false(self):
        self.value("on", "cat", "Dog on", "x")
        self.value("off", "cat", "Dog off", "x", active=False)
        self.value("retired", "cat-off", "Dog retired", "x")

        hits = {h["id"]: (h["is_active"], h["category_is_active"]) for h in self.values.list_for_find(self.user_id, "dog")}
        self.assertEqual(hits, {"on": (True, True), "off": (False, True), "retired": (True, False)})

        active_only = [h["id"] for h in self.values.list_for_find(self.user_id, "dog", include_inactive=False)]
        self.assertEqual(active_only, ["on"])

    def test_path_prefix_restricts_to_the_subtree(self):
        self.value("in-root", "cat", "Dog", "x")
        self.value("in-sub", "sub", "Dog", "x")
        self.value("out", "cat-off", "Dog", "x")

        ids = {h["id"] for h in self.values.list_for_find(self.user_id, "dog", path_prefix="animals")}
        self.assertEqual(ids, {"in-root", "in-sub"})
        ids = {h["id"] for h in self.values.list_for_find(self.user_id, "dog", path_prefix="animals.dogs")}
        self.assertEqual(ids, {"in-sub"})

    def test_escapes_like_wildcards(self):
        self.value("literal", "cat", "50% dog", "x")
        self.value("plain", "cat", "50 dog", "x")

        self.assertEqual([h["id"] for h in self.values.list_for_find(self.user_id, "50%")], ["literal"])

    def test_scoped_to_user(self):
        self.category("theirs", "Theirs", "theirs", user_id=self.other_user)
        self.value("mine", "cat", "Dog", "x")
        self.value("their-value", "theirs", "Dog theirs", "x", user_id=self.other_user)

        self.assertEqual([h["id"] for h in self.values.list_for_find(self.user_id, "dog")], ["mine"])


class TestValueBulkWrites(PhrasebookRepositoryBase):
    def setUp(self):
        super().setUp()
        self.category("cat", "Animals", "animals")
        self.category("theirs", "Theirs", "theirs", user_id=self.other_user)
        self.value("v1", "cat", "One", "one")
        self.value("v2", "cat", "Two", "two")
        self.value("t1", "theirs", "Their", "their", user_id=self.other_user)

    def test_get_many_keeps_request_order_and_skips_foreign_ids(self):
        got = self.values.get_many(["v2", "t1", "nope", "v1"], self.user_id)
        self.assertEqual([v.id for v in got], ["v2", "v1"])
        self.assertEqual(self.values.get_many([], self.user_id), [])

    def test_max_sort_order(self):
        self.assertEqual(self.values.max_sort_order("cat", self.user_id), 0)
        self.value("v3", "cat", "Three", "three", sort_order=7)
        self.assertEqual(self.values.max_sort_order("cat", self.user_id), 7)
        self.assertEqual(self.values.max_sort_order("empty", self.user_id), -1)

    def test_update_texts_bulk(self):
        self.values.update_texts_bulk(self.user_id, [("v1", "Uno", "uno"), ("v2", "Dos", "dos")])

        self.assertEqual(self.values.get_by_id("v1").label, "Uno")
        self.assertEqual(self.values.get_by_id("v2").value, "dos")

    def test_update_texts_bulk_rolls_back_everything_on_a_foreign_id(self):
        with self.assertRaises(RuntimeError):
            self.values.update_texts_bulk(self.user_id, [("v1", "Uno", "uno"), ("t1", "X", "x")])

        self.assertEqual(self.values.get_by_id("v1").label, "One")
        self.assertEqual(self.values.get_by_id("t1").label, "Their")

    def test_update_active_state_bulk_rolls_back_on_a_foreign_id(self):
        self.values.update_active_state_bulk(["v1", "v2"], self.user_id, False)
        self.assertFalse(self.values.get_by_id("v1").is_active)
        self.assertFalse(self.values.get_by_id("v2").is_active)

        with self.assertRaises(RuntimeError):
            self.values.update_active_state_bulk(["v1", "t1"], self.user_id, True)
        self.assertFalse(self.values.get_by_id("v1").is_active)

    def test_move_bulk_rolls_back_on_a_foreign_id(self):
        self.category("dest", "Dest", "dest")
        self.values.move_bulk(self.user_id, [("v1", "dest", 5)])
        moved = self.values.get_by_id("v1")
        self.assertEqual((moved.category_id, moved.sort_order), ("dest", 5))

        with self.assertRaises(RuntimeError):
            self.values.move_bulk(self.user_id, [("v2", "dest", 6), ("t1", "dest", 7)])
        self.assertEqual(self.values.get_by_id("v2").category_id, "cat")

    def test_delete_bulk_rolls_back_on_a_foreign_id(self):
        with self.assertRaises(RuntimeError):
            self.values.delete_bulk(["v1", "t1"], self.user_id)
        self.assertIsNotNone(self.values.get_by_id("v1"))

        self.values.delete_bulk(["v1", "v2"], self.user_id)
        self.assertIsNone(self.values.get_by_id("v1"))
        self.assertIsNone(self.values.get_by_id("v2"))
        self.assertIsNotNone(self.values.get_by_id("t1"))
