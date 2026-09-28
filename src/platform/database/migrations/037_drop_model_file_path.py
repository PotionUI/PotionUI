from src.platform.database.database import db
from src.platform.filesystem.model_roots import physical_legacy_ref

_NAME = "037_drop_model_file_path"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_models_file_path")
        if _has_column(cursor, "models", "file_path"):
            cursor.execute("ALTER TABLE models DROP COLUMN file_path")
    print(f"Migration {_NAME}: dropped models.file_path and its index")


def down():
    restored = 0
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "models", "file_path"):
            cursor.execute("ALTER TABLE models ADD COLUMN file_path TEXT")
        restored = _restore_file_paths(cursor)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_models_file_path ON models (file_path)")
    print(f"Migration {_NAME}: restored models.file_path for {restored} model(s)")


def _has_column(cursor, table, column) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in cursor.fetchall())


def _best_present_location(cursor, model_id: str):
    cursor.execute(
        """
        SELECT ml.rel_path AS rel_path, mr.path AS root_path, mrb.subdir AS subdir
        FROM model_locations ml
        JOIN model_root_bindings mrb ON mrb.root_id = ml.root_id AND mrb.model_type = ml.model_type
        JOIN model_roots mr ON mr.id = ml.root_id
        WHERE ml.model_id = ? AND ml.status = 'present'
        ORDER BY mrb.position ASC, LENGTH(ml.rel_path) ASC, ml.rel_path ASC
        LIMIT 1
        """,
        (model_id,),
    )
    return cursor.fetchone()


def _restore_file_paths(cursor) -> int:
    cursor.execute("SELECT id FROM models")
    model_ids = [row["id"] for row in cursor.fetchall()]

    restored = 0
    for model_id in model_ids:
        location = _best_present_location(cursor, model_id)
        if location is None:
            continue
        file_path = physical_legacy_ref(location["root_path"], location["subdir"], location["rel_path"])
        cursor.execute("UPDATE models SET file_path = ? WHERE id = ?", (file_path, model_id))
        restored += 1
    return restored
