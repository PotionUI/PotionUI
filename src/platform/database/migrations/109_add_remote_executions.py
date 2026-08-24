"""
Migration 109: Create the remote_executions table.

Core's record of one pipeline handed to a remote native worker. Read and
written by src/features/remote_execution/.

Three of the columns exist for failure modes rather than for the happy path:

- `idempotency_key` is UNIQUE. A resubmitted request collapses onto the row
  that already exists instead of starting a second billed job.
- `lease_owner`/`lease_expires_at_ms`/`lease_epoch` let exactly one dispatcher
  hold a row at a time. The claim is a single conditional UPDATE, so two
  dispatchers racing produce one winner; the epoch is a fencing token, so a
  dispatcher that stalled past its lease and woke up cannot overwrite the
  writes of whoever took the row from it. Lease deadlines are epoch
  milliseconds, not TIMESTAMP: they are *compared*, and SQLite's
  CURRENT_TIMESTAMP format ('YYYY-MM-DD HH:MM:SS') sorts inconsistently
  against an ISO-8601 string with a 'T' or an offset.
- `event_cursor` is the highest contiguous worker event applied. A reconnecting
  core resumes from it rather than replaying (duplicate artifacts) or skipping
  (lost artifacts).

`request_digest` lets core tell a worker reporting on the package it was sent
from one reporting on a stale package it still had.
"""

from src.platform.database.database import db


def up():
    """Create remote_executions and its indexes."""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS remote_executions (
                id TEXT PRIMARY KEY,
                generation_id TEXT,
                provider TEXT NOT NULL,
                backend_id TEXT,
                state TEXT NOT NULL,
                protocol_version INTEGER NOT NULL DEFAULT 1,
                idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                provider_job_id TEXT,
                worker_id TEXT,
                event_cursor INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT,
                lease_expires_at_ms INTEGER,
                lease_epoch INTEGER NOT NULL DEFAULT 0,
                attempt INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                error_message TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                dispatched_at TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_executions_idempotency_key
            ON remote_executions(idempotency_key)
        """)

        # Partial index: many rows have no provider job id yet, and NULLs are
        # distinct in SQLite - a plain unique index would permit two rows to
        # claim the same provider job once both are dispatched.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_executions_provider_job
            ON remote_executions(provider, provider_job_id)
            WHERE provider_job_id IS NOT NULL
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_executions_state
            ON remote_executions(state, created_at)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_executions_lease
            ON remote_executions(state, lease_expires_at_ms)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_executions_generation_id
            ON remote_executions(generation_id)
        """)

        print("Migration 109: Created remote_executions table")


def down():
    """Drop remote_executions table."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS remote_executions")
        print("Migration 109: Dropped remote_executions table")
