from src.platform.database.database import db

_NEW_INDEXES = (
    (
        "idx_run_reports_created_generation",
        "CREATE INDEX IF NOT EXISTS idx_run_reports_created_generation "
        "ON generation_run_reports (created_at, generation_id)",
    ),
    (
        "idx_generations_favorites_created",
        "CREATE INDEX IF NOT EXISTS idx_generations_favorites_created "
        "ON generations (user_id, created_at DESC, id DESC) WHERE is_favorite = 1",
    ),
    (
        "idx_models_file_path",
        "CREATE INDEX IF NOT EXISTS idx_models_file_path ON models (file_path)",
    ),
    (
        "idx_prompts_user_updated",
        "CREATE INDEX IF NOT EXISTS idx_prompts_user_updated ON prompts (user_id, updated_at DESC)",
    ),
)

_DUPLICATES_OF_UNIQUE = (
    "idx_mcp_tokens_hash",
    "idx_models_sha256",
    "idx_presets_preset_id",
    "idx_prompt_segments_parent",
    "idx_providers_model_provider",
    "idx_session_versions_session",
    "idx_settings_key",
    "idx_setup_step_attempts_run",
    "idx_template_segments_parent",
    "idx_user_groups_name",
    "idx_users_email",
    "idx_users_username",
)


def _key_columns(cursor, index_name):
    cursor.execute(f"PRAGMA index_xinfo('{index_name}')")
    return tuple((row[2], row[3], row[4]) for row in cursor.fetchall() if row[5] == 1)


def _table_exists(cursor, table):
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
    return cursor.fetchone() is not None


def _is_covered_by_a_unique_index(cursor, index_name):
    cursor.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type = 'index' AND name = ?", (index_name,)
    )
    row = cursor.fetchone()
    if row is None:
        return False
    table = row[0]
    cursor.execute(f"PRAGMA index_list('{table}')")
    listing = cursor.fetchall()
    own = next((entry for entry in listing if entry[1] == index_name), None)
    if own is None or own[2] or own[4]:
        return False
    columns = _key_columns(cursor, index_name)
    for entry in listing:
        if entry[1] == index_name or not entry[2] or entry[4]:
            continue
        if _key_columns(cursor, entry[1]) == columns:
            return True
    return False


def up():
    created = []
    dropped = []
    with db.get_cursor() as cursor:
        for name, statement in _NEW_INDEXES:
            table = statement.split(" ON ", 1)[1].split(" ", 1)[0]
            if _table_exists(cursor, table):
                cursor.execute(statement)
                created.append(name)
        for name in _DUPLICATES_OF_UNIQUE:
            if _is_covered_by_a_unique_index(cursor, name):
                cursor.execute(f"DROP INDEX IF EXISTS {name}")
                dropped.append(name)
    print(
        f"Migration 027_measured_indexes: created {len(created)} index(es), "
        f"dropped {len(dropped)} duplicate(s) of a UNIQUE index"
    )


def down():
    with db.get_cursor() as cursor:
        for name, _ in _NEW_INDEXES:
            cursor.execute(f"DROP INDEX IF EXISTS {name}")
    print("Migration 027_measured_indexes: dropped the added indexes")
