"""Persistence coverage for the reset Segment library domain."""

import sqlite3
from unittest.mock import patch

from src.features.segments.dto import (
    RichSegment,
    SavedSegment,
    SegmentCategory,
    SegmentTemplate,
)
from src.features.segments.repository import (
    SavedSegmentRepository,
    SegmentCategoryRepository,
    SegmentTemplateRepository,
)
from src.platform.util.ids import generate_ulid
from tests.fixtures.persistence_base import PersistenceTestBase


def _chip(chip_id: str = "chip-1") -> dict:
    return {
        "id": chip_id,
        "categoryPath": "lighting.mood",
        "valueId": "value-1",
        "label": "Golden hour",
        "value": "warm golden-hour lighting",
        "allValues": [
            {
                "id": "value-1",
                "label": "Golden hour",
                "value": "warm golden-hour lighting",
                "preview_file_id": "preview-1",
            }
        ],
        "shuffle": True,
        "autoRegen": True,
    }


class TestSegmentLibraryRepository(PersistenceTestBase):
    def setUp(self):
        super().setUp()
        import src.features.segments.repository as repository_module

        repository_module.db = self.db
        self.categories = SegmentCategoryRepository()
        self.segments = SavedSegmentRepository()
        self.templates = SegmentTemplateRepository()
        self.user_1 = self.create_test_user(
            "segment-user-1", "segmentuser1", "segment1@example.com"
        )
        self.user_2 = self.create_test_user(
            "segment-user-2", "segmentuser2", "segment2@example.com"
        )

    def test_defaults_are_seeded_lazily_and_scoped_per_user(self):
        user_1_categories = self.categories.get_all(self.user_1)
        user_2_categories = self.categories.get_all(self.user_2)

        self.assertEqual(len(user_1_categories), 4)
        self.assertEqual(len(user_2_categories), 4)
        self.assertTrue(all(item.user_id == self.user_1 for item in user_1_categories))
        self.assertTrue(all(item.user_id == self.user_2 for item in user_2_categories))
        self.assertTrue(
            {item.id for item in user_1_categories}.isdisjoint(
                {item.id for item in user_2_categories}
            )
        )

        # Lazy seeding must not recreate a default after the user renames it.
        renamed = user_1_categories[0]
        old_name = renamed.name
        self.categories.update(
            renamed.id,
            SegmentCategory(
                id=renamed.id,
                user_id=self.user_1,
                name="My renamed category",
                description=renamed.description,
                color=renamed.color,
                created_at=renamed.created_at,
            ),
            self.user_1,
        )
        names = {item.name for item in self.categories.get_all(self.user_1)}
        self.assertIn("My renamed category", names)
        self.assertNotIn(old_name, names)
        self.assertEqual(len(names), 4)

    def test_saved_segments_are_isolated_and_copy_the_effective_category_color(self):
        category_1 = self.categories.get_all(self.user_1)[0]
        category_2 = self.categories.get_all(self.user_2)[0]

        segment_1 = self.segments.create(
            SavedSegment(
                id=generate_ulid(),
                user_id=self.user_1,
                category_id=category_1.id,
                name="Lighting block",
                content="#[lighting mood]",
                chips={"chip-1": _chip()},
                enabled=False,
                description="Reusable light",
                tags=["light"],
            )
        )
        segment_2 = self.segments.create(
            SavedSegment(
                id=generate_ulid(),
                user_id=self.user_2,
                category_id=category_2.id,
                name="Lighting block",
                content="other user's content",
            )
        )

        assert segment_1 is not None
        assert segment_2 is not None
        self.assertEqual(segment_1.effective_color, category_1.color)
        self.assertIsNone(segment_1.color)
        self.assertFalse(segment_1.enabled)
        self.assertTrue(segment_1.chips["chip-1"].shuffle)
        self.assertTrue(segment_1.chips["chip-1"].autoRegen)
        self.assertIsNone(self.segments.get_by_id(segment_1.id, self.user_2))
        self.assertFalse(self.segments.delete(segment_1.id, self.user_2))

        # Database RESTRICT is the final guard behind the manager's explicit check.
        with self.assertRaises(sqlite3.IntegrityError):
            self.categories.delete(category_1.id, self.user_1)
        self.assertTrue(self.segments.delete(segment_1.id, self.user_1))
        self.assertTrue(self.categories.delete(category_1.id, self.user_1))

    def test_saved_segment_names_are_unique_per_user_not_globally(self):
        category_1 = self.categories.get_all(self.user_1)[0]
        category_2 = self.categories.get_all(self.user_2)[0]
        first = SavedSegment(
            id=generate_ulid(),
            user_id=self.user_1,
            category_id=category_1.id,
            name="Subject",
        )
        self.segments.create(first)
        with self.assertRaises(sqlite3.IntegrityError):
            self.segments.create(
                first.model_copy(update={"id": generate_ulid(), "name": "subject"})
            )

        # The same case-insensitive name belongs to a different namespace here.
        other = self.segments.create(
            SavedSegment(
                id=generate_ulid(),
                user_id=self.user_2,
                category_id=category_2.id,
                name="subject",
            )
        )
        self.assertIsNotNone(other)

    def test_template_round_trip_and_atomic_ordered_replacement(self):
        original = self.templates.create(
            SegmentTemplate(
                id=generate_ulid(),
                user_id=self.user_1,
                name="Two-shot template",
                description="Original",
                tags=["shots"],
                segments=[
                    RichSegment(
                        type="content",
                        content="portrait, #lighting.mood",
                        chips={"chip-1": _chip()},
                        enabled=True,
                        name="Opening",
                        color="#123456",
                        description="Starter content",
                    ),
                    RichSegment(type="break", enabled=False, name="Pause"),
                ],
            )
        )
        self.assertEqual([item.type for item in original.segments], ["content", "break"])
        self.assertEqual(original.segments[0].chips["chip-1"].valueId, "value-1")
        self.assertFalse(original.segments[1].enabled)

        original_ids = [item.id for item in original.segments]
        replacement = original.model_copy(
            update={
                "name": "Replaced template",
                "segments": [
                    RichSegment(type="break", enabled=True, name="First"),
                    RichSegment(content="new ending", name="Second"),
                ],
            }
        )
        updated = self.templates.update(original.id, replacement, self.user_1)
        assert updated is not None
        self.assertEqual(updated.name, "Replaced template")
        self.assertEqual([item.name for item in updated.segments], ["First", "Second"])
        self.assertTrue(
            set(original_ids).isdisjoint({item.id for item in updated.segments})
        )

        # Force the second child INSERT to violate its primary key.  Parent and
        # deleted children must both roll back as one transaction.
        before_failure = self.templates.get_by_id(original.id, self.user_1)
        assert before_failure is not None
        failing = before_failure.model_copy(
            update={
                "name": "Must roll back",
                "segments": [RichSegment(content="one"), RichSegment(content="two")],
            }
        )
        with patch(
            "src.features.segments.repository.generate_ulid",
            return_value="same-child-id",
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                self.templates.update(original.id, failing, self.user_1)

        after_failure = self.templates.get_by_id(original.id, self.user_1)
        assert after_failure is not None
        self.assertEqual(after_failure.name, before_failure.name)
        self.assertEqual(
            [item.id for item in after_failure.segments],
            [item.id for item in before_failure.segments],
        )
        self.assertEqual(
            [item.content for item in after_failure.segments],
            [item.content for item in before_failure.segments],
        )

    def test_template_reads_and_mutations_are_user_scoped(self):
        template = self.templates.create(
            SegmentTemplate(
                id=generate_ulid(),
                user_id=self.user_1,
                name="Private template",
                segments=[RichSegment(content="private")],
            )
        )
        self.assertIsNone(self.templates.get_by_id(template.id, self.user_2))
        self.assertFalse(self.templates.delete(template.id, self.user_2))
        self.assertIsNotNone(self.templates.get_by_id(template.id, self.user_1))
