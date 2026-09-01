"""claim_for_dispatch under real concurrency.

The existing dispatch-lease tests in test_repository.py prove the SQL
precondition sequentially - dispatcher A claims, then dispatcher B claims,
one call after another. Nothing exercises two dispatchers actually racing
against the same row at the same time. This uses real OS threads, each with
its own sqlite3 connection (matching production: `Database.get_connection()`
opens a fresh connection per call), against a real file-backed database in
WAL mode - the same connection pattern `claim_for_dispatch` runs under
in production.
"""

import io
import sys
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from src.features.remote_execution.records import RemoteExecution, RemoteExecutionState
from src.features.remote_execution.repository import RemoteExecutionRepository
from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner

S = RemoteExecutionState


class ClaimForDispatchConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db_path = Path(self.temp_dir) / "test.sqlite"

        Database._instance = None
        self.db = Database()
        self.db.db_path = self.temp_db_path
        self.db.db_path.parent.mkdir(exist_ok=True)
        self.db._initialized = True

        self._patchers = [
            patch("src.platform.database.database.db", self.db),
            patch("src.platform.database.migration_runner.db", self.db),
        ]
        for p in self._patchers:
            p.start()

        manager = MigrationRunner()
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            manager.run_migrations()
        finally:
            sys.stdout = old_stdout

        self.repo = RemoteExecutionRepository()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        for leftover in Path(self.temp_dir).iterdir():
            leftover.unlink()
        Path(self.temp_dir).rmdir()
        Database._instance = None

    def _seed_pending(self, n: int) -> list[str]:
        ids = []
        for i in range(n):
            row = self.repo.create(
                RemoteExecution(
                    id="",
                    provider="example-provider",
                    state=S.PENDING,
                    idempotency_key=f"idem-{i}",
                    request_digest="sha256:" + "a" * 64,
                )
            )
            ids.append(row.id)
        return ids

    def test_many_dispatchers_racing_one_row_yields_exactly_one_winner(self):
        """The tightest case: N threads, ONE pending row. Exactly one must win."""
        seeded = self._seed_pending(1)
        n_threads = 24
        results: list = [None] * n_threads
        errors: list = []
        barrier = threading.Barrier(n_threads)

        def worker(i):
            try:
                barrier.wait(timeout=10)
                results[i] = self.repo.claim_for_dispatch(f"dispatcher-{i}", 60)
            except Exception as exc:  # noqa: BLE001 - capture for the assertion below
                errors.append((i, exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"claim_for_dispatch raised under contention: {errors}")

        winners = [r for r in results if r is not None]
        self.assertEqual(
            len(winners), 1,
            f"expected exactly one winner, got {len(winners)}: "
            f"{[(w.id, w.lease_owner) for w in winners]}",
        )
        self.assertEqual(winners[0].id, seeded[0])

        # The row itself must reflect exactly one claim: epoch bumped once,
        # attempt bumped once, and it must equal what the winner observed
        # (no lost update from a second writer landing after the winner read).
        final = self.repo.get_by_id(seeded[0])
        self.assertEqual(final.lease_epoch, 1)
        self.assertEqual(final.attempt, 1)
        self.assertEqual(final.lease_owner, winners[0].lease_owner)
        self.assertEqual(final.state, S.DISPATCHING)

    def test_many_dispatchers_racing_many_rows_no_double_claim_no_row_lost(self):
        """N pending rows, more dispatcher threads than rows: every row claimed
        by exactly one dispatcher, no dispatcher claims two rows in the same
        wave, and no row is claimed twice."""
        n_rows = 8
        n_threads = 32
        seeded = set(self._seed_pending(n_rows))

        claims: list = []
        lock = threading.Lock()
        errors: list = []
        barrier = threading.Barrier(n_threads)

        def worker(i):
            try:
                barrier.wait(timeout=10)
                result = self.repo.claim_for_dispatch(f"dispatcher-{i}", 60)
                if result is not None:
                    with lock:
                        claims.append((i, result.id, result.lease_owner, result.lease_epoch))
            except Exception as exc:  # noqa: BLE001
                errors.append((i, exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"claim_for_dispatch raised under contention: {errors}")

        claimed_ids = [c[1] for c in claims]
        self.assertEqual(
            len(claimed_ids), n_rows,
            f"expected exactly {n_rows} successful claims (one per row), got {len(claimed_ids)}",
        )
        # No id claimed more than once.
        counts = Counter(claimed_ids)
        duplicates = {k: v for k, v in counts.items() if v > 1}
        self.assertEqual(duplicates, {}, f"row(s) claimed more than once: {duplicates}")
        # Every seeded row got claimed - none stranded.
        self.assertEqual(set(claimed_ids), seeded)
        # No dispatcher thread won twice (each thread calls claim exactly once
        # here, so this is really checking claims list integrity).
        thread_ids = [c[0] for c in claims]
        self.assertEqual(len(thread_ids), len(set(thread_ids)))

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT state, lease_epoch, attempt FROM remote_executions")
            rows = cursor.fetchall()
        self.assertEqual(len(rows), n_rows)
        for row in rows:
            self.assertEqual(row["state"], S.DISPATCHING.value)
            self.assertEqual(row["lease_epoch"], 1)
            self.assertEqual(row["attempt"], 1)
