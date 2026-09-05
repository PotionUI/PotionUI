"""Ordering and membership-filter behaviour of GenerationRepository.get_all /
count_by_status: the deterministic sort tie-breaker, tag/collection semi-joins
(which must not fan out into duplicate rows or inflated counts), date-filter
boundaries, and the index the default history page is expected to use.
"""

import os
import sys
from contextlib import contextmanager

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.records import Generation, File
from src.features.generation.repository import GenerationRepository

# Insertion order deliberately disagrees with id order, so a result that comes
# back id-sorted can only have been sorted, not scanned in the order written.
_IDS = ['gen-c', 'gen-a', 'gen-e', 'gen-b', 'gen-d']
_FIXED_CREATED_AT = '2026-03-01 09:00:00'


class _SpyCursor:
    """sqlite3.Cursor is a C type and takes no attribute assignment, so the SQL
    the repository really runs is recorded through a proxy instead."""

    def __init__(self, cursor, log):
        self._cursor = cursor
        self._log = log

    def execute(self, sql, params=()):
        self._log.append((sql, list(params)))
        return self._cursor.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class TestGenerationOrderingAndMembership(PersistenceTestBase):

    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.user_id = self.create_test_user()

    # --- helpers ---

    def _make(self, generation_id, prompt="a prompt", status="completed"):
        return self.repo.create(Generation(
            id=generation_id,
            preset_id="native/SDXL/realistic",
            form_data={"prompt": prompt},
            user_id=self.user_id,
            status=status,
        ))

    def _set_created_at(self, generation_id, value):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE generations SET created_at = ? WHERE id = ?",
                (value, generation_id),
            )

    def _tied_rows(self):
        """Five generations that share one created_at and one completed_at."""
        for generation_id in _IDS:
            self._make(generation_id)
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE generations SET created_at = ?, completed_at = ?, "
                    "rating = 3 WHERE id = ?",
                    (_FIXED_CREATED_AT, _FIXED_CREATED_AT, generation_id),
                )

    def _add_tag(self, generation_id, tag_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO tags (id, name, type, user_id) VALUES (?, ?, 'GENERATION', ?)",
                (tag_id, tag_id, self.user_id),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO generation_tags (generation_id, tag_id) VALUES (?, ?)",
                (generation_id, tag_id),
            )

    def _add_to_collection(self, generation_id, collection_id):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO collections (id, name, user_id) VALUES (?, ?, ?)",
                (collection_id, collection_id, self.user_id),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO collection_generations (collection_id, generation_id) "
                "VALUES (?, ?)",
                (collection_id, generation_id),
            )

    @contextmanager
    def _capture_sql(self):
        log = []
        real_get_cursor = self.db.get_cursor

        @contextmanager
        def spy():
            with real_get_cursor() as cursor:
                yield _SpyCursor(cursor, log)

        self.db.get_cursor = spy
        try:
            yield log
        finally:
            self.db.get_cursor = real_get_cursor

    # --- tie-breaker determinism ---

    def test_tied_created_at_orders_by_id_descending(self):
        self._tied_rows()

        # Unscoped as well as user-scoped: the user-scoped page is served straight
        # off idx_generations_user_created_id, which would hand back id order even
        # without a tie-breaker in the ORDER BY. The unscoped listing has to sort.
        for scope in ({'user_id': self.user_id}, {}):
            with self.subTest(scope=scope):
                ids = [g.id for g in self.repo.get_all(sort_dir="desc", **scope)]
                self.assertEqual(ids, sorted(_IDS, reverse=True))

    def test_tied_created_at_orders_by_id_ascending(self):
        self._tied_rows()

        for scope in ({'user_id': self.user_id}, {}):
            with self.subTest(scope=scope):
                ids = [g.id for g in self.repo.get_all(sort_dir="asc", **scope)]
                self.assertEqual(ids, sorted(_IDS))

    def test_every_whitelisted_sort_breaks_ties_by_id(self):
        self._tied_rows()

        for sort_by in ('created_at', 'completed_at', 'rating', 'file_size'):
            for sort_dir, expected in (
                ('desc', sorted(_IDS, reverse=True)),
                ('asc', sorted(_IDS)),
            ):
                with self.subTest(sort_by=sort_by, sort_dir=sort_dir):
                    ids = [
                        g.id for g in self.repo.get_all(
                            user_id=self.user_id, sort_by=sort_by, sort_dir=sort_dir
                        )
                    ]
                    self.assertEqual(ids, expected)

    def test_paging_over_tied_rows_visits_each_row_once(self):
        self._tied_rows()

        for scope in ({'user_id': self.user_id}, {}):
            with self.subTest(scope=scope):
                paged = []
                for offset in range(0, len(_IDS), 2):
                    paged.extend(
                        g.id for g in self.repo.get_all(limit=2, offset=offset, **scope)
                    )
                self.assertEqual(paged, sorted(_IDS, reverse=True))

    # --- tag membership ---

    def test_multiple_tags_require_all_of_them(self):
        both = self._make('gen-both')
        one = self._make('gen-one')
        self._add_tag(both.id, 'tag-red')
        self._add_tag(both.id, 'tag-blue')
        self._add_tag(one.id, 'tag-red')

        results = self.repo.get_all(user_id=self.user_id, tag_ids=['tag-red', 'tag-blue'])

        self.assertEqual([g.id for g in results], [both.id])

    def test_tag_filter_count_matches_the_listing(self):
        both = self._make('gen-both')
        one = self._make('gen-one')
        for tag_id in ('tag-red', 'tag-blue'):
            self._add_tag(both.id, tag_id)
        self._add_tag(one.id, 'tag-red')

        for tag_ids in (['tag-red'], ['tag-red', 'tag-blue']):
            with self.subTest(tag_ids=tag_ids):
                listed = self.repo.get_all(user_id=self.user_id, tag_ids=tag_ids)
                total = self.repo.count_by_status(user_id=self.user_id, tag_ids=tag_ids)
                self.assertEqual(total, len(listed))

    def test_single_tag_on_many_generations_lists_each_once(self):
        for generation_id in ('gen-a', 'gen-b', 'gen-c'):
            self._make(generation_id)
            self._add_tag(generation_id, 'tag-red')

        results = self.repo.get_all(user_id=self.user_id, tag_ids=['tag-red'])

        self.assertEqual(len(results), 3)
        self.assertEqual(len({g.id for g in results}), 3)

    # --- collection membership ---

    def test_generation_in_several_collections_appears_once(self):
        many = self._make('gen-many')
        other = self._make('gen-other')
        for collection_id in ('col-1', 'col-2', 'col-3'):
            self._add_to_collection(many.id, collection_id)
        self._add_to_collection(other.id, 'col-2')

        results = self.repo.get_all(user_id=self.user_id, collection_id='col-2')

        self.assertEqual([g.id for g in results], sorted([many.id, other.id], reverse=True))
        self.assertEqual(len(results), 2)

    def test_collection_count_matches_the_listing(self):
        many = self._make('gen-many')
        for collection_id in ('col-1', 'col-2', 'col-3'):
            self._add_to_collection(many.id, collection_id)

        listed = self.repo.get_all(user_id=self.user_id, collection_id='col-1')
        total = self.repo.count_by_status(user_id=self.user_id, collection_id='col-1')

        self.assertEqual(total, 1)
        self.assertEqual(total, len(listed))

    def test_tag_and_collection_filters_combine(self):
        both = self._make('gen-both')
        tagged_only = self._make('gen-tagged')
        self._add_tag(both.id, 'tag-red')
        self._add_tag(tagged_only.id, 'tag-red')
        self._add_to_collection(both.id, 'col-1')
        self._add_to_collection(both.id, 'col-2')

        results = self.repo.get_all(
            user_id=self.user_id, tag_ids=['tag-red'], collection_id='col-1'
        )
        total = self.repo.count_by_status(
            user_id=self.user_id, tag_ids=['tag-red'], collection_id='col-1'
        )

        self.assertEqual([g.id for g in results], [both.id])
        self.assertEqual(total, 1)

    def test_media_type_filter_lists_a_multi_file_generation_once(self):
        gen = self._make('gen-many-files')
        for name in ('a.png', 'b.png', 'c.png'):
            self.repo.add_file(gen.id, File(
                file_path=f"/test/{name}", file_type="IMAGE",
                user_id=self.user_id, is_final=True,
            ))

        results = self.repo.get_all(user_id=self.user_id, media_type="image")
        total = self.repo.count_by_status(user_id=self.user_id, media_type="image")

        self.assertEqual([g.id for g in results], [gen.id])
        self.assertEqual(total, 1)

    # --- date boundaries ---

    def _dated_rows(self):
        stamps = {
            'gen-start': '2026-01-01 00:00:00',
            'gen-noon': '2026-01-01 12:00:00',
            'gen-end': '2026-01-01 23:59:59',
            'gen-next': '2026-01-02 00:00:00',
        }
        for generation_id, stamp in stamps.items():
            self._make(generation_id)
            self._set_created_at(generation_id, stamp)
        return stamps

    def test_date_only_bounds_cover_the_whole_day(self):
        self._dated_rows()

        results = self.repo.get_all(
            user_id=self.user_id, created_from='2026-01-01', created_to='2026-01-01'
        )

        self.assertEqual(
            {g.id for g in results}, {'gen-start', 'gen-noon', 'gen-end'}
        )

    def test_timestamp_bounds_are_inclusive(self):
        self._dated_rows()

        results = self.repo.get_all(
            user_id=self.user_id,
            created_from='2026-01-01 12:00:00',
            created_to='2026-01-01 23:59:59',
        )

        self.assertEqual({g.id for g in results}, {'gen-noon', 'gen-end'})

    def test_date_filter_count_matches_the_listing(self):
        self._dated_rows()

        listed = self.repo.get_all(
            user_id=self.user_id, created_from='2026-01-01', created_to='2026-01-01'
        )
        total = self.repo.count_by_status(
            user_id=self.user_id, created_from='2026-01-01', created_to='2026-01-01'
        )

        self.assertEqual(total, len(listed))

    # --- query plan ---

    def _plan_for(self, **kwargs):
        with self._capture_sql() as log:
            self.repo.get_all(user_id=self.user_id, limit=50, offset=0, **kwargs)
        sql, params = log[0]
        with self.db.get_cursor() as cursor:
            cursor.execute(f"EXPLAIN QUERY PLAN {sql}", params)
            return sql, [row[3] for row in cursor.fetchall()]

    def test_default_history_page_uses_the_user_created_id_index(self):
        self._tied_rows()

        _, plan = self._plan_for()

        self.assertTrue(
            any('idx_generations_user_created_id' in step for step in plan),
            f"plan did not use the index: {plan}",
        )

    def test_default_history_page_needs_no_sorter(self):
        self._tied_rows()

        _, plan = self._plan_for()

        self.assertFalse(
            any('TEMP B-TREE' in step.upper() for step in plan),
            f"plan still sorts: {plan}",
        )
