"""
Atomic first-owner claiming + a registration policy.

Two coupled fixes to the old "first user becomes admin" rule
(`AuthManager.register`), which decided admin-ness by counting rows
(fetch-then-count) and left registration open forever once an admin existed:

1. ``instance_claim`` - a single-row sentinel table. Its ``CHECK (id = 1)``
   primary key means at most one row can ever exist, so the very first
   ``INSERT ... (id, ...) VALUES (1, ...)`` wins and every later/concurrent
   attempt raises ``IntegrityError``. Registration writes this row in the SAME
   transaction as the first user (see
   ``UserRepository.create_claiming_instance``), so two concurrent
   registrations can never both become admin: SQLite serialises the two
   writers and the loser's sentinel insert is rejected by the constraint.

2. ``registration_policy`` setting (``open`` | ``closed``, default ``closed``).
   While the instance is unclaimed, registration is always allowed (someone has
   to become the owner). Once claimed, ``register()`` honours this policy;
   ``closed`` is the secure default, and an admin can reopen signups by setting
   it to ``open`` through the normal settings surface.

Backfill: an instance that already has users is already "claimed", so we seed
the sentinel for its earliest admin (or earliest user if somehow none is an
admin). Existing multi-user installs therefore read as claimed and, with the
default ``closed`` policy, stop accepting open registrations until an admin
deliberately reopens them - which is the whole point of the fix.
"""

import random
import string
import time

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        # 1. Single-row instance-claim sentinel. No FK on owner_user_id: the row
        #    is written in the same transaction as (and, for atomicity, BEFORE)
        #    the owner user row, so a FK would fire before the user exists. The
        #    columns are informational; the security property comes from the
        #    CHECK (id = 1) primary key, which permits exactly one row.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS instance_claim (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                owner_user_id TEXT,
                owner_username TEXT,
                claimed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 2. Backfill: treat an install that already has users as claimed, so
        #    registration does not silently reopen on upgrade. Prefer the
        #    earliest admin; fall back to the earliest user.
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        )
        if cursor.fetchone():
            cursor.execute("SELECT COUNT(*) FROM instance_claim")
            already_claimed = cursor.fetchone()[0]
            if not already_claimed:
                cursor.execute(
                    """
                    SELECT id, username FROM users
                    ORDER BY (account_type = 'ADMIN') DESC, created_at ASC, id ASC
                    LIMIT 1
                    """
                )
                owner = cursor.fetchone()
                if owner:
                    cursor.execute(
                        "INSERT INTO instance_claim (id, owner_user_id, owner_username) "
                        "VALUES (1, ?, ?)",
                        (owner[0], owner[1]),
                    )

        # 3. registration_policy setting (default 'closed'). Only consulted once
        #    the instance is claimed; an admin flips it to 'open' to reopen.
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if cursor.fetchone():
            timestamp = int(time.time() * 1000)
            randomness = "".join(
                random.choices(string.ascii_uppercase + string.digits, k=10)
            )
            setting_id = f"{timestamp:013d}{randomness}"
            description = (
                "Whether new-account registration is accepted once the instance "
                "has an owner: 'closed' (default, invitation-only) or 'open' "
                "(anyone may register). Ignored while the instance is unclaimed."
            )
            cursor.execute(
                """
                INSERT OR IGNORE INTO settings (
                    id, key, value, value_type, description, type
                ) VALUES (?, 'registration_policy', 'closed', 'string', ?, 'SYSTEM')
                """,
                (setting_id, description),
            )


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = 'registration_policy'")
        cursor.execute("DROP TABLE IF EXISTS instance_claim")
