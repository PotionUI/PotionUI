"""
Migration 092: session_versions -- versioned session history.

"Session history" shows different saves (historical) with one-click go-back;
bringing back a historical entry and saving again makes it the current (latest)
session state.

The `sessions` table (migration 020) already IS the "current" state and stays
exactly that -- zero behavior change for existing callers. This migration adds
an *append-only* history alongside it: every successful save
(`SessionManager.save_session` / `update_session`) additionally writes an
immutable snapshot row here. "Go back" needs no restore endpoint -- the UI
loads a version's payload into the tab client-side, and the next Save just
runs the normal save path, which appends the restored state as the newest
version.

Design:

* `session_id` carries a real `FOREIGN KEY ... ON DELETE CASCADE` -- unlike
  `generation_stats` (migration 091, deliberately uncoupled), a session's
  version history IS the session's own user data, so deleting the session
  must delete its versions too.
* `version_number` is monotonic per session (1, 2, 3, ...), enforced by the
  `UNIQUE(session_id, version_number)` constraint; `SessionVersionRepository`
  computes the next number from the current max.
* `summary` denormalizes a small human-relevant label (currently the preset's
  display name, resolved once at write time from the on-disk `preset.yml` via
  `FilePresetRepository` -- same "resolve once, store text" pattern
  `generation_stats.preset_name` uses in migration 091) so the version list
  endpoint never needs to touch the filesystem.
* `payload` is the full JSON-serialized session `data` snapshot; only
  fetched by the single-version detail endpoint, never by the list endpoint.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS session_versions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                payload TEXT NOT NULL,  -- JSON string, snapshot of session.data at save time
                summary TEXT,           -- denormalized human-relevant label (e.g. preset name)
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
                UNIQUE (session_id, version_number)
            )
            """
        )
        # The only read patterns are "list newest-first for a session" and
        # "fetch one version of a session" -- both covered by one composite
        # index over (session_id, version_number).
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_versions_session "
            "ON session_versions (session_id, version_number)"
        )


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_session_versions_session")
        cursor.execute("DROP TABLE IF EXISTS session_versions")
