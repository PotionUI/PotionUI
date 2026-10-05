import json
import os

from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.settings.records import Setting, SettingValueType
from src.platform.settings.runtime_flags import (
    APP_FLAGS,
    coerce_runtime_flag,
    seed_engine_flags_from_env,
    seed_value_from_env,
)
from src.platform.util.ids import generate_ulid

_NAME = "054_runtime_settings"

_NATIVE_ENGINE = "native"
_LOCAL_DRIVER = "native.local"
_ENGINE_FLAGS_FIELD = "engine_flags"

_RETIRED_SETTINGS = (
    ("native_attention_backend", "Pinned attention backend for the native engine (empty = auto): sdpa, sage, sage2, or flash"),
    ("native_torch_compile", "Regional torch.compile for the native engine (empty = follow $NATIVE_TORCH_COMPILE): on or off"),
    ("native_stream_prefetch", "Streaming layer prefetch under partial residency (empty = follow $NATIVE_STREAM_PREFETCH): on or off"),
)


def _retired_setting_values(cursor):
    values = {}
    for key, _ in _RETIRED_SETTINGS:
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row is None or row["value"] is None or not str(row["value"]).strip():
            continue
        try:
            values[key] = coerce_runtime_flag(key, row["value"])
        except ValueError:
            continue
    return values


def _seed_app_settings(cursor, env, now):
    for flag in APP_FLAGS:
        cursor.execute("SELECT 1 FROM settings WHERE key = ?", (flag.key,))
        if cursor.fetchone() is not None:
            continue
        seeded = seed_value_from_env(flag, env)
        value = Setting.serialize_value(flag.default if seeded is None else seeded, SettingValueType(flag.value_type))
        cursor.execute(
            """
            INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'SYSTEM', ?, ?)
            """,
            (generate_ulid(), flag.key, value, flag.value_type, flag.description, now, now),
        )


def _seed_native_backends(cursor, env, now):
    from_env = seed_engine_flags_from_env(env)
    from_local_settings = _retired_setting_values(cursor)
    cursor.execute("SELECT id, driver, config FROM backends WHERE engine = ?", (_NATIVE_ENGINE,))
    seeded = 0
    for row in cursor.fetchall():
        try:
            config = json.loads(row["config"]) if row["config"] else {}
        except ValueError:
            continue
        if not isinstance(config, dict) or _ENGINE_FLAGS_FIELD in config:
            continue
        flags = dict(from_env)
        if row["driver"] == _LOCAL_DRIVER:
            flags.update(from_local_settings)
        config[_ENGINE_FLAGS_FIELD] = flags
        cursor.execute(
            "UPDATE backends SET config = ?, updated_at = ? WHERE id = ?",
            (json.dumps(config), now, row["id"]),
        )
        seeded += 1
    return seeded


def up(environ=None):
    env = os.environ if environ is None else environ
    with db.get_cursor() as cursor:
        now = now_iso()
        _seed_app_settings(cursor, env, now)
        seeded = _seed_native_backends(cursor, env, now)
        for key, _ in _RETIRED_SETTINGS:
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
    print(f"Migration {_NAME}: seeded {seeded} native backend(s) and the profiling settings")


def down():
    with db.get_cursor() as cursor:
        now = now_iso()
        for flag in APP_FLAGS:
            cursor.execute("DELETE FROM settings WHERE key = ?", (flag.key,))
        for key, description in _RETIRED_SETTINGS:
            cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if cursor.fetchone() is None:
                cursor.execute(
                    """
                    INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                    VALUES (?, ?, '', 'string', ?, 'SYSTEM', ?, ?)
                    """,
                    (generate_ulid(), key, description, now, now),
                )
        cursor.execute("SELECT id, config FROM backends WHERE engine = ?", (_NATIVE_ENGINE,))
        for row in cursor.fetchall():
            try:
                config = json.loads(row["config"]) if row["config"] else {}
            except ValueError:
                continue
            if isinstance(config, dict) and config.pop(_ENGINE_FLAGS_FIELD, None) is not None:
                cursor.execute("UPDATE backends SET config = ? WHERE id = ?", (json.dumps(config), row["id"]))
    print(f"Migration {_NAME}: removed the profiling settings and the native backends' engine settings")
