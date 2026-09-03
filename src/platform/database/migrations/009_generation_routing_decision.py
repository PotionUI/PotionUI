"""Migration 009: `generations` learns a `routing_decision` column.

Captures `GenerationRouter`'s `RoutingDecision.to_trace_dict()` (plus a
`reason` on `chosen`) at creation, for the history detail "Routing" section
- see `src.features.generation.orchestrator`. Nullable: a generation started
with no router wired, or any row older than this migration, has none.

IDEMPOTENT: the column is added only when missing.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(generations)")
        existing = {row[1] for row in cursor.fetchall()}
        if 'routing_decision' in existing:
            print("Migration 009_generation_routing_decision: column already present, skipping")
            return
        cursor.execute("ALTER TABLE generations ADD COLUMN routing_decision TEXT")
    print("Migration 009_generation_routing_decision: added routing_decision column")


def down():
    print("Migration 009_generation_routing_decision: no-op (SQLite keeps the added column)")
