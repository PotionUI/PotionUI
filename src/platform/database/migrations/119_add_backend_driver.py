"""
Migration 119: backend driver discriminator.

`backends.engine` conflates two things: the protocol a pipeline speaks
(`native`, `comfyui`, ...) and which concrete implementation executes it. The
native engine is about to gain a second implementation - a remote worker
(`native.remote`) alongside the always-present in-process one (`native.local`)
- while every preset, selection, default and priority rule stays keyed on
`engine` alone (a preset says `engine: native`; it never picks a driver).
`driver` is the narrower discriminator that answers "which registered
implementation does THIS row use", so `BackendRegistry` can instantiate the
right class without any of the engine-level selection logic changing.

Backfill: every existing `native` row becomes driver `native.local` (the only
native driver that has ever existed); every other engine's rows become their
own engine name, because a plugin that only ever registered by engine name
(e.g. `comfyui`) has exactly one driver - the engine itself. That mapping is
the permanent contract for "engine-only" plugin registration going forward
(see `BackendRegistry._register_builtin_backends` / the `backend.register`
hook), not a temporary shim.
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if _has_column(cursor, "backends", "driver"):
            print("Migration 119: backends.driver already present, skipping")
            return

        cursor.execute("ALTER TABLE backends ADD COLUMN driver TEXT NOT NULL DEFAULT ''")
        cursor.execute(
            """
            UPDATE backends
            SET driver = CASE WHEN engine = 'native' THEN 'native.local' ELSE engine END
            """
        )
        print("Migration 119: added backends.driver (native -> native.local, else = engine)")


def down():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "backends", "driver"):
            print("Migration 119: backends.driver absent, nothing to drop")
            return
        cursor.execute("ALTER TABLE backends DROP COLUMN driver")
        print("Migration 119: dropped backends.driver")
