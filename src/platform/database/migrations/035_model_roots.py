import json
import os
import posixpath
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_roots import (
    HOME_ROOT_ID,
    default_case_insensitive,
    is_windows,
    root_path_key,
)
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY, MODEL_TYPES
from src.platform.util.ids import generate_ulid

_NAME = "035_model_roots"

_EXTERNAL_PATH_SETTING_KEY = "models_location_external_path"
_OVERRIDES_SETTING_KEY = "models_location_overrides"
_UNPLACED_SETTING_KEY = "model_roots_unplaced"
_UNPLACED_SETTING_DESCRIPTION = (
    "Model files found on disk that are not under any known model root, "
    "grouped by parent directory (JSON)."
)


def up():
    with db.get_cursor() as cursor:
        _create_tables(cursor)

        now = now_iso()
        repo_root_posix = _normalize(str(Path.cwd()))
        case_insensitive = default_case_insensitive()

        home_path = _get_setting(cursor, "models_dir", "models")
        home_abs = _to_absolute_posix(home_path, repo_root_posix)
        home_key = root_path_key(home_abs, case_insensitive=case_insensitive)

        cursor.execute(
            """
            INSERT OR IGNORE INTO model_roots
                (id, label, path, path_key, kind, read_only, case_insensitive, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'home', 0, ?, 'online', ?, ?)
            """,
            (HOME_ROOT_ID, "PotionUI models", home_path, home_key, int(case_insensitive), now, now),
        )

        bindings_seeded = _seed_home_bindings(cursor)

        external_path = _get_setting(cursor, _EXTERNAL_PATH_SETTING_KEY, None)
        overrides = _get_json_setting(cursor, _OVERRIDES_SETTING_KEY, {})

        locations_created, unplaced = _backfill_locations(
            cursor, repo_root_posix, home_abs, case_insensitive, external_path, overrides, now
        )
        _upsert_setting(
            cursor, _UNPLACED_SETTING_KEY, json.dumps(unplaced), "json", _UNPLACED_SETTING_DESCRIPTION, now
        )

    print(
        f"Migration {_NAME}: home root ready, {bindings_seeded} binding(s) seeded, "
        f"{locations_created} location(s) backfilled, {len(unplaced)} unplaced group(s)"
    )


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS model_locations")
        cursor.execute("DROP TABLE IF EXISTS model_root_bindings")
        cursor.execute("DROP TABLE IF EXISTS model_roots")
        cursor.execute("DELETE FROM settings WHERE key = ?", (_UNPLACED_SETTING_KEY,))
    print(f"Migration {_NAME}: dropped model_locations, model_root_bindings, model_roots")


def _create_tables(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS model_roots (
            id                TEXT PRIMARY KEY,
            label             TEXT NOT NULL,
            path              TEXT NOT NULL,
            path_key          TEXT NOT NULL UNIQUE,
            kind              TEXT NOT NULL,
            read_only         INTEGER NOT NULL DEFAULT 0,
            case_insensitive  INTEGER NOT NULL,
            state             TEXT NOT NULL DEFAULT 'online',
            state_reason      TEXT,
            state_checked_at  TIMESTAMP,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS model_root_bindings (
            root_id     TEXT NOT NULL REFERENCES model_roots(id) ON DELETE CASCADE,
            model_type  TEXT NOT NULL,
            subdir      TEXT NOT NULL,
            position    INTEGER NOT NULL,
            is_write    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (root_id, model_type)
        )
        """
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_binding_type_position ON model_root_bindings (model_type, position)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_binding_write_root ON model_root_bindings (model_type) "
        "WHERE is_write = 1"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS model_locations (
            id          TEXT PRIMARY KEY,
            model_id    TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
            root_id     TEXT NOT NULL REFERENCES model_roots(id) ON DELETE CASCADE,
            model_type  TEXT NOT NULL,
            rel_path    TEXT NOT NULL,
            rel_key     TEXT NOT NULL,
            size        INTEGER,
            mtime_ns    INTEGER,
            sha256      TEXT,
            status      TEXT NOT NULL DEFAULT 'present',
            seen_at     TIMESTAMP,
            UNIQUE (root_id, model_type, rel_key)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_locations_model ON model_locations (model_id)"
    )


def _seed_home_bindings(cursor) -> int:
    cursor.execute(
        "SELECT model_type, MAX(position) AS max_position FROM model_root_bindings GROUP BY model_type"
    )
    max_positions = {row["model_type"]: row["max_position"] for row in cursor.fetchall()}
    cursor.execute("SELECT model_type FROM model_root_bindings WHERE is_write = 1")
    write_types = {row["model_type"] for row in cursor.fetchall()}

    seeded = 0
    for model_type in MODEL_TYPES:
        cursor.execute(
            "SELECT 1 FROM model_root_bindings WHERE root_id = ? AND model_type = ?",
            (HOME_ROOT_ID, model_type),
        )
        if cursor.fetchone() is not None:
            continue
        position = max_positions.get(model_type, -1) + 1
        is_write = model_type not in write_types
        cursor.execute(
            """
            INSERT OR IGNORE INTO model_root_bindings (root_id, model_type, subdir, position, is_write)
            VALUES (?, ?, ?, ?, ?)
            """,
            (HOME_ROOT_ID, model_type, MODEL_TYPE_TO_DIRECTORY[model_type], position, int(is_write)),
        )
        max_positions[model_type] = position
        if is_write:
            write_types.add(model_type)
        seeded += 1
    return seeded


def _normalize(raw: str) -> str:
    return posixpath.normpath(raw.replace("\\", "/"))


def _is_absolute(raw: str) -> bool:
    if raw.startswith("/"):
        return True
    if raw.startswith("//"):
        return True
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha():
        return True
    return False


def _to_absolute_posix(raw: str, base_posix: str) -> str:
    normalized = _normalize(raw)
    if _is_absolute(normalized):
        return normalized
    return f"{base_posix.rstrip('/')}/{normalized}"


def _key_os_name() -> Optional[str]:
    return "nt" if is_windows() else None


def _realpath_posix(path_posix: str) -> Optional[str]:
    try:
        real = os.path.realpath(path_posix)
    except OSError:
        return None
    return _normalize(real)


def _strip_prefix(path_posix: str, prefix_posix: str, *, case_insensitive: bool = False) -> Optional[str]:
    prefix = prefix_posix.rstrip("/")
    effective_case_insensitive = case_insensitive or is_windows()
    os_name = _key_os_name()
    path_key = root_path_key(path_posix, case_insensitive=effective_case_insensitive, os_name=os_name)
    prefix_key = root_path_key(prefix, case_insensitive=effective_case_insensitive, os_name=os_name)
    if path_key == prefix_key:
        return ""
    if path_key.startswith(prefix_key + "/"):
        return path_posix[len(prefix) + 1:]
    return None


def _map_to_home(resolved_file: str, home_posix: str, type_dir: str, case_insensitive: bool = False) -> Optional[str]:
    base = f"{home_posix.rstrip('/')}/{type_dir}"
    return _strip_prefix(resolved_file, base, case_insensitive=case_insensitive)


def _map_through_symlink_targets(
    resolved_file: str,
    home_posix: str,
    type_dir: str,
    external_path: Optional[str],
    overrides: Dict[str, str],
    case_insensitive: bool = False,
) -> Optional[str]:
    candidates: List[str] = []
    override = overrides.get(type_dir) if overrides else None
    if override:
        candidates.append(_normalize(override))
    if external_path:
        candidates.append(f"{_normalize(external_path).rstrip('/')}/{type_dir}")

    link_path = Path(home_posix) / type_dir
    try:
        if link_path.is_symlink():
            candidates.append(_normalize(str(link_path.resolve())))
    except OSError:
        pass

    for candidate in candidates:
        rel = _strip_prefix(resolved_file, candidate, case_insensitive=case_insensitive)
        if rel is not None:
            return rel

    real_file = _realpath_posix(resolved_file)
    if real_file is not None:
        for candidate in candidates:
            real_candidate = _realpath_posix(candidate)
            if real_candidate is None:
                continue
            rel = _strip_prefix(real_file, real_candidate, case_insensitive=case_insensitive)
            if rel is not None:
                return rel
    return None


def _posix_dirname(path_posix: str) -> str:
    if "/" not in path_posix:
        return path_posix
    head = path_posix.rsplit("/", 1)[0]
    return head or "/"


def _rel_key(rel_path: str, case_insensitive: bool) -> str:
    key = unicodedata.normalize("NFC", rel_path)
    return key.casefold() if case_insensitive else key


def _get_setting(cursor, key: str, default: Optional[str]) -> Optional[str]:
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    if row is None or row["value"] in (None, ""):
        return default
    return row["value"]


def _get_json_setting(cursor, key: str, default: Any) -> Any:
    raw = _get_setting(cursor, key, None)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _upsert_setting(cursor, key: str, value: str, value_type: str, description: str, now: str) -> None:
    cursor.execute("SELECT id FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    if row is not None:
        cursor.execute("UPDATE settings SET value = ?, updated_at = ? WHERE id = ?", (value, now, row["id"]))
        return
    cursor.execute(
        """
        INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'SYSTEM', ?, ?)
        """,
        (generate_ulid(), key, value, value_type, description, now, now),
    )


def _backfill_locations(
    cursor,
    repo_root_posix: str,
    home_posix: str,
    case_insensitive: bool,
    external_path: Optional[str],
    overrides: Dict[str, str],
    now: str,
) -> Tuple[int, List[Dict[str, Any]]]:
    cursor.execute(
        "SELECT id, model_type, file_path, file_size, sha256, is_available FROM models "
        "WHERE file_path IS NOT NULL AND file_path != ''"
    )
    rows = cursor.fetchall()

    unplaced_groups: Dict[str, Dict[str, Any]] = {}
    locations_created = 0

    for row in rows:
        model_id = row["id"]
        model_type = row["model_type"]
        file_path = row["file_path"]
        type_dir = MODEL_TYPE_TO_DIRECTORY.get(model_type)
        if type_dir is None:
            continue

        cursor.execute("SELECT 1 FROM model_locations WHERE model_id = ?", (model_id,))
        if cursor.fetchone() is not None:
            continue

        resolved_file = _to_absolute_posix(file_path, repo_root_posix)
        rel = _map_to_home(resolved_file, home_posix, type_dir, case_insensitive)
        if rel is None:
            rel = _map_through_symlink_targets(
                resolved_file, home_posix, type_dir, external_path, overrides, case_insensitive
            )

        if rel is None:
            parent = _posix_dirname(resolved_file)
            entry = unplaced_groups.setdefault(parent, {"dir": parent, "count": 0, "types": set()})
            entry["count"] += 1
            entry["types"].add(model_type)
            continue

        cursor.execute("SELECT mtime_ns FROM model_hash_cache WHERE path = ?", (file_path,))
        cache_row = cursor.fetchone()
        mtime_ns = cache_row["mtime_ns"] if cache_row is not None else None

        cursor.execute(
            """
            INSERT OR IGNORE INTO model_locations
                (id, model_id, root_id, model_type, rel_path, rel_key, size, mtime_ns, sha256, status, seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generate_ulid(),
                model_id,
                HOME_ROOT_ID,
                model_type,
                rel,
                _rel_key(rel, case_insensitive),
                row["file_size"],
                mtime_ns,
                row["sha256"],
                "present" if row["is_available"] else "missing",
                now,
            ),
        )
        locations_created += 1

    unplaced = [
        {"dir": entry["dir"], "count": entry["count"], "types": sorted(entry["types"])}
        for entry in sorted(unplaced_groups.values(), key=lambda item: item["dir"])
    ]
    return locations_created, unplaced
