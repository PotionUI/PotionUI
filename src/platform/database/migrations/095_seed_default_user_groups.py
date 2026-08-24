"""
Migration 095: seed the ALL_USERS / ALL_ADMINS built-in user groups.

A fresh onboarded instance needs an ALL_USERS group and an ALL_ADMINS group
created by default, with the first user in both. Before this migration,
`user_groups` (migration 039) started empty -
an admin had to create groups by hand before group-scoped preset/LLM/model
access meant anything, and there was no "everyone" group a plugin or
automation could target.

This migration:

1. Adds `user_groups.is_system` (INTEGER 0/1, default 0). `UserGroupManager.
   delete_group` (src/features/user_groups/manager.py) refuses to delete a
   group with `is_system=1` (raises `SystemGroupProtectedError`, surfaced by
   the route as HTTP 409) - a plain flag, not a hardcoded id check, so a
   future system group needs no code change beyond seeding it here.
2. Seeds exactly two groups with STABLE, well-known ids (not `generate_ulid()`
   - see `src.features.user_groups.constants` for why): `all_users` and
   `all_admins`. Seeding at migration time (not lazily at claim time) means
   the groups exist and are visible in the admin Groups UI even before the
   instance is claimed, same as every other admin-facing default.
3. `INSERT OR IGNORE` on both the column-add guard and the seed rows keeps
   this idempotent: re-running (or a partially-applied prior run) never
   double-inserts or errors on an already-migrated database.

Every user is joined to ALL_USERS (and ALL_ADMINS when created as an admin)
atomically with their own creation - see `UserRepository._insert_user` /
`_join_builtin_groups` (src/features/users/repository.py), which inserts the
membership rows on the SAME cursor/transaction as the user row, for every
creation path (admin panel, self-registration, instance claim). That code
references the two ids below via `src.features.user_groups.constants`, kept
in sync with the literals seeded here by convention (migrations stay
self-contained/import-free, like every other migration in this directory).
Migration 130 backfills the membership for accounts created before this
became unconditional.
"""

from src.platform.database.database import db

ALL_USERS_GROUP_ID = "all_users"
ALL_ADMINS_GROUP_ID = "all_admins"


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "user_groups", "is_system"):
            cursor.execute("ALTER TABLE user_groups ADD COLUMN is_system INTEGER NOT NULL DEFAULT 0")
            print("Migration 095: added user_groups.is_system")
        else:
            print("Migration 095: user_groups.is_system already present, skipping")

        cursor.execute(
            """
            INSERT OR IGNORE INTO user_groups (id, name, description, is_system)
            VALUES (?, ?, ?, 1)
            """,
            (
                ALL_USERS_GROUP_ID,
                "All Users",
                "Every account on this instance. Built in - can't be deleted.",
            ),
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO user_groups (id, name, description, is_system)
            VALUES (?, ?, ?, 1)
            """,
            (
                ALL_ADMINS_GROUP_ID,
                "All Admins",
                "Every administrator on this instance. Built in - can't be deleted.",
            ),
        )
        print("Migration 095: seeded All Users / All Admins built-in groups")


def down():
    with db.get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM user_groups WHERE id IN (?, ?)",
            (ALL_USERS_GROUP_ID, ALL_ADMINS_GROUP_ID),
        )
        if _has_column(cursor, "user_groups", "is_system"):
            cursor.execute("ALTER TABLE user_groups DROP COLUMN is_system")
        print("Migration 095: removed built-in groups and user_groups.is_system")
