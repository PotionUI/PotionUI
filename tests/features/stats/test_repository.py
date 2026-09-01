"""StatsRepository runs real SQL, so these tests run it against a real (in-memory) SQLite
database built by the real migrations, with real rows.

`stats_repository` resolves `db` at call time, so patching the canonical
`src.platform.database.database.db` reaches it.
"""

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from src.features.stats.repository import StatsRepository


class _MemoryDb:
    """Minimal stand-in for the global `db`: one shared in-memory connection."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def get_connection(self):
        yield self._conn

    @contextmanager
    def get_cursor(self):
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()


_SCHEMA = """
CREATE TABLE generations (
    id TEXT PRIMARY KEY, preset_id TEXT, preset_version TEXT, form_data TEXT,
    user_id TEXT, status TEXT, progress REAL, mode TEXT, prompt_state TEXT,
    rating INTEGER DEFAULT 0, is_favorite INTEGER DEFAULT 0, backend_id TEXT,
    duration_ms INTEGER, started_at TIMESTAMP,
    created_at TIMESTAMP, completed_at TIMESTAMP, updated_at TIMESTAMP
);
CREATE TABLE files (
    id TEXT PRIMARY KEY, file_path TEXT, file_type TEXT, file_size INTEGER,
    pipe_name TEXT, is_final BOOLEAN, user_id TEXT, created_at TIMESTAMP,
    width INTEGER, height INTEGER
);
CREATE TABLE generation_files (id TEXT PRIMARY KEY, generation_id TEXT, file_id TEXT);
CREATE TABLE models (id TEXT PRIMARY KEY, filename TEXT, model_type TEXT);
CREATE TABLE generation_models (id TEXT PRIMARY KEY, generation_id TEXT, model_id TEXT);
CREATE TABLE generation_parameters (
    id TEXT PRIMARY KEY, generation_id TEXT, parameter_name TEXT,
    parameter_value TEXT, parameter_index INTEGER DEFAULT 0
);
"""


def _seed(db):
    with db.get_cursor() as c:
        c.executescript(_SCHEMA)

        # Three generations on two days. g1/g2 completed with known durations; g3 has an
        # unknown duration (NULL), which must never be counted as zero.
        rows = [
            ('g1', 'preset-a', 'completed', 10000, '2026-01-01 10:00:00', '2026-01-01 10:00:10'),
            ('g2', 'preset-a', 'completed', 30000, '2026-01-01 11:00:00', '2026-01-01 11:00:30'),
            ('g3', 'preset-b', 'completed', None, '2026-01-02 09:00:00', None),
        ]
        for gid, preset, status, dur, created, completed in rows:
            c.execute(
                "INSERT INTO generations (id, preset_id, status, duration_ms, created_at, completed_at, mode, user_id)"
                " VALUES (?,?,?,?,?,?, 'txt2img', 'u1')",
                (gid, preset, status, dur, created, completed),
            )

        files = [
            ('f1', 'IMAGE', 1000, 512, 512), ('f2', 'IMAGE', 3000, 512, 512),
            ('f3', 'VIDEO', 6000, 1920, 1080),
        ]
        for fid, ftype, size, w, h in files:
            c.execute(
                "INSERT INTO files (id, file_type, file_size, width, height, created_at)"
                " VALUES (?,?,?,?,?, '2026-01-01 10:00:00')", (fid, ftype, size, w, h))
        for i, (gid, fid) in enumerate([('g1', 'f1'), ('g2', 'f2'), ('g3', 'f3')]):
            c.execute("INSERT INTO generation_files VALUES (?,?,?)", (f'gf{i}', gid, fid))

        c.execute("INSERT INTO models VALUES ('m1', 'sdxl.safetensors', 'checkpoint')")
        c.execute("INSERT INTO models VALUES ('m2', 'detail.safetensors', 'lora')")
        c.execute("INSERT INTO generation_models VALUES ('gm1', 'g1', 'm1')")
        c.execute("INSERT INTO generation_models VALUES ('gm2', 'g2', 'm1')")
        c.execute("INSERT INTO generation_models VALUES ('gm3', 'g2', 'm2')")

        # parameter_value is JSON-encoded. cfg is written as both "1" and "1.0" by different
        # presets -- the repository must merge them onto one numeric key.
        params = [
            ('p1', 'g1', 'sampler', '"euler"'), ('p2', 'g2', 'sampler', '"euler"'),
            ('p3', 'g3', 'sampler', '"dpmpp_2m"'),
            ('p4', 'g1', 'cfg', '"1"'), ('p5', 'g2', 'cfg', '"1.0"'), ('p6', 'g3', 'cfg', '"7.5"'),
            ('p7', 'g1', 'steps', '"20"'),
            ('p8', 'g1', 'resolution', '"512x512"'),
        ]
        for pid, gid, name, value in params:
            c.execute("INSERT INTO generation_parameters (id, generation_id, parameter_name, parameter_value)"
                      " VALUES (?,?,?,?)", (pid, gid, name, value))


@pytest.fixture
def repo():
    db = _MemoryDb()
    _seed(db)
    with patch('src.platform.database.database.db', db):
        yield StatsRepository()


class TestOverview:
    def test_counts_and_bytes(self, repo):
        o = repo.overview()
        assert o['total_generations'] == 3
        assert o['completed'] == 3
        assert o['total_outputs'] == 3
        assert o['total_bytes'] == 10000
        assert o['image_bytes'] == 4000
        assert o['video_bytes'] == 6000
        assert o['distinct_models'] == 2
        assert o['active_days'] == 2

    def test_unknown_duration_is_excluded_not_counted_as_zero(self, repo):
        o = repo.overview()
        # durations are 10s and 30s; g3 is unknown. Mean of the two known ones is 20s.
        assert o['avg_duration_ms'] == 20000
        assert o['median_duration_ms'] == 10000

    def test_date_range_filters(self, repo):
        assert repo.overview(date_from='2026-01-02')['total_generations'] == 1
        assert repo.overview(date_to='2026-01-01')['total_generations'] == 2
        assert repo.overview(date_from='2026-01-01', date_to='2026-01-01')['total_generations'] == 2

    def test_empty_range_yields_zeros_not_none(self, repo):
        o = repo.overview(date_from='2030-01-01')
        assert o['total_generations'] == 0
        assert o['total_bytes'] == 0
        assert o['median_duration_ms'] is None


class TestBreakdown:
    def test_preset_counts(self, repo):
        result = repo.breakdown('preset')
        assert result['total_distinct'] == 2
        assert result['items'][0] == {'key': 'preset-a', 'group': None, 'count': 2}

    def test_model_carries_its_type_as_group(self, repo):
        items = repo.breakdown('model')['items']
        by_key = {i['key']: i for i in items}
        assert by_key['sdxl.safetensors']['group'] == 'checkpoint'
        assert by_key['sdxl.safetensors']['count'] == 2
        assert by_key['detail.safetensors']['group'] == 'lora'

    def test_json_encoded_params_are_decoded(self, repo):
        items = repo.breakdown('sampler')['items']
        assert items[0] == {'key': 'euler', 'group': None, 'count': 2}

    def test_numeric_params_merge_equivalent_values(self, repo):
        # "1" and "1.0" are the same cfg and must not become two bars.
        items = repo.breakdown('cfg')['items']
        by_key = {i['key']: i['count'] for i in items}
        assert by_key == {'1': 2, '7.5': 1}
        assert repo.breakdown('cfg')['total_distinct'] == 2

    def test_integral_numeric_keys_render_without_decimal(self, repo):
        assert repo.breakdown('steps')['items'][0]['key'] == '20'

    def test_non_numeric_params_keep_their_text(self, repo):
        assert repo.breakdown('resolution')['items'][0]['key'] == '512x512'

    def test_limit_is_respected(self, repo):
        assert len(repo.breakdown('cfg', limit=1)['items']) == 1

    def test_unknown_dimension_raises_rather_than_interpolating(self, repo):
        with pytest.raises(ValueError):
            repo.breakdown("preset'; DROP TABLE generations;--")


class TestTimeseries:
    def test_count_by_day(self, repo):
        points = repo.timeseries(metric='count', bucket='day')
        assert points == [
            {'bucket': '2026-01-01', 'value': 2},
            {'bucket': '2026-01-02', 'value': 1},
        ]

    def test_bytes_by_day(self, repo):
        points = repo.timeseries(metric='bytes', bucket='day')
        assert points[0]['value'] == 4000
        assert points[1]['value'] == 6000

    def test_duration_excludes_unknown_rows_entirely(self, repo):
        points = repo.timeseries(metric='duration', bucket='day')
        # 2026-01-02 holds only g3, whose duration is unknown -> the day is absent,
        # rather than present with a misleading 0.
        assert points == [{'bucket': '2026-01-01', 'value': 20000.0}]

    def test_bad_metric_or_bucket_raises(self, repo):
        with pytest.raises(ValueError):
            repo.timeseries(metric='evil')
        with pytest.raises(ValueError):
            repo.timeseries(bucket='fortnight')


class TestDurations:
    def test_histogram_buckets_and_percentiles(self, repo):
        d = repo.durations()
        counts = {(b['lower_s'], b['upper_s']): b['count'] for b in d['buckets']}
        assert counts[(10, 20)] == 1   # g1 at 10s
        assert counts[(30, 60)] == 1   # g2 at 30s
        assert d['p50_ms'] == 10000
        assert d['unknown'] == 1       # g3

    def test_buckets_are_fixed_regardless_of_filter(self, repo):
        all_edges = [(b['lower_s'], b['upper_s']) for b in repo.durations()['buckets']]
        filtered = [(b['lower_s'], b['upper_s']) for b in repo.durations(date_from='2026-01-02')['buckets']]
        assert all_edges == filtered


class TestStorage:
    def test_by_type_totals(self, repo):
        by_type = {t['file_type']: t for t in repo.storage()['by_type']}
        assert by_type['IMAGE'] == {'file_type': 'IMAGE', 'count': 2, 'bytes': 4000}
        assert by_type['VIDEO']['bytes'] == 6000

    def test_over_time_splits_image_and_video(self, repo):
        rows = repo.storage(bucket='day')['over_time']
        assert rows[0] == {'bucket': '2026-01-01', 'image_bytes': 4000, 'video_bytes': 0}
        assert rows[1] == {'bucket': '2026-01-02', 'image_bytes': 0, 'video_bytes': 6000}

    def test_top_resolutions(self, repo):
        res = repo.storage()['top_resolutions']
        assert res[0]['label'] == '512x512'
        assert res[0]['count'] == 2

    def test_average_file_size(self, repo):
        assert repo.storage()['avg_file_bytes'] == 10000 // 3
