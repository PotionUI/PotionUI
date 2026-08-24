"""Migration 002: rename the `clip` model type to `text_encoder`.

The depot's text-encoder bucket used to be called `clip` - the directory
`<models_root>/clip`, the model type string `clip`, the preset configuration
key `clip_tags`. It never only held CLIP: Gemma3, Qwen3, UMT5 and T5 encoders
all live there, so the name described one member of the set rather than the
set. The code-side rename (`DIRECTORY_TO_MODEL_TYPE`, `IOType.TEXT_ENCODER`,
the pipe port, the preset field values) lands with this migration; this file
moves the rows that already carry the old spelling.

THE DIRECTORY ON DISK IS NOT TOUCHED HERE. Renaming `<models_root>/clip` to
`<models_root>/text_encoders` is a manual deploy step the maintainer performs
alongside this migration - a migration that moved files would be undoable and
would run against whatever filesystem happened to be mounted. This migration
only rewrites what the database says those files are called.

WHAT MOVES

- `models.model_type`: `clip` -> `text_encoder`.
- `models.file_path` for exactly those rows: the depot's `clip` path segment
  becomes `text_encoders`.
- `model_hash_cache.path`, `downloads.destination_path`,
  `generations.form_data`: any stored path that sits under a depot `clip`
  directory gets the same segment swap.
- `llm_configurations.model`: adopted text encoders are stored depot-relative
  as `clip/<file>.safetensors` (see `src/features/llm/native_te_adoption.py`),
  so the leading `clip/` becomes `text_encoders/`.
- `presets.configuration`: the admin-set `clip_tags` key becomes
  `text_encoder_tags`, matching the preset YAML rename.
- `model_attribute_definitions.model_types`: the `clip` entry in that JSON
  array becomes `text_encoder`.

WHAT DELIBERATELY DOES NOT MOVE

- `model_availability.ref` names a file inside a *remote backend's* model
  tree (a ComfyUI server's own `clip` folder), not inside this app's depot.
  Rewriting it would point availability at a directory the remote does not
  have. It is re-derived by the next backend scan anyway.

PRECISION. Nothing here is a blind `REPLACE(col, 'clip', 'text_encoders')` -
a lora named `clip-fix.safetensors` or a video-clip upload must survive
untouched. Paths are only rewritten when they sit under a *known* depot
`clip` directory: the set of those directories is derived from the
`models_dir` setting and from the paths of the `clip` rows themselves, and a
value must equal that directory or start with it plus a separator to be
rewritten.

IDEMPOTENT. Every statement is conditional on the old spelling still being
present, so a second run touches nothing.
"""

import json
import logging

from src.platform.database.database import db

logger = logging.getLogger(__name__)

_OLD_TYPE = "clip"
_NEW_TYPE = "text_encoder"
_OLD_DIR = "clip"
_NEW_DIR = "text_encoders"
_OLD_CONFIG_KEY = "clip_tags"
_NEW_CONFIG_KEY = "text_encoder_tags"


def _depot_clip_dir(path: str) -> str | None:
    """The `<models_root>/clip` directory `path` sits under, or None.

    The depot is one first-level directory per model type, so the *first*
    `clip` segment is the bucket directory; anything deeper is a
    user-created subdirectory inside it and stays where it is.
    """
    parts = path.split("/")
    for index, part in enumerate(parts):
        if part == _OLD_DIR:
            return "/".join(parts[: index + 1])
    return None


def _swap_dir(clip_dir: str) -> str:
    return clip_dir[: -len(_OLD_DIR)] + _NEW_DIR


def _rewrite(value: str, clip_dirs: set) -> str | None:
    """`value` with its depot `clip` directory swapped, or None if `value`
    does not sit under one of `clip_dirs`."""
    for clip_dir in clip_dirs:
        if value == clip_dir:
            return _swap_dir(clip_dir)
        if value.startswith(clip_dir + "/"):
            return _swap_dir(clip_dir) + value[len(clip_dir):]
    return None


def _collect_clip_dirs(cursor) -> set:
    """Every depot `clip` directory this database knows about.

    Two sources, because either alone has a gap: the configured `models_dir`
    covers a depot whose text encoders were never indexed (a pending download
    still has somewhere to point), and the indexed rows cover a depot that was
    indexed under a `models_dir` that has since been changed.
    """
    clip_dirs = set()

    cursor.execute("SELECT value FROM settings WHERE key = 'models_dir'")
    for row in cursor.fetchall():
        root = (row[0] or "").rstrip("/")
        if root:
            clip_dirs.add(f"{root}/{_OLD_DIR}")
    clip_dirs.add(_OLD_DIR)

    cursor.execute(
        "SELECT file_path FROM models WHERE model_type = ? AND file_path IS NOT NULL",
        (_OLD_TYPE,),
    )
    for row in cursor.fetchall():
        clip_dir = _depot_clip_dir(row[0])
        if clip_dir:
            clip_dirs.add(clip_dir)

    return clip_dirs


def _rewrite_paths(cursor, table: str, column: str, key_column: str, clip_dirs: set) -> int:
    """Swap the depot directory in `table.column` for every row that sits
    under one of `clip_dirs`. Returns the number of rows changed."""
    cursor.execute(
        f"SELECT {key_column}, {column} FROM {table} "
        f"WHERE {column} IS NOT NULL AND {column} LIKE '%{_OLD_DIR}%'"
    )
    updates = []
    for key, value in cursor.fetchall():
        rewritten = _rewrite(value, clip_dirs)
        if rewritten is not None:
            updates.append((rewritten, key))
    if updates:
        cursor.executemany(
            f"UPDATE {table} SET {column} = ? WHERE {key_column} = ?", updates
        )
    return len(updates)


def _rewrite_json_paths(cursor, table: str, column: str, key_column: str, clip_dirs: set) -> int:
    """Same swap, applied to every string leaf of a JSON blob column."""

    def walk(node):
        if isinstance(node, str):
            rewritten = _rewrite(node, clip_dirs)
            return (rewritten, True) if rewritten is not None else (node, False)
        if isinstance(node, list):
            changed = False
            out = []
            for item in node:
                value, item_changed = walk(item)
                out.append(value)
                changed = changed or item_changed
            return out, changed
        if isinstance(node, dict):
            changed = False
            out = {}
            for key, item in node.items():
                value, item_changed = walk(item)
                out[key] = value
                changed = changed or item_changed
            return out, changed
        return node, False

    cursor.execute(
        f"SELECT {key_column}, {column} FROM {table} "
        f"WHERE {column} IS NOT NULL AND {column} LIKE '%{_OLD_DIR}%'"
    )
    updates = []
    for key, raw in cursor.fetchall():
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        rewritten, changed = walk(payload)
        if changed:
            updates.append((json.dumps(rewritten), key))
    if updates:
        cursor.executemany(
            f"UPDATE {table} SET {column} = ? WHERE {key_column} = ?", updates
        )
    return len(updates)


def _rename_preset_config_key(cursor) -> int:
    cursor.execute(
        "SELECT id, configuration FROM presets "
        f"WHERE configuration LIKE '%{_OLD_CONFIG_KEY}%'"
    )
    updates = []
    for preset_row_id, raw in cursor.fetchall():
        try:
            configuration = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(configuration, dict) or _OLD_CONFIG_KEY not in configuration:
            continue
        renamed = {
            (_NEW_CONFIG_KEY if key == _OLD_CONFIG_KEY else key): value
            for key, value in configuration.items()
        }
        updates.append((json.dumps(renamed), preset_row_id))
    if updates:
        cursor.executemany(
            "UPDATE presets SET configuration = ? WHERE id = ?", updates
        )
    return len(updates)


def _rename_attribute_model_types(cursor) -> int:
    cursor.execute(
        "SELECT id, model_types FROM model_attribute_definitions "
        f"WHERE model_types LIKE '%\"{_OLD_TYPE}\"%'"
    )
    updates = []
    for definition_id, raw in cursor.fetchall():
        try:
            model_types = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(model_types, list) or _OLD_TYPE not in model_types:
            continue
        renamed = [_NEW_TYPE if t == _OLD_TYPE else t for t in model_types]
        updates.append((json.dumps(renamed), definition_id))
    if updates:
        cursor.executemany(
            "UPDATE model_attribute_definitions SET model_types = ? WHERE id = ?",
            updates,
        )
    return len(updates)


def _rename_llm_te_references(cursor) -> int:
    cursor.execute(
        "SELECT id, model FROM llm_configurations WHERE model LIKE ?",
        (f"{_OLD_DIR}/%",),
    )
    updates = [
        (f"{_NEW_DIR}/{model[len(_OLD_DIR) + 1:]}", config_id)
        for config_id, model in cursor.fetchall()
    ]
    if updates:
        cursor.executemany(
            "UPDATE llm_configurations SET model = ? WHERE id = ?", updates
        )
    return len(updates)


def up():
    with db.get_cursor() as cursor:
        clip_dirs = _collect_clip_dirs(cursor)

        model_paths = _rewrite_paths(cursor, "models", "file_path", "id", clip_dirs)

        cursor.execute(
            "UPDATE models SET model_type = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE model_type = ?",
            (_NEW_TYPE, _OLD_TYPE),
        )
        retyped = cursor.rowcount

        hashes = _rewrite_paths(cursor, "model_hash_cache", "path", "path", clip_dirs)
        destinations = _rewrite_paths(
            cursor, "downloads", "destination_path", "id", clip_dirs
        )
        form_data = _rewrite_json_paths(
            cursor, "generations", "form_data", "id", clip_dirs
        )
        llm_models = _rename_llm_te_references(cursor)
        preset_configs = _rename_preset_config_key(cursor)
        attribute_defs = _rename_attribute_model_types(cursor)

        print(
            f"Migration 002_rename_clip_to_text_encoder: retyped {retyped} models, "
            f"rewrote {model_paths} model paths, {hashes} hash-cache paths, "
            f"{destinations} download destinations, {form_data} generation form_data "
            f"blobs, {llm_models} LLM text-encoder references, {preset_configs} preset "
            f"configurations, {attribute_defs} model-attribute definitions"
        )


def down():
    print(
        "Migration 002_rename_clip_to_text_encoder: no-op (the code no longer "
        "knows the 'clip' model type, so reverting the rows would orphan them)"
    )
