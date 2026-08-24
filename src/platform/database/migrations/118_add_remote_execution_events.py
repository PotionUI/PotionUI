"""
Migration 118: persist the worker -> core event stream, and give
remote_executions the columns its dispatch policy needs.

`remote_execution_events` is the durable form of `JobEventV1` (see
src.platform.worker_protocol.job_event): one row per event, keyed on
(execution_id, cursor) so a replayed or out-of-order delivery is rejected by
the schema, not just by application logic. `payload` is the event's full
envelope-decoded dump - core never needs to re-derive an event from partial
columns, it needs the exact thing the worker sent, e.g. to answer an
EventResumeRequestV1. `received_at` is separate from the event's own
`emitted_at` because the two can disagree by however long the transport took,
and only `received_at` is meaningful for "how stale is our view of this
execution".

Two columns land on `remote_executions` itself:

- `expires_at_ms` mirrors `ExecutionPackageV1.expires_at` (epoch
  milliseconds, matching `lease_expires_at_ms` from migration 109, for the
  same reason: it's compared, not displayed). `expire_overdue()` sweeps rows
  whose deadline has passed into EXPIRED - the state migration 109 defined
  but nothing has ever produced until now.
- `lease_lapses` counts how many times a dispatcher's lease on this row
  lapsed and had to be reclaimed by `requeue_expired_leases`. It is
  deliberately not `attempt`: `attempt` (109) counts dispatch attempts - a
  worker actually got the package - while a lease lapse can happen before a
  worker ever saw it (a dispatcher process died between claiming the row and
  starting the transfer). Conflating them would make `attempt >=
  max_dispatch_attempts` fire on dispatcher crashes that never cost the
  worker anything.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS remote_execution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL,
                cursor INTEGER NOT NULL,
                kind TEXT NOT NULL,
                pipe_id TEXT,
                emitted_at TIMESTAMP NOT NULL,
                received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                payload TEXT NOT NULL,
                FOREIGN KEY (execution_id) REFERENCES remote_executions(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_execution_events_cursor
            ON remote_execution_events(execution_id, cursor)
        """)

        cursor.execute("PRAGMA table_info(remote_executions)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'expires_at_ms' not in columns:
            cursor.execute(
                "ALTER TABLE remote_executions ADD COLUMN expires_at_ms INTEGER"
            )

        if 'lease_lapses' not in columns:
            cursor.execute(
                "ALTER TABLE remote_executions "
                "ADD COLUMN lease_lapses INTEGER NOT NULL DEFAULT 0"
            )

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_executions_expires_at
            ON remote_executions(state, expires_at_ms)
        """)

        print("Migration 118: Created remote_execution_events; added expiry/lease-lapse columns")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS remote_execution_events")
        cursor.execute("DROP INDEX IF EXISTS idx_remote_executions_expires_at")
        # remote_executions.expires_at_ms/lease_lapses are left in place -
        # SQLite has no cheap DROP COLUMN, same tradeoff as 104/110.
        print("Migration 118: Dropped remote_execution_events (new columns left in place)")
