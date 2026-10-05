from src.platform.database.database import db

_NAME = "052_organize"

_TABLES = (
    "CREATE TABLE IF NOT EXISTS organize_rules ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL, "
    "name TEXT NOT NULL, "
    "subject TEXT NOT NULL CHECK (subject IN ('generation', 'upload', 'model')), "
    "trigger TEXT NOT NULL, "
    "match_mode TEXT NOT NULL DEFAULT 'all' CHECK (match_mode IN ('all', 'any')), "
    "conditions_json TEXT NOT NULL DEFAULT '[]', "
    "actions_json TEXT NOT NULL DEFAULT '[]', "
    "enabled INTEGER NOT NULL DEFAULT 1, "
    "stop_after INTEGER NOT NULL DEFAULT 0, "
    "position INTEGER NOT NULL DEFAULT 0, "
    "paused_reason TEXT, "
    "paused_at TEXT, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "last_run_at TEXT, "
    "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS organize_runs ("
    "id TEXT PRIMARY KEY, "
    "rule_id TEXT NOT NULL, "
    "rule_name TEXT NOT NULL, "
    "user_id TEXT NOT NULL, "
    "subject TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('live', 'backfill')), "
    "status TEXT NOT NULL DEFAULT 'running', "
    "matched INTEGER NOT NULL DEFAULT 0, "
    "applied INTEGER NOT NULL DEFAULT 0, "
    "started_at TEXT NOT NULL, "
    "finished_at TEXT, "
    "undone_at TEXT, "
    "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS organize_applications ("
    "id TEXT PRIMARY KEY, "
    "run_id TEXT NOT NULL, "
    "rule_id TEXT NOT NULL, "
    "user_id TEXT NOT NULL, "
    "item_type TEXT NOT NULL, "
    "item_id TEXT NOT NULL, "
    "action_kind TEXT NOT NULL, "
    "target_type TEXT NOT NULL, "
    "target_id TEXT NOT NULL, "
    "target_name TEXT NOT NULL DEFAULT '', "
    "change_json TEXT NOT NULL DEFAULT '{}', "
    "created_at TEXT NOT NULL, "
    "undone_at TEXT, "
    "FOREIGN KEY (run_id) REFERENCES organize_runs (id) ON DELETE CASCADE, "
    "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS organize_handled ("
    "rule_id TEXT NOT NULL, "
    "item_type TEXT NOT NULL, "
    "item_id TEXT NOT NULL, "
    "handled_at TEXT NOT NULL, "
    "PRIMARY KEY (rule_id, item_type, item_id), "
    "FOREIGN KEY (rule_id) REFERENCES organize_rules (id) ON DELETE CASCADE)",
    "CREATE TABLE IF NOT EXISTS organize_controls ("
    "scope_key TEXT PRIMARY KEY, "
    "paused INTEGER NOT NULL DEFAULT 0, "
    "rule_cap INTEGER, "
    "hourly_limit INTEGER, "
    "updated_at TEXT NOT NULL)",
)

_INDEXES = (
    ("idx_organize_rules_user_subject", "organize_rules (user_id, subject, position)"),
    ("idx_organize_runs_user_started", "organize_runs (user_id, started_at)"),
    ("idx_organize_runs_rule", "organize_runs (rule_id, kind, started_at)"),
    ("idx_organize_applications_run", "organize_applications (run_id)"),
    ("idx_organize_applications_item", "organize_applications (user_id, item_type, item_id)"),
    ("idx_organize_applications_rule_created", "organize_applications (rule_id, created_at)"),
    ("idx_organize_applications_target", "organize_applications (target_type, target_id)"),
)

_DROP_ORDER = (
    "organize_handled",
    "organize_applications",
    "organize_runs",
    "organize_rules",
    "organize_controls",
)


def up():
    with db.get_cursor() as cursor:
        for statement in _TABLES:
            cursor.execute(statement)
        for name, target in _INDEXES:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
    print(f"Migration {_NAME}: organize tables created")


def down():
    with db.get_cursor() as cursor:
        for name, _target in _INDEXES:
            cursor.execute(f"DROP INDEX IF EXISTS {name}")
        for table in _DROP_ORDER:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
    print(f"Migration {_NAME}: organize tables dropped")
