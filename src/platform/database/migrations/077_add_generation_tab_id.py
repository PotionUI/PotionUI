"""
Give `generations` the tab that owns them.

Generations used to be fire-and-forget: the browser held the id of the one it
had just started, subscribed to it over the WebSocket, and that was the only
link between a generation and the tab that asked for it. Nothing server-side
recorded ownership.

With a queue, work outlives the request that created it - a generation can sit
pending for minutes and complete long after the tab that queued it navigated
away and came back. To rehydrate a tab's queue on reload we need the link to be
durable, so it lives on the row.

`tab_id` is minted by the client and is opaque to the server: it is a routing
label, not a foreign key. It is therefore only unique *within a user*, which is
why the index (and every query) is on `(user_id, tab_id, status)` rather than on
`tab_id` alone.

Existing rows get NULL, which reads as "no owning tab". They are all terminal by
the time this runs, so nothing needs to route to them.
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if _has_column(cursor, "generations", "tab_id"):
            print("Migration 077: generations.tab_id already present, skipping")
            return

        cursor.execute("ALTER TABLE generations ADD COLUMN tab_id TEXT")

        # Serves the two queue reads: "what is this tab waiting on" and
        # "what should this tab rehydrate", both of which filter by status.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generations_user_tab_status
            ON generations (user_id, tab_id, status)
        """)

        print("Migration 077: added generations.tab_id + (user_id, tab_id, status) index")


def down():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "generations", "tab_id"):
            print("Migration 077: generations.tab_id absent, nothing to drop")
            return

        cursor.execute("DROP INDEX IF EXISTS idx_generations_user_tab_status")
        cursor.execute("ALTER TABLE generations DROP COLUMN tab_id")
        print("Migration 077: dropped generations.tab_id")
