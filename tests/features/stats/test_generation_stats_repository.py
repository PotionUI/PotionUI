"""GenerationStatsRepository runs real SQL against a real (in-memory) SQLite
database, mirroring `tests/features/stats/test_repository.py`'s pattern: the
module does `from src.platform.database import db` at import time, so the
module's OWN `db` attribute is patched directly (see that file's docstring
for why `unittest.mock.patch('src.platform.database.database.db', ...)`
would not reach it).
"""

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest

import src.features.stats.generation_stats_repository as generation_stats_repository_module
from src.features.stats.generation_stats_repository import (
    MAX_LIMIT,
    GenerationStatsRepository,
    clamp_limit,
)


class _MemoryDb:
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
CREATE TABLE generation_stats (
    id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, preset_id TEXT,
    preset_name TEXT, engine TEXT, backend_id TEXT, duration_ms INTEGER,
    cold_start INTEGER, model_load_ms REAL, peak_vram_mb REAL,
    peak_ram_mb REAL, cpu_percent REAL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def repo():
    memory_db = _MemoryDb()
    with memory_db.get_cursor() as c:
        c.executescript(_SCHEMA)
    with patch.object(generation_stats_repository_module, "db", memory_db):
        yield GenerationStatsRepository()


class TestRecordCompletion:
    def test_writes_one_row_with_all_fields(self, repo):
        repo.record_completion(
            generation_id="g1", preset_id="native/SDXL/base", preset_name="SDXL Base",
            engine="native", backend_id="b1", duration_ms=5000, cold_start=True,
            model_load_ms=1500.0, peak_vram_mb=8000.0, peak_ram_mb=12000.0, cpu_percent=55.0,
        )
        assert repo.count() == 1

    def test_none_resource_fields_stay_null(self, repo):
        """A non-native/no-lease generation captures nothing resource-side --
        NULL, never a guessed 0/false."""
        repo.record_completion(
            generation_id="g1", preset_id="p1", preset_name="P1", engine="comfyui",
            backend_id="b1", duration_ms=5000, cold_start=None, model_load_ms=None,
            peak_vram_mb=None, peak_ram_mb=None, cpu_percent=None,
        )
        items = repo.preset_timing()
        assert items[0]["cold_runs"] == 0
        assert items[0]["warm_runs"] == 0
        assert items[0]["total_runs"] == 1  # still counted, just unclassified

    def test_cold_start_bool_stored_as_int(self, repo):
        repo.record_completion(
            generation_id="g1", preset_id="p1", preset_name="P1", engine="native",
            backend_id="b1", duration_ms=1000, cold_start=False, model_load_ms=None,
            peak_vram_mb=None, peak_ram_mb=None, cpu_percent=None,
        )
        with repo_db(repo).get_cursor() as c:
            row = c.execute("SELECT cold_start FROM generation_stats").fetchone()
        assert row["cold_start"] == 0


def repo_db(repo):
    return generation_stats_repository_module.db


class TestPresetTiming:
    def _seed(self, repo):
        rows = [
            ("g1", "preset-a", "SDXL", True, 20000, 5000.0),
            ("g2", "preset-a", "SDXL", False, 8000, None),
            ("g3", "preset-a", "SDXL", False, 7000, None),
            ("g4", "preset-b", "Flux", True, 40000, 15000.0),
        ]
        for gid, preset, name, cold, dur, load_ms in rows:
            repo.record_completion(
                generation_id=gid, preset_id=preset, preset_name=name, engine="native",
                backend_id="b1", duration_ms=dur, cold_start=cold, model_load_ms=load_ms,
                peak_vram_mb=None, peak_ram_mb=None, cpu_percent=None,
            )

    def test_counts_and_averages_split_by_cold_warm(self, repo):
        self._seed(repo)
        items = {i["preset_id"]: i for i in repo.preset_timing()}

        preset_a = items["preset-a"]
        assert preset_a["total_runs"] == 3
        assert preset_a["cold_runs"] == 1
        assert preset_a["warm_runs"] == 2
        assert preset_a["preset_name"] == "SDXL"
        assert preset_a["avg_cold_duration_ms"] == 20000
        assert preset_a["avg_warm_duration_ms"] == 7500  # (8000+7000)/2

        preset_b = items["preset-b"]
        assert preset_b["cold_runs"] == 1
        assert preset_b["avg_model_load_ms"] == 15000.0

    def test_ranked_by_total_runs_desc(self, repo):
        self._seed(repo)
        items = repo.preset_timing()
        assert items[0]["preset_id"] == "preset-a"  # 3 runs > preset-b's 1

    def test_limit_bounds_rows(self, repo):
        self._seed(repo)
        items = repo.preset_timing(limit=1)
        assert len(items) == 1


class TestPresetResources:
    def test_peak_is_max_avg_is_mean(self, repo):
        repo.record_completion(
            generation_id="g1", preset_id="p1", preset_name="P1", engine="native",
            backend_id="b1", duration_ms=1000, cold_start=True, model_load_ms=None,
            peak_vram_mb=4000.0, peak_ram_mb=8000.0, cpu_percent=20.0,
        )
        repo.record_completion(
            generation_id="g2", preset_id="p1", preset_name="P1", engine="native",
            backend_id="b1", duration_ms=1000, cold_start=False, model_load_ms=None,
            peak_vram_mb=6000.0, peak_ram_mb=10000.0, cpu_percent=40.0,
        )
        item = repo.preset_resources()[0]
        assert item["peak_vram_mb"] == 6000.0
        assert item["avg_vram_mb"] == 5000.0
        assert item["peak_ram_mb"] == 10000.0
        assert item["avg_cpu_percent"] == 30.0

    def test_all_null_resources_report_null_not_zero(self, repo):
        repo.record_completion(
            generation_id="g1", preset_id="p1", preset_name="P1", engine="comfyui",
            backend_id="b1", duration_ms=1000, cold_start=None, model_load_ms=None,
            peak_vram_mb=None, peak_ram_mb=None, cpu_percent=None,
        )
        item = repo.preset_resources()[0]
        assert item["peak_vram_mb"] is None
        assert item["avg_vram_mb"] is None


class TestClampLimit:
    def test_none_uses_default(self):
        assert clamp_limit(None) == 10

    def test_clamps_to_max(self):
        assert clamp_limit(999999) == MAX_LIMIT

    def test_clamps_negative_to_one(self):
        assert clamp_limit(-5) == 1
