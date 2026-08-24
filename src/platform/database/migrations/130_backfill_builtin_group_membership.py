"""Migration 130: backfill built-in group membership for existing users.

Before this migration, only the account that won the instance claim was
joined to the built-in ALL_USERS/ALL_ADMINS groups (`UserRepository.
create_claiming_instance` -> `_join_builtin_groups`). Every user created
through another path - admin panel, self-registration after the instance was
claimed - was left out until `_join_builtin_groups` moved into the shared
`_insert_user` funnel both paths call (src/features/users/repository.py).
This migration backfills the missing memberships for accounts created before
that fix landed.

`INSERT OR IGNORE` keeps re-running (or a partially-applied prior run) a
no-op: a user already carrying the membership is left untouched.

down() is a deliberate no-op, same idiom as 075_add_generation_duration.py's
one-way repair: a backfilled row is indistinguishable from one a user or the
"Add new members to the everyone groups" automation added independently, so
there is nothing safe to undo.
"""

import random
import string

from src.platform.database.database import db

ALL_USERS_GROUP_ID = "all_users"
ALL_ADMINS_GROUP_ID = "all_admins"


def _generate_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=26))


def _has_table(cursor, name: str) -> bool:
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cursor.fetchone() is not None


def _group_exists(cursor, group_id: str) -> bool:
    cursor.execute("SELECT 1 FROM user_groups WHERE id = ?", (group_id,))
    return cursor.fetchone() is not None


def up():
    with db.get_cursor() as cursor:
        if not (_has_table(cursor, "users")
                and _has_table(cursor, "user_groups")
                and _has_table(cursor, "user_group_members")):
            print("Migration 130: required tables missing, skipping backfill")
            return

        all_users_exists = _group_exists(cursor, ALL_USERS_GROUP_ID)
        if not all_users_exists:
            print("Migration 130: all_users group missing, skipping backfill")
            return
        all_admins_exists = _group_exists(cursor, ALL_ADMINS_GROUP_ID)

        cursor.execute("SELECT id, account_type FROM users")
        users = cursor.fetchall()

        backfilled = 0
        for user_id, account_type in users:
            cursor.execute(
                "INSERT OR IGNORE INTO user_group_members (id, group_id, user_id) VALUES (?, ?, ?)",
                (_generate_id(), ALL_USERS_GROUP_ID, user_id),
            )
            backfilled += cursor.rowcount
            if account_type == "ADMIN" and all_admins_exists:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_group_members (id, group_id, user_id) VALUES (?, ?, ?)",
                    (_generate_id(), ALL_ADMINS_GROUP_ID, user_id),
                )
                backfilled += cursor.rowcount

        print(f"Migration 130: backfilled {backfilled} built-in group membership(s) for {len(users)} user(s)")


def down():
    print("Migration 130: down() is a deliberate no-op, see module docstring")
