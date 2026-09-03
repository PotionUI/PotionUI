"""Migration 008: `plugin_hooks` learns a display label and a role gate.

Needed for `admin_tabs[]` (a plugin's own tabs on its Admin -> Plugins detail
panel, registered as `hook_name = "admin.plugin.tabs"` rows) - a tab needs a
`label` to render and can carry `require_role` the same way a sidebar item
does. Both columns are nullable and unused by every other hook kind.

IDEMPOTENT: each column is added only when missing.
"""

from src.platform.database.database import db

_COLUMNS = (
    ("label", "TEXT"),
    ("require_role", "TEXT"),
)


def up():
    added = []
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(plugin_hooks)")
        existing = {row[1] for row in cursor.fetchall()}
        for name, ddl in _COLUMNS:
            if name in existing:
                continue
            cursor.execute(f"ALTER TABLE plugin_hooks ADD COLUMN {name} {ddl}")
            added.append(name)
    print(f"Migration 008_plugin_hooks_label_and_role: added {added or 'no'} column(s)")


def down():
    print("Migration 008_plugin_hooks_label_and_role: no-op (SQLite keeps the added columns)")
