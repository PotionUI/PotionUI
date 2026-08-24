"""Migration 137: split the shared collections tree into History, Library and
Prompts.

`collections` (062) held both generation folders and library folders in one
tree, distinguished only by which junction (`collection_generations` vs
`collection_uploads`, 115) a row's memberships lived in. The maintainer ruled
this wrong - a History folder must never surface in Library, and vice versa -
and generalized the rule: every module's collections are separate. This adds
`collections.scope` (open enumeration, launching with 'history' | 'library' |
'prompts' - see `CollectionScope`/`ALLOWED_SCOPES` in
`src.features.collections.dto`) and backfills it from existing membership
content:

- A collection with only `collection_generations` memberships (or none at
  all) becomes scope='history'.
- A collection with only `collection_uploads` memberships becomes
  scope='library'.
- A collection with BOTH kinds of membership is split in two: the original
  row keeps scope='history' and its generation memberships; a new,
  same-named clone is created with scope='library', starting with the same
  `parent_id`, and takes over the `collection_uploads` rows (reassigned by
  `collection_id`, not duplicated).

The Prompt Library's collections are a brand-new feature (no pre-existing
rows), so 'prompts' needs no backfill - only its junction table,
`collection_prompts` (mirroring `collection_uploads`: collection_id/prompt_id,
both FK cascade, `added_at`, unique pair, both columns indexed).

Parent/nesting integrity: a folder tree must not cross scopes. After every
row above has its final scope, each collection whose `parent_id` points at a
collection of a *different* scope is re-rooted (`parent_id = NULL`) rather
than left attached to a foreign tree. This includes clones, which start out
mirroring their original's parent_id and are re-rooted by the same pass if
that parent turned out to be 'library'-incompatible (or vice versa).

down() is a best-effort merge, not a true inverse: for each scope='library'
collection it looks for a same-owner, same-name scope='history' sibling and,
if found, moves its `collection_uploads` memberships onto that sibling and
drops the (now-empty, non-parent) clone row. A library collection with no
matching history sibling - or with its own children - is left in place.
SQLite can't drop a column, so `scope` itself is never removed (same
convention as 063's `parent_id`). `collection_prompts` is dropped outright -
it carries no legacy data to preserve.
"""

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _create_collection_prompts(cursor) -> None:
    """Idempotent on its own (IF NOT EXISTS) - kept outside the scope-column
    guard below so a re-run after a partial migration still ensures it exists."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS collection_prompts (
            collection_id TEXT NOT NULL,
            prompt_id TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(collection_id, prompt_id),
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
            FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_prompts_collection_id ON collection_prompts (collection_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_collection_prompts_prompt_id ON collection_prompts (prompt_id)"
    )


def up():
    with db.get_cursor() as cursor:
        _create_collection_prompts(cursor)

        if _column_exists(cursor, "collections", "scope"):
            return

        cursor.execute(
            "ALTER TABLE collections ADD COLUMN scope TEXT NOT NULL DEFAULT 'history'"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_collections_scope ON collections (scope)")

        cursor.execute("SELECT id, parent_id FROM collections")
        rows = [dict(row) for row in cursor.fetchall()]

        clone_ids = set()
        for row in rows:
            collection_id = row["id"]

            cursor.execute(
                "SELECT 1 FROM collection_generations WHERE collection_id = ? LIMIT 1",
                (collection_id,),
            )
            has_generations = cursor.fetchone() is not None

            cursor.execute(
                "SELECT 1 FROM collection_uploads WHERE collection_id = ? LIMIT 1",
                (collection_id,),
            )
            has_uploads = cursor.fetchone() is not None

            if has_generations and has_uploads:
                clone_id = generate_ulid()
                cursor.execute(
                    """
                    INSERT INTO collections (id, name, user_id, parent_id, created_at, scope)
                    SELECT ?, name, user_id, parent_id, created_at, 'library'
                    FROM collections WHERE id = ?
                    """,
                    (clone_id, collection_id),
                )
                cursor.execute(
                    "UPDATE collection_uploads SET collection_id = ? WHERE collection_id = ?",
                    (clone_id, collection_id),
                )
                clone_ids.add(clone_id)
                # Original already defaulted to 'history' by the ALTER TABLE above.
            elif has_uploads:
                cursor.execute(
                    "UPDATE collections SET scope = 'library' WHERE id = ?", (collection_id,)
                )
            # has_generations-only, or no memberships at all: stays 'history'.

        # Re-root pass: a collection whose parent ended up in a different
        # scope is detached to root rather than left in a cross-scope tree.
        # Snapshot every row's final (parent_id, scope) up front - re-rooting
        # a child never changes its own scope, so each decision only depends
        # on its immediate parent's scope, not on other re-roots in this pass.
        cursor.execute("SELECT id, parent_id, scope FROM collections")
        final = {row["id"]: (row["parent_id"], row["scope"]) for row in cursor.fetchall()}

        for collection_id, (parent_id, scope) in final.items():
            if parent_id is None or parent_id not in final:
                continue
            _, parent_scope = final[parent_id]
            if parent_scope != scope:
                cursor.execute(
                    "UPDATE collections SET parent_id = NULL WHERE id = ?", (collection_id,)
                )

    print(f"Migration 137: added collections.scope, split {len(clone_ids)} mixed collection(s)")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS collection_prompts")

        cursor.execute(
            "SELECT id, user_id, name FROM collections WHERE scope = 'library'"
        )
        library_collections = [dict(row) for row in cursor.fetchall()]

        merged = 0
        for library_collection in library_collections:
            cursor.execute(
                "SELECT id FROM collections WHERE user_id = ? AND name = ? AND scope = 'history'",
                (library_collection["user_id"], library_collection["name"]),
            )
            sibling = cursor.fetchone()
            if not sibling:
                continue

            cursor.execute(
                "UPDATE collection_uploads SET collection_id = ? WHERE collection_id = ?",
                (sibling["id"], library_collection["id"]),
            )

            cursor.execute(
                "SELECT 1 FROM collections WHERE parent_id = ? LIMIT 1",
                (library_collection["id"],),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    "DELETE FROM collections WHERE id = ?", (library_collection["id"],)
                )
                merged += 1

    print(f"Migration 137: merged {merged} library clone(s) back into their history sibling")
