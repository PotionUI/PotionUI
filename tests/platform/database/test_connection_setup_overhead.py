"""Measures whether per-call SQLite connection setup (``sqlite3.connect`` plus
the four PRAGMAs ``Database.get_connection`` applies on every open) is
material against SQL execution time on the generation-history hot read paths.

MEASURE-FIRST: this is the evidence a bounded connection-lifecycle change
would be justified by. It asserts on connection-open *counts* (so a future
change to the call graph is caught) and prints a timing table; it does not
assert on absolute timings, which are noisy on a shared CI box.
"""

import statistics
import sys
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import sqlite3

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.history_query import GenerationHistoryQuery
from src.platform.util.ids import generate_ulid

ROW_COUNT = 3000
FILES_PER_GENERATION = 2
PAGE_SIZE = 50
REPS = 15
# Above this share of a unit's wall time, connection setup is "material"
# enough to justify a bounded lifecycle change (a thread-local/unit-of-work
# connection) instead of leaving `database.py` untouched.
MATERIALITY_THRESHOLD = 0.20


class _RecordingConn:
    """Proxies a real `sqlite3.Connection`, timing only its PRAGMA calls.

    `sqlite3.Connection` is a C type - its methods can't be monkeypatched on
    the instance or the class (see `tests/platform/database/test_database.py`
    for the same constraint) - so this wraps the instance instead. `__setattr__`
    forwards to the real connection so `conn.row_factory = sqlite3.Row`
    (set by `Database.get_connection`) still takes effect on it, not on this
    proxy - otherwise every row read through this connection would come back
    as a plain tuple instead of a `sqlite3.Row`.
    """

    def __init__(self, real_conn, probe):
        object.__setattr__(self, "_real", real_conn)
        object.__setattr__(self, "_probe", probe)

    def execute(self, sql, *args, **kwargs):
        if isinstance(sql, str) and sql.strip().upper().startswith("PRAGMA"):
            t0 = time.perf_counter()
            try:
                return self._real.execute(sql, *args, **kwargs)
            finally:
                self._probe.pragma_time += time.perf_counter() - t0
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


class ConnectionSetupProbe:
    """Counts `sqlite3.connect` calls and times connect() and PRAGMA setup
    separately from everything else a unit of work does (SQL execution,
    Python-side hydration, ...)."""

    def __init__(self):
        self.opens = 0
        self.connect_time = 0.0
        self.pragma_time = 0.0

    @property
    def setup_time(self) -> float:
        return self.connect_time + self.pragma_time

    @contextmanager
    def measure(self):
        real_connect = sqlite3.connect

        def timed_connect(*args, **kwargs):
            t0 = time.perf_counter()
            conn = real_connect(*args, **kwargs)
            self.connect_time += time.perf_counter() - t0
            self.opens += 1
            return _RecordingConn(conn, self)

        with patch("src.platform.database.database.sqlite3.connect", side_effect=timed_connect):
            yield self


def _run_unit(fn, reps: int):
    """Run `fn()` `reps` times, each under its own probe.

    Returns (opens per call - constant, expected - , list of wall times,
    list of setup times).
    """
    opens_per_call = None
    walls = []
    setups = []
    for _ in range(reps):
        probe = ConnectionSetupProbe()
        with probe.measure():
            t0 = time.perf_counter()
            fn()
            walls.append(time.perf_counter() - t0)
        setups.append(probe.setup_time)
        if opens_per_call is None:
            opens_per_call = probe.opens
        else:
            assert probe.opens == opens_per_call, (
                f"connection-open count changed between reps: {opens_per_call} vs {probe.opens}"
            )
    return opens_per_call, walls, setups


class TestConnectionSetupOverhead(PersistenceTestBase):
    """Bounded synthetic timing harness for PERF-05.

    Seeds a scratch database (never the live one - see `PersistenceTestBase`)
    with a few thousand generations and measures four representative history
    read units: a full history list page, its count, `history_version`, and a
    `matching_generation_ids` batch.
    """

    def setUp(self):
        super().setUp()
        self.repo = GenerationRepository()
        self.query = GenerationHistoryQuery(self.repo)
        self.user_id = self.create_test_user()
        self._seed(ROW_COUNT)

    def _seed(self, count: int) -> None:
        """Bulk-insert synthetic rows through one connection - this is fixture
        setup, not part of what's measured, so it deliberately bypasses the
        per-row repository calls (and their per-call connection opens)."""
        generation_ids = [generate_ulid() for _ in range(count)]
        file_ids = [
            generate_ulid()
            for _ in range(count * FILES_PER_GENERATION)
        ]

        with self.db.get_cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO generations (
                    id, preset_id, preset_version, form_data, user_id, status,
                    progress, mode, prompt_state, backend_id, tab_id, form_name,
                    source_prompt_id
                ) VALUES (?, ?, ?, ?, ?, 'completed', 1.0, 'txt2img', NULL, NULL, NULL, NULL, NULL)
                """,
                [
                    (gid, "native/SDXL/realistic", "1.0", '{"prompt": "a synthetic prompt"}', self.user_id)
                    for gid in generation_ids
                ],
            )

            file_rows = []
            link_rows = []
            for i, gid in enumerate(generation_ids):
                for j in range(FILES_PER_GENERATION):
                    fid = file_ids[i * FILES_PER_GENERATION + j]
                    file_rows.append((fid, f"{gid}/{j}.png", "IMAGE", self.user_id, 1024))
                    link_rows.append((generate_ulid(), gid, fid))

            cursor.executemany(
                """
                INSERT INTO files (id, file_path, file_type, user_id, file_size)
                VALUES (?, ?, ?, ?, ?)
                """,
                file_rows,
            )
            cursor.executemany(
                "INSERT INTO generation_files (id, generation_id, file_id) VALUES (?, ?, ?)",
                link_rows,
            )

            # Steady state: almost every real install has a history_revisions
            # row for an active user (every mutation bumps it - see
            # history_revision_repository). Seed one directly so
            # `history_version` measures its normal one-connection lookup
            # rather than the no-row-yet fallback aggregate over `generations`.
            cursor.execute(
                "INSERT INTO history_revisions (user_id, revision, updated_at) "
                "VALUES (?, 1, CURRENT_TIMESTAMP)",
                (self.user_id,),
            )

        self._generation_ids = generation_ids

    # --- The measurement itself -------------------------------------------

    def test_connection_setup_share_of_hot_read_paths(self):
        units = {
            "list page (get_history)": lambda: self.query.get_history(
                user_id=self.user_id, limit=PAGE_SIZE, offset=0, include_tags=True,
            ),
            "count_by_status": lambda: self.repo.count_by_status(user_id=self.user_id),
            "history_version": lambda: self.repo.history_version(self.user_id),
            "matching_generation_ids (1500-id batch)": lambda: self.repo.matching_generation_ids(
                self._generation_ids[:1500], user_id=self.user_id,
            ),
        }

        # Isolated per-open setup cost, measured directly against the same
        # database file, independent of any repository call.
        baseline_probe = ConnectionSetupProbe()
        with baseline_probe.measure():
            for _ in range(REPS * 4):
                with self.db.get_connection():
                    pass
        baseline_setup_per_open = baseline_probe.setup_time / baseline_probe.opens

        rows = []
        for name, fn in units.items():
            opens, walls, setups = _run_unit(fn, REPS)
            median_wall = statistics.median(walls)
            median_setup = statistics.median(setups)
            share = median_setup / median_wall if median_wall else 0.0
            rows.append((name, opens, median_wall, median_setup, share))

        self._print_table(baseline_setup_per_open, rows)

        # --- Assertions on the shape of the call graph, not on timings ---
        opens_by_name = {name: opens for name, opens, *_ in rows}
        self.assertEqual(
            opens_by_name["list page (get_history)"], 4,
            "a history list page should open exactly 4 connections "
            "(get_all, files bulk, tags bulk, count_by_status) - "
            "if this changed, the units below and the PERF-05 decision "
            "need re-deriving from fresh numbers",
        )
        self.assertEqual(opens_by_name["count_by_status"], 1)
        self.assertEqual(opens_by_name["history_version"], 1)
        self.assertEqual(
            opens_by_name["matching_generation_ids (1500-id batch)"], 1,
            "matching_generation_ids batches ids under one connection already - "
            "must stay one open even though 1500 ids span two _ID_FILTER_BATCH chunks",
        )

    def _print_table(self, baseline_setup_per_open: float, rows) -> None:
        out = sys.stderr
        print(f"\n--- PERF-05 connection-setup measurement ({ROW_COUNT} rows, {REPS} reps, median) ---", file=out)
        print(f"isolated per-open setup (connect + 4 PRAGMAs): {baseline_setup_per_open * 1000:.3f} ms", file=out)
        print(f"{'unit':<40} {'opens':>6} {'wall ms':>10} {'setup ms':>10} {'share':>8}", file=out)
        for name, opens, wall, setup, share in rows:
            print(
                f"{name:<40} {opens:>6} {wall * 1000:>10.3f} {setup * 1000:>10.3f} {share * 100:>7.1f}%",
                file=out,
            )
        print(f"materiality threshold: {MATERIALITY_THRESHOLD * 100:.0f}% of unit wall time", file=out)


if __name__ == "__main__":
    unittest.main()
