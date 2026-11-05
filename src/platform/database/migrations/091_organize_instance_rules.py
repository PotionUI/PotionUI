from src.platform.database.database import db

_NAME = "091_organize_instance_rules"

_RULES = (
    "CREATE TABLE organize_rules_rebuilt ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT, "
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
    "created_by TEXT, "
    "updated_by TEXT, "
    "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE, "
    "FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL, "
    "FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE SET NULL)"
)

_RULES_COPY = (
    "INSERT INTO organize_rules_rebuilt (id, user_id, name, subject, trigger, match_mode, conditions_json, "
    "actions_json, enabled, stop_after, position, paused_reason, paused_at, created_at, updated_at, last_run_at, "
    "created_by, updated_by) "
    "SELECT id, user_id, name, subject, trigger, match_mode, conditions_json, actions_json, enabled, stop_after, "
    "position, paused_reason, paused_at, created_at, updated_at, last_run_at, user_id, user_id FROM organize_rules"
)

_RUNS = (
    "CREATE TABLE organize_runs_rebuilt ("
    "id TEXT PRIMARY KEY, "
    "rule_id TEXT NOT NULL, "
    "rule_name TEXT NOT NULL, "
    "user_id TEXT, "
    "subject TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('live', 'backfill')), "
    "status TEXT NOT NULL DEFAULT 'running', "
    "matched INTEGER NOT NULL DEFAULT 0, "
    "applied INTEGER NOT NULL DEFAULT 0, "
    "started_at TEXT NOT NULL, "
    "finished_at TEXT, "
    "undone_at TEXT, "
    "started_by TEXT, "
    "undone_by TEXT, "
    "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE, "
    "FOREIGN KEY (started_by) REFERENCES users (id) ON DELETE SET NULL, "
    "FOREIGN KEY (undone_by) REFERENCES users (id) ON DELETE SET NULL)"
)

_RUNS_COPY = (
    "INSERT INTO organize_runs_rebuilt (id, rule_id, rule_name, user_id, subject, kind, status, matched, applied, "
    "started_at, finished_at, undone_at, started_by, undone_by) "
    "SELECT id, rule_id, rule_name, user_id, subject, kind, status, matched, applied, started_at, finished_at, "
    "undone_at, NULL, NULL FROM organize_runs"
)

_APPLICATIONS = (
    "CREATE TABLE organize_applications_rebuilt ("
    "id TEXT PRIMARY KEY, "
    "run_id TEXT NOT NULL, "
    "rule_id TEXT NOT NULL, "
    "user_id TEXT, "
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
    "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE)"
)

_APPLICATIONS_COPY = (
    "INSERT INTO organize_applications_rebuilt (id, run_id, rule_id, user_id, item_type, item_id, action_kind, "
    "target_type, target_id, target_name, change_json, created_at, undone_at) "
    "SELECT id, run_id, rule_id, user_id, item_type, item_id, action_kind, target_type, target_id, target_name, "
    "change_json, created_at, undone_at FROM organize_applications"
)

_OUTCOMES = (
    "CREATE TABLE IF NOT EXISTS organize_outcomes ("
    "rule_id TEXT NOT NULL, "
    "item_type TEXT NOT NULL, "
    "item_id TEXT NOT NULL, "
    "run_id TEXT, "
    "outcome TEXT NOT NULL CHECK (outcome IN ('waiting', 'skipped')), "
    "code TEXT NOT NULL, "
    "detail TEXT NOT NULL DEFAULT '', "
    "attempts INTEGER NOT NULL DEFAULT 0, "
    "next_try_at TEXT, "
    "updated_at TEXT NOT NULL, "
    "PRIMARY KEY (rule_id, item_type, item_id), "
    "FOREIGN KEY (rule_id) REFERENCES organize_rules (id) ON DELETE CASCADE)"
)

_INDEXES = (
    ("idx_organize_rules_user_subject", "organize_rules (user_id, subject, position)"),
    ("idx_organize_runs_user_started", "organize_runs (user_id, started_at)"),
    ("idx_organize_runs_rule", "organize_runs (rule_id, kind, started_at)"),
    ("idx_organize_applications_run", "organize_applications (run_id)"),
    ("idx_organize_applications_item", "organize_applications (user_id, item_type, item_id)"),
    ("idx_organize_applications_rule_created", "organize_applications (rule_id, created_at)"),
    ("idx_organize_applications_target", "organize_applications (target_type, target_id)"),
    ("idx_organize_outcomes_run", "organize_outcomes (run_id, outcome)"),
    ("idx_organize_outcomes_due", "organize_outcomes (outcome, next_try_at)"),
    ("idx_organize_outcomes_item", "organize_outcomes (item_type, item_id)"),
)


def _user_id_is_required(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    for row in cursor.fetchall():
        if row[1] == "user_id":
            return bool(row[3])
    return False


def _rebuild(cursor, table, create, copy):
    cursor.execute(f"DROP TABLE IF EXISTS {table}_rebuilt")
    cursor.execute(create)
    cursor.execute(copy)
    carried = cursor.rowcount
    cursor.execute(f"DROP TABLE {table}")
    cursor.execute(f"ALTER TABLE {table}_rebuilt RENAME TO {table}")
    return carried


def up():
    carried = {}
    with db.get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF").close()
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN")
            for table, create, copy in (
                ("organize_rules", _RULES, _RULES_COPY),
                ("organize_runs", _RUNS, _RUNS_COPY),
                ("organize_applications", _APPLICATIONS, _APPLICATIONS_COPY),
            ):
                if _user_id_is_required(cursor, table):
                    carried[table] = _rebuild(cursor, table, create, copy)
            for name, _target in _INDEXES:
                cursor.execute(f"DROP INDEX IF EXISTS {name}")
            cursor.execute(_OUTCOMES)
            for name, target in _INDEXES:
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
            cursor.close()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON").close()
    print(
        f"Migration {_NAME}: organize rules, runs and applications can be owned by the instance, "
        f"{carried.get('organize_rules', 0)} rule(s), {carried.get('organize_runs', 0)} run(s) and "
        f"{carried.get('organize_applications', 0)} change(s) kept"
    )
