"""
Rework backends from execution-location types to engines.

The `backends.type` column conflated two ideas: where a pipeline executes
(`local`, `runpod`, `remote_http`) and which engine it speaks (`local` diffusers
pipes vs a `comfyui` server). `runpod`/`remote_http` were never selected by any
preset and have been deleted; what remains are engines.

This migration:
  - renames `type` -> `engine`
  - renames the `local` engine to `native` (matching the `content/presets/marketplace/` layout)
  - drops any leftover `runpod` / `remote_http` backend rows
  - strips the never-enforced `max_concurrent_generations` key from `config`
  - makes `is_default` unique PER ENGINE rather than globally
  - seeds a default backend for each engine that has exactly one candidate

See docs/backends.md.
"""

import json

from src.platform.database.database import db


def up():
    """Migrate the backends table from `type` to `engine`."""
    with db.get_cursor() as cursor:
        # The old global single-default index would block per-engine defaults.
        cursor.execute("DROP INDEX IF EXISTS idx_backends_default")

        cursor.execute("ALTER TABLE backends RENAME COLUMN type TO engine")

        # Deleted backend types - nothing ever selected them.
        cursor.execute("DELETE FROM backends WHERE engine IN ('runpod', 'remote_http')")

        cursor.execute("UPDATE backends SET engine = 'native' WHERE engine = 'local'")

        # max_concurrent_generations was configured, persisted, surfaced in the
        # admin UI, and enforced nowhere. Drop it from the config blob.
        cursor.execute("SELECT id, config FROM backends")
        for row in cursor.fetchall():
            config = json.loads(row['config']) if row['config'] else {}
            if 'max_concurrent_generations' in config:
                config.pop('max_concurrent_generations')
                cursor.execute(
                    "UPDATE backends SET config = ? WHERE id = ?",
                    (json.dumps(config), row['id'])
                )

        cursor.execute("""
            CREATE UNIQUE INDEX idx_backends_default
            ON backends (engine)
            WHERE is_default = 1
        """)

        # Previously `is_default` was silently discarded on every write, so no
        # row has it set. Give each engine a default where the choice is
        # unambiguous (exactly one enabled backend).
        cursor.execute("""
            UPDATE backends SET is_default = 1
            WHERE enabled = 1
              AND engine IN (
                  SELECT engine FROM backends WHERE enabled = 1
                  GROUP BY engine HAVING COUNT(*) = 1
              )
        """)

        print("Migration 069: backends.type -> backends.engine (local -> native)")


def down():
    """Revert the backends table to `type`."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_backends_default")

        cursor.execute("UPDATE backends SET engine = 'local' WHERE engine = 'native'")
        cursor.execute("ALTER TABLE backends RENAME COLUMN engine TO type")

        # Restore the global single-default invariant, keeping at most one.
        cursor.execute("""
            UPDATE backends SET is_default = 0
            WHERE id NOT IN (SELECT id FROM backends WHERE is_default = 1 LIMIT 1)
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX idx_backends_default
            ON backends (is_default)
            WHERE is_default = 1
        """)

        print("Migration 069: reverted backends.engine -> backends.type")
