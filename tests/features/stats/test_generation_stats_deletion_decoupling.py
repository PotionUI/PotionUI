"""Proof that `generation_stats` is durable across generation deletion.

The requirement: removing a generation must not remove its stat row, but the
generation itself must still be fully removed. The mechanism is migration
091 (`generation_stats.generation_id` is a plain TEXT column with no FOREIGN
KEY / ON DELETE clause at all -- see that migration's docstring), so
`GenerationRepository.delete()`'s plain `DELETE FROM generations WHERE id = ?`
can never cascade into it.

Runs real SQL against a real (in-memory) SQLite database, mirroring the
pattern `tests/features/stats/test_repository.py` already uses: both
`GenerationRepository` and `GenerationStatsRepository` do
`from src.platform.database import db` at module import time, so the global
`mock_db`/`test_db` conftest fixtures (which patch
`src.platform.database.database.db`) don't reach either module's own,
already-bound `db` name -- each module's `db` attribute is patched directly
instead.

`generation_files` is included (with a real `ON DELETE CASCADE` FK, matching
the real schema) purely as the control: it proves the same deletion call
still fully cascades a generation's normal children, so "the generation
should be still fully removed" isn't broken by this table's absence of a FK.
"""

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import src.features.generation.repository as generation_repository_module
import src.features.stats.generation_stats_repository as generation_stats_repository_module
from src.features.generation.repository import GenerationRepository
from src.features.stats.generation_stats_repository import GenerationStatsRepository


class _MemoryDb:
    """Minimal stand-in for the global `db`: one shared in-memory connection
    with foreign keys enforced (SQLite disables them by default per-connection)."""

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
    id TEXT PRIMARY KEY, preset_id TEXT, status TEXT, created_at TIMESTAMP
);
CREATE TABLE generation_files (
    id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, file_id TEXT,
    FOREIGN KEY (generation_id) REFERENCES generations (id) ON DELETE CASCADE
);
CREATE TABLE generation_stats (
    id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, preset_id TEXT,
    preset_name TEXT, engine TEXT, backend_id TEXT, duration_ms INTEGER,
    cold_start INTEGER, model_load_ms REAL, peak_vram_mb REAL,
    peak_ram_mb REAL, cpu_percent REAL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _seed(db):
    with db.get_cursor() as c:
        c.executescript(_SCHEMA)


def test_deleting_generation_leaves_stat_row_but_fully_removes_generation():
    memory_db = _MemoryDb()
    _seed(memory_db)

    generation_id = "gen-deletion-decoupling-1"

    with memory_db.get_cursor() as c:
        c.execute(
            "INSERT INTO generations (id, preset_id, status, created_at) VALUES (?, ?, ?, ?)",
            (generation_id, "native/SDXL/base", "completed", "2026-07-17 10:00:00"),
        )
        c.execute(
            "INSERT INTO generation_files (id, generation_id, file_id) VALUES (?, ?, ?)",
            ("file-1", generation_id, "f-1"),
        )

    with patch.object(generation_stats_repository_module, "db", memory_db):
        GenerationStatsRepository().record_completion(
            generation_id=generation_id,
            preset_id="native/SDXL/base",
            preset_name="SDXL Base",
            engine="native",
            backend_id="backend-1",
            duration_ms=12345,
            cold_start=True,
            model_load_ms=4000.0,
            peak_vram_mb=8192.0,
            peak_ram_mb=16384.0,
            cpu_percent=42.5,
        )

    # Sanity: everything is present before deletion.
    with memory_db.get_cursor() as c:
        assert c.execute("SELECT COUNT(*) FROM generations WHERE id = ?", (generation_id,)).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM generation_files WHERE generation_id = ?", (generation_id,)).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM generation_stats WHERE generation_id = ?", (generation_id,)).fetchone()[0] == 1

    # Delete the generation the same way the real deletion path does
    # (GenerationRepository.delete -> plain `DELETE FROM generations`).
    with patch.object(generation_repository_module, "db", memory_db):
        deleted = GenerationRepository().delete(generation_id)
    assert deleted is True

    with memory_db.get_cursor() as c:
        # The generation is FULLY removed...
        assert c.execute("SELECT COUNT(*) FROM generations WHERE id = ?", (generation_id,)).fetchone()[0] == 0
        # ...and so are its normal (FK-cascaded) children -- proves this test's
        # deletion call is the real, complete deletion path, not a partial one.
        assert c.execute("SELECT COUNT(*) FROM generation_files WHERE generation_id = ?", (generation_id,)).fetchone()[0] == 0
        # ...but the durable stat row survives untouched.
        row = c.execute(
            "SELECT preset_id, preset_name, cold_start, duration_ms, peak_vram_mb "
            "FROM generation_stats WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        assert row is not None
        assert row["preset_id"] == "native/SDXL/base"
        assert row["preset_name"] == "SDXL Base"
        assert row["cold_start"] == 1
        assert row["duration_ms"] == 12345
        assert row["peak_vram_mb"] == 8192.0


def test_deleting_nonexistent_generation_still_leaves_orphaned_stat_row_alone():
    """A stat row for an id that was never a real generation (or whose
    generation was deleted long ago) is untouched by an unrelated delete --
    `generation_stats` has no relationship to `generations` at all, by design."""
    memory_db = _MemoryDb()
    _seed(memory_db)

    with patch.object(generation_stats_repository_module, "db", memory_db):
        GenerationStatsRepository().record_completion(
            generation_id="already-gone",
            preset_id="native/SDXL/base",
            preset_name="SDXL Base",
            engine="native",
            backend_id="backend-1",
            duration_ms=1000,
            cold_start=False,
            model_load_ms=None,
            peak_vram_mb=None,
            peak_ram_mb=None,
            cpu_percent=None,
        )

    with patch.object(generation_repository_module, "db", memory_db):
        deleted = GenerationRepository().delete("some-other-id-never-existed")
    assert deleted is False

    with memory_db.get_cursor() as c:
        assert c.execute(
            "SELECT COUNT(*) FROM generation_stats WHERE generation_id = ?", ("already-gone",)
        ).fetchone()[0] == 1
