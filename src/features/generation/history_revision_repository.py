"""The per-user history revision counter behind `GET /api/generations/history/version`.

Every mutation that changes what a user's history list would render bumps their
row; the version endpoint hands the counter out as an opaque token and the
client refetches only when it moves.

An aggregate over `generations` cannot stand in for this. Tags, collection
membership and attached files live in junction tables that leave the
`generations` row untouched, and `updated_at` has one-second resolution, so a
rating or favourite flipped inside the same second as the previous write is
invisible.

Every bump takes the caller's open cursor rather than opening its own. Each
`db.get_cursor()` is a separate SQLite connection, so a nested one would sit
behind its own caller's uncommitted write until the busy timeout expired.
"""

import sqlite3
from typing import Iterable, List, Optional

_UPSERT = """
    INSERT INTO history_revisions (user_id, revision, updated_at)
    VALUES (?, 1, CURRENT_TIMESTAMP)
    ON CONFLICT(user_id) DO UPDATE
    SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP
"""


def bump_history_revision(cursor: sqlite3.Cursor, user_id: Optional[str]) -> None:
    """Mark this user's history as changed."""
    if not user_id:
        return
    cursor.execute(_UPSERT, (user_id,))


def bump_history_revision_for_generation(
    cursor: sqlite3.Cursor, generation_id: Optional[str]
) -> None:
    """Mark the owner of this generation's history as changed.

    Consumes the cursor's result set, so callers must read whatever they need
    from their own statement - `rowcount` included - before calling this.
    """
    if not generation_id:
        return
    bump_history_revision_for_generations(cursor, [generation_id])


def bump_history_revision_for_generations(
    cursor: sqlite3.Cursor, generation_ids: Iterable[str]
) -> None:
    """`bump_history_revision_for_generation` for a batch, one bump per owner."""
    ids = [generation_id for generation_id in generation_ids if generation_id]
    if not ids:
        return

    placeholders = ','.join('?' * len(ids))
    cursor.execute(
        f"SELECT DISTINCT user_id FROM generations "
        f"WHERE id IN ({placeholders}) AND user_id IS NOT NULL",
        ids
    )
    owners: List[str] = [row[0] for row in cursor.fetchall()]

    for owner in owners:
        cursor.execute(_UPSERT, (owner,))


def read_history_revision(user_id: Optional[str]) -> Optional[int]:
    """This user's current revision, or None if nothing has bumped it yet."""
    if not user_id:
        return None

    from src.platform.database.database import db
    with db.get_cursor() as cursor:
        cursor.execute(
            "SELECT revision FROM history_revisions WHERE user_id = ?", (user_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else None
