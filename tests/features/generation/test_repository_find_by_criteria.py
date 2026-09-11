"""GenerationRepository.find_by_criteria: the criteria the History
'Delete by criteria' (owner-scoped) and admin housekeeping (every user)
flows filter on, AND-ed."""

import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.records import Generation, File
from src.features.generation.repository import GenerationRepository
from src.features.tags.repository import tag_repo
from src.platform.util.ids import generate_ulid


class TestFindByCriteria(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.user_a = self.create_test_user(user_id="user_a", username="a", email="a@example.com")
        self.user_b = self.create_test_user(user_id="user_b", username="b", email="b@example.com")

    def _make(self, user_id, status="completed", favorite=False):
        gen = self.repo.create(Generation(
            id=generate_ulid(),
            preset_id="native/SDXL/realistic",
            form_data={"prompt": "a prompt"},
            user_id=user_id,
            status=status,
        ))
        if favorite:
            self.repo.set_favorite(gen.id, True, user_id=user_id)
        return gen

    def _set_created_at(self, generation_id, value):
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE generations SET created_at = ? WHERE id = ?", (value, generation_id))

    def _attach_file(self, generation_id, user_id):
        self.repo.add_file(generation_id, File(
            file_path="/test/image.png", file_type="IMAGE", user_id=user_id, is_final=True,
        ))

    def _make_tag(self, user_id, name):
        return tag_repo.create_tag(name, type="GENERATION", user_id=user_id)

    def _tag(self, generation_id, tag_id):
        tag_repo.add_tag_to_generation(generation_id, tag_id)

    def test_matches_across_every_user_by_default(self):
        a = self._make(self.user_a)
        b = self._make(self.user_b)

        pairs = self.repo.find_by_criteria()

        self.assertEqual({p for p in pairs}, {(a.id, self.user_a), (b.id, self.user_b)})

    def test_user_id_scopes_to_one_owner(self):
        a = self._make(self.user_a)
        self._make(self.user_b)

        pairs = self.repo.find_by_criteria(user_id=self.user_a)

        self.assertEqual({p[0] for p in pairs}, {a.id})

    def test_older_than_days_excludes_recent_rows(self):
        old = self._make(self.user_a)
        self._set_created_at(old.id, "2020-01-01 00:00:00")
        recent = self._make(self.user_a)

        pairs = self.repo.find_by_criteria(older_than_days=30)

        self.assertEqual({p[0] for p in pairs}, {old.id})

    def test_created_date_range_is_inclusive_of_both_bounds(self):
        before = self._make(self.user_a)
        self._set_created_at(before.id, "2020-01-01 00:00:00")
        within = self._make(self.user_a)
        self._set_created_at(within.id, "2020-06-15 12:00:00")
        after = self._make(self.user_a)
        self._set_created_at(after.id, "2020-12-31 00:00:00")

        pairs = self.repo.find_by_criteria(created_from="2020-06-01", created_to="2020-06-30")

        self.assertEqual({p[0] for p in pairs}, {within.id})

    def test_without_media_excludes_generations_with_files(self):
        with_media = self._make(self.user_a)
        self._attach_file(with_media.id, self.user_a)
        without_media = self._make(self.user_a)

        pairs = self.repo.find_by_criteria(without_media=True)

        self.assertEqual({p[0] for p in pairs}, {without_media.id})

    def test_statuses_filters_to_the_given_set(self):
        failed = self._make(self.user_a, status="failed")
        completed = self._make(self.user_a, status="completed")

        pairs = self.repo.find_by_criteria(statuses=["failed"])

        self.assertEqual({p[0] for p in pairs}, {failed.id})

    def test_keep_favorites_excludes_favorited_rows(self):
        favorite = self._make(self.user_a, favorite=True)
        plain = self._make(self.user_a)

        pairs = self.repo.find_by_criteria(keep_favorites=True)

        self.assertEqual({p[0] for p in pairs}, {plain.id})

    def test_keep_favorites_false_includes_favorited_rows(self):
        favorite = self._make(self.user_a, favorite=True)

        pairs = self.repo.find_by_criteria(keep_favorites=False)

        self.assertIn(favorite.id, {p[0] for p in pairs})

    def test_running_and_pending_are_never_candidates(self):
        self._make(self.user_a, status="running")
        self._make(self.user_a, status="pending")

        pairs = self.repo.find_by_criteria(keep_favorites=False)

        self.assertEqual(pairs, [])

    def test_criteria_are_and_ed_together(self):
        old_failed = self._make(self.user_a, status="failed")
        self._set_created_at(old_failed.id, "2020-01-01 00:00:00")
        old_completed = self._make(self.user_a, status="completed")
        self._set_created_at(old_completed.id, "2020-01-01 00:00:00")
        recent_failed = self._make(self.user_a, status="failed")

        pairs = self.repo.find_by_criteria(older_than_days=30, statuses=["failed"])

        self.assertEqual({p[0] for p in pairs}, {old_failed.id})

    def test_tag_ids_requires_all_of_them(self):
        tag_x = self._make_tag(self.user_a, "x")
        tag_y = self._make_tag(self.user_a, "y")
        both = self._make(self.user_a)
        self._tag(both.id, tag_x.id)
        self._tag(both.id, tag_y.id)
        only_x = self._make(self.user_a)
        self._tag(only_x.id, tag_x.id)

        pairs = self.repo.find_by_criteria(tag_ids=[tag_x.id, tag_y.id])

        self.assertEqual({p[0] for p in pairs}, {both.id})

    def test_tag_ids_scoped_by_user_id_together(self):
        tag_x = self._make_tag(self.user_a, "x")
        a_gen = self._make(self.user_a)
        self._tag(a_gen.id, tag_x.id)
        b_gen = self._make(self.user_b)

        pairs = self.repo.find_by_criteria(user_id=self.user_a, tag_ids=[tag_x.id])

        self.assertEqual({p[0] for p in pairs}, {a_gen.id})
