from src.platform.database.database import db
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY

_NAME = "036_native_availability_logical_refs"

_NATIVE_LOCAL_DRIVER = "native.local"


def up():
    with db.get_cursor() as cursor:
        rewritten, dropped = _rewrite(cursor)
    print(f"Migration {_NAME}: {rewritten} ref(s) rewritten to logical form, {dropped} unmappable row(s) dropped")


def down():
    with db.get_cursor() as cursor:
        restored = _restore(cursor)
    print(f"Migration {_NAME}: {restored} ref(s) restored to physical form")


def _looks_logical(ref: str, type_dir: str) -> bool:
    normalized = ref.replace("\\", "/")
    return normalized == type_dir or normalized.startswith(f"{type_dir}/")


def _best_present_location(cursor, model_id: str, model_type: str):
    cursor.execute(
        """
        SELECT ml.rel_path AS rel_path, mrb.position AS position
        FROM model_locations ml
        JOIN model_root_bindings mrb ON mrb.root_id = ml.root_id AND mrb.model_type = ml.model_type
        WHERE ml.model_id = ? AND ml.model_type = ? AND ml.status = 'present'
        ORDER BY mrb.position ASC, LENGTH(ml.rel_path) ASC, ml.rel_path ASC
        LIMIT 1
        """,
        (model_id, model_type),
    )
    return cursor.fetchone()


def _rewrite(cursor):
    cursor.execute(
        """
        SELECT ma.id AS availability_id, ma.model_id AS model_id, ma.ref AS ref,
               m.model_type AS model_type
        FROM model_availability ma
        JOIN models m ON m.id = ma.model_id
        JOIN backends b ON b.id = ma.backend_id
        WHERE b.driver = ?
        """,
        (_NATIVE_LOCAL_DRIVER,),
    )
    rows = cursor.fetchall()

    rewritten = 0
    dropped = 0
    for row in rows:
        model_type = row["model_type"]
        type_dir = MODEL_TYPE_TO_DIRECTORY.get(model_type)
        if type_dir is None or _looks_logical(row["ref"], type_dir):
            continue

        location = _best_present_location(cursor, row["model_id"], model_type)
        if location is None:
            cursor.execute("DELETE FROM model_availability WHERE id = ?", (row["availability_id"],))
            dropped += 1
            continue

        new_ref = f"{type_dir}/{location['rel_path']}"
        cursor.execute(
            "UPDATE model_availability SET ref = ? WHERE id = ?",
            (new_ref, row["availability_id"]),
        )
        rewritten += 1

    return rewritten, dropped


def _restore(cursor):
    cursor.execute(
        """
        SELECT ma.id AS availability_id, ma.ref AS ref, m.model_type AS model_type
        FROM model_availability ma
        JOIN models m ON m.id = ma.model_id
        JOIN backends b ON b.id = ma.backend_id
        WHERE b.driver = ?
        """,
        (_NATIVE_LOCAL_DRIVER,),
    )
    rows = cursor.fetchall()

    restored = 0
    for row in rows:
        model_type = row["model_type"]
        type_dir = MODEL_TYPE_TO_DIRECTORY.get(model_type)
        if type_dir is None or not _looks_logical(row["ref"], type_dir):
            continue

        rel_path = row["ref"][len(type_dir) + 1:]
        cursor.execute(
            """
            SELECT mr.path AS root_path, mrb.subdir AS subdir
            FROM model_locations ml
            JOIN model_roots mr ON mr.id = ml.root_id
            JOIN model_root_bindings mrb ON mrb.root_id = ml.root_id AND mrb.model_type = ml.model_type
            WHERE ml.model_type = ? AND ml.rel_path = ? AND ml.status = 'present'
            ORDER BY mrb.position ASC
            LIMIT 1
            """,
            (model_type, rel_path),
        )
        location = cursor.fetchone()
        if location is None:
            continue

        parts = [part for part in (location["subdir"], rel_path) if part]
        physical = "/".join([location["root_path"].rstrip("/")] + parts) if parts else location["root_path"]
        cursor.execute(
            "UPDATE model_availability SET ref = ? WHERE id = ?",
            (physical, row["availability_id"]),
        )
        restored += 1

    return restored
