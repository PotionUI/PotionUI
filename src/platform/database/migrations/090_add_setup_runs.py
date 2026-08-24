"""
Migration 090: durable setup-run state.

The onboarding wizard must not own its own position. A browser
refresh, an API restart, or a crash mid-provisioning has to reconstruct exactly
where setup was. So the plan lives in the database, not in a frontend step
index (audit note cmrl0fao90015pe01udsyaunt, "Use a durable plan").

Three tables:

* ``setup_runs`` - one row per attempt to provision the instance against a
  recipe. Its lifecycle status is the single source of truth the frontend
  renders. ``safe_input`` / ``safe_output`` are *redacted* JSON blobs (plain
  scalar fields only; secret-looking keys stripped by the DTO layer before they
  ever reach here) - never serialized service objects, never tokens.

* ``setup_step_attempts`` - append-only. Each retry of a step writes a NEW row
  with an incremented ``attempt`` number, so the full provenance of what was
  tried, when, and why it failed is preserved. ``(run_id, step_key, attempt)``
  is unique.

* ``user_onboarding_state`` - per-user "have you finished onboarding / seen your
  first result" markers, distinct from the instance-level setup run.

Single-active-run guarantee. Setup mutates the whole instance, so at most one
run may be active (non-terminal) at a time. Rather than a fragile
select-then-insert, this mirrors the ``instance_claim`` idiom (089): an
``active_marker`` column holds the constant ``1`` while a run is active and is
set to NULL once it reaches a terminal status. A UNIQUE index over that column
lets any number of terminal rows coexist (NULLs are distinct in SQLite unique
indexes) while permitting exactly one active row. A concurrent second create
therefore fails with IntegrityError, which the repository turns into "return the
existing active run" - idempotent creation.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        # One provisioning run against a recipe. `created_by` is informational
        # provenance (the owner/admin who started it) and deliberately carries
        # NO foreign key - the same choice `instance_claim.owner_user_id` makes
        # (089): a run must be creatable without coupling to user-row existence
        # or ordering, and losing the owner account later must not delete run
        # history. `active_marker` is 1 while the run is non-terminal, NULL once
        # terminal - see the module docstring.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS setup_runs (
                id TEXT PRIMARY KEY,
                recipe_id TEXT NOT NULL,
                recipe_version INTEGER NOT NULL DEFAULT 1,
                scope TEXT NOT NULL DEFAULT 'instance',
                status TEXT NOT NULL DEFAULT 'pending',
                current_step TEXT,
                safe_input TEXT,
                safe_output TEXT,
                error_code TEXT,
                safe_error_detail TEXT,
                active_marker INTEGER,
                created_by TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
            """
        )

        # At most one active run instance-wide. Terminal rows carry NULL and do
        # not contend; exactly one row may carry active_marker = 1.
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_setup_runs_single_active "
            "ON setup_runs (active_marker)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_setup_runs_status "
            "ON setup_runs (status, created_at)"
        )

        # Append-only step attempts. A retry never mutates a prior attempt; it
        # inserts a new row with attempt = previous + 1.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS setup_step_attempts (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_key TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                progress_current INTEGER,
                progress_total INTEGER,
                progress_unit TEXT,
                safe_input TEXT,
                safe_output TEXT,
                error_code TEXT,
                safe_error_detail TEXT,
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES setup_runs (id) ON DELETE CASCADE,
                UNIQUE (run_id, step_key, attempt)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_setup_step_attempts_run "
            "ON setup_step_attempts (run_id, step_key, attempt)"
        )

        # Per-user onboarding markers (distinct from the instance-level run).
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_onboarding_state (
                user_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                dismissed_at TIMESTAMP,
                completed_at TIMESTAMP,
                first_generation_id TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
        )


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS user_onboarding_state")
        cursor.execute("DROP TABLE IF EXISTS setup_step_attempts")
        cursor.execute("DROP INDEX IF EXISTS uq_setup_runs_single_active")
        cursor.execute("DROP INDEX IF EXISTS idx_setup_runs_status")
        cursor.execute("DROP TABLE IF EXISTS setup_runs")
