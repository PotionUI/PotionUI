"""find_by_text on both phrasebook repositories against a real SQLite schema."""
from datetime import datetime

from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookValue
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from tests.fixtures.persistence_base import PersistenceTestBase


class PhrasebookFindBase(PersistenceTestBase):
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

    def value(self, val_id, cat_id, label, text, user_id=None, active=True):
        assert self.values.create(PhrasebookValue(
            id=val_id, category_id=cat_id, label=label, value=text, sort_order=0,
            user_id=user_id or self.user_id,
            created_at=datetime.now(), updated_at=datetime.now(),
        ))
        if not active:
            self.values.update_active_state(val_id, user_id or self.user_id, False)


class TestCategoryFindByText(PhrasebookFindBase):
    def test_matches_name_path_and_description_case_insensitively(self):
        self.category("c1", "Dogs", "animals.dogs")
        self.category("c2", "Pets", "pets", description="Mostly DOG photos")
        self.category("c3", "Breeds", "hotdog.breeds")
        self.category("c4", "Cats", "animals.cats")

        ids = [c.id for c in self.categories.find_by_text(self.user_id, "dog")]

        self.assertEqual(sorted(ids), ["c1", "c2", "c3"])

    def test_orders_exact_then_prefix_then_substring_then_path(self):
        self.category("sub", "Hotdog", "food.hotdog")
        self.category("prefix-b", "Dogma", "z.dogma")
        self.category("prefix-a", "Doggo", "a.doggo")
        self.category("exact", "dog", "animals.dog")

        ids = [c.id for c in self.categories.find_by_text(self.user_id, "Dog")]

        self.assertEqual(ids, ["exact", "prefix-a", "prefix-b", "sub"])

    def test_includes_inactive_with_flag(self):
        self.category("on", "Dog on", "dog.on")
        self.category("off", "Dog off", "dog.off", active=False)

        hits = {c.id: c.is_active for c in self.categories.find_by_text(self.user_id, "dog")}

        self.assertEqual(hits, {"on": True, "off": False})

    def test_escapes_like_wildcards(self):
        self.category("literal", "100% dog", "pct.dog")
        self.category("plain", "100 dog", "plain.dog")
        self.category("under", "snake_case", "snake.case")
        self.category("nounder", "snakeXcase", "snakex.case")

        self.assertEqual([c.id for c in self.categories.find_by_text(self.user_id, "100%")], ["literal"])
        self.assertEqual([c.id for c in self.categories.find_by_text(self.user_id, "snake_")], ["under"])

    def test_scoped_to_user_and_limited(self):
        for i in range(5):
            self.category(f"c{i}", f"Dog {i}", f"dog.{i}")
        self.category("theirs", "Dog theirs", "dog.theirs", user_id=self.other_user)

        hits = self.categories.find_by_text(self.user_id, "dog", limit=3)

        self.assertEqual(len(hits), 3)
        self.assertTrue(all(c.user_id == self.user_id for c in hits))

    def test_blank_query_matches_nothing(self):
        self.category("c1", "Dogs", "dogs")
        self.assertEqual(self.categories.find_by_text(self.user_id, "   "), [])


class TestValueFindByText(PhrasebookFindBase):
    def setUp(self):
        super().setUp()
        self.category("cat", "Animals", "animals", active=True)
        self.category("cat-off", "Retired", "retired", active=False)

    def test_matches_label_and_value_case_insensitively_with_category_info(self):
        self.value("v1", "cat", "Puppy", "a small DOG")
        self.value("v2", "cat", "Doghouse", "wooden shelter")
        self.value("v3", "cat", "Kitten", "a small cat")

        hits = self.values.find_by_text(self.user_id, "dog")

        self.assertEqual(sorted(h["id"] for h in hits), ["v1", "v2"])
        by_id = {h["id"]: h for h in hits}
        self.assertEqual(by_id["v1"]["category_id"], "cat")
        self.assertEqual(by_id["v1"]["category_path"], "animals")
        self.assertEqual(by_id["v1"]["category_name"], "Animals")
        self.assertTrue(by_id["v1"]["category_is_active"])

    def test_orders_exact_then_prefix_then_substring_then_label(self):
        self.value("sub", "cat", "Hotdog", "sausage")
        self.value("prefix-b", "cat", "Dogma", "x")
        self.value("prefix-a", "cat", "Doggo", "x")
        self.value("exact", "cat", "Dog", "x")

        ids = [h["id"] for h in self.values.find_by_text(self.user_id, "dog")]

        self.assertEqual(ids, ["exact", "prefix-a", "prefix-b", "sub"])

    def test_includes_inactive_values_and_inactive_categories_with_flags(self):
        self.value("on", "cat", "Dog on", "x")
        self.value("off", "cat", "Dog off", "x", active=False)
        self.value("retired", "cat-off", "Dog retired", "x")

        hits = {h["id"]: (h["is_active"], h["category_is_active"]) for h in self.values.find_by_text(self.user_id, "dog")}

        self.assertEqual(hits, {"on": (True, True), "off": (False, True), "retired": (True, False)})

    def test_escapes_like_wildcards(self):
        self.value("literal", "cat", "50% dog", "x")
        self.value("plain", "cat", "50 dog", "x")

        self.assertEqual([h["id"] for h in self.values.find_by_text(self.user_id, "50%")], ["literal"])

    def test_scoped_to_user_and_limited(self):
        self.category("theirs", "Theirs", "theirs", user_id=self.other_user)
        for i in range(5):
            self.value(f"v{i}", "cat", f"Dog {i}", "x")
        self.value("their-value", "theirs", "Dog theirs", "x", user_id=self.other_user)

        hits = self.values.find_by_text(self.user_id, "dog", limit=3)

        self.assertEqual(len(hits), 3)
        self.assertTrue(all(h["user_id"] == self.user_id for h in hits))
