"""
Move GPU/performance settings onto the native backend.

`device`, `dtype` and `gpu_max_vram` were global SYSTEM settings, but only the
native engine ever consulted them: they appear in `content/presets/marketplace/**` pipelines and
in GpuManager's budget, and in no ComfyUI preset. A ComfyUI server picks its own
device and manages its own VRAM. They are therefore configuration of the *native
backend*, not of the application, and they move into its config blob.

`file_storage_directory` stays a global setting - both engines use it (it is where
files land on this host).

`attention_mechanism` is deleted outright: seeded by migration 033, read by
`SettingsManager.get_attention_mechanism()`, which nothing ever called.

See docs/backends.md.
"""

import json

from src.platform.database.database import db

_MOVED_KEYS = ("device", "dtype", "gpu_max_vram")
_DEAD_KEYS = ("attention_mechanism",)

_DEFAULTS = {"device": "cuda", "dtype": "float16", "gpu_max_vram": 8}


def _coerce(key, raw):
    """Settings are stored as text; the backend config expects real types."""
    if key == "gpu_max_vram":
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return _DEFAULTS[key]
    return raw or _DEFAULTS[key]


def up():
    """Fold the GPU settings into the native backend's config."""
    with db.get_cursor() as cursor:
        cursor.execute(
            "SELECT key, value FROM settings WHERE key IN (?, ?, ?)", _MOVED_KEYS
        )
        values = {row["key"]: _coerce(row["key"], row["value"]) for row in cursor.fetchall()}

        # Anything absent from the settings table keeps the config class's default.
        for key, default in _DEFAULTS.items():
            values.setdefault(key, default)

        cursor.execute("SELECT id, config FROM backends WHERE engine = 'native'")
        for row in cursor.fetchall():
            config = json.loads(row["config"]) if row["config"] else {}
            config.update(values)
            cursor.execute(
                "UPDATE backends SET config = ? WHERE id = ?",
                (json.dumps(config), row["id"]),
            )

        cursor.execute("DELETE FROM settings WHERE key IN (?, ?, ?)", _MOVED_KEYS)
        cursor.execute("DELETE FROM settings WHERE key = ?", _DEAD_KEYS)

        print(
            f"Migration 070: moved {sorted(values)} onto the native backend; "
            f"deleted {list(_DEAD_KEYS)}"
        )


def down():
    """Restore the GPU settings as global settings."""
    descriptions = {
        "device": "Configuration for device",
        "dtype": "Data type for model tensors",
        "gpu_max_vram": "Maximum GPU VRAM usage in GB",
    }
    value_types = {"device": "string", "dtype": "string", "gpu_max_vram": "integer"}

    with db.get_cursor() as cursor:
        cursor.execute("SELECT config FROM backends WHERE engine = 'native' LIMIT 1")
        row = cursor.fetchone()
        config = json.loads(row["config"]) if row and row["config"] else {}

        for key in _MOVED_KEYS:
            value = config.get(key, _DEFAULTS[key])
            cursor.execute(
                """
                INSERT OR REPLACE INTO settings (id, key, value, value_type, description, type)
                VALUES (?, ?, ?, ?, ?, 'SYSTEM')
                """,
                (f"setting_{key}", key, str(value), value_types[key], descriptions[key]),
            )

        cursor.execute(
            """
            INSERT OR REPLACE INTO settings (id, key, value, value_type, description, type)
            VALUES ('setting_attention_mechanism', 'attention_mechanism', 'flash_attention',
                    'string', 'Attention mechanism', 'SYSTEM')
            """
        )

        # Strip them back out of the backend config.
        cursor.execute("SELECT id, config FROM backends WHERE engine = 'native'")
        for row in cursor.fetchall():
            config = json.loads(row["config"]) if row["config"] else {}
            for key in _MOVED_KEYS:
                config.pop(key, None)
            cursor.execute(
                "UPDATE backends SET config = ? WHERE id = ?",
                (json.dumps(config), row["id"]),
            )

        print("Migration 070: restored GPU settings as global settings")
