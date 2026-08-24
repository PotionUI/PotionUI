"""
Marketplace credentials move from global settings onto the providers that use them.

`civitai_api_key`, `hf_api_key` and `ca_api_key` were global SYSTEM settings. A
provider is a plugin that knows how to talk to one marketplace, and it already
declares its own credentials in its manifest (see `civitai-provider`'s `api_key`
setting) and authenticates downloads through `get_download_headers()`. Keeping a
second copy in global settings meant two values that could disagree.

  - `civitai_api_key` -> the `civitai-provider` plugin's `api_key` setting
    (carried over here if the plugin has no value yet).
  - `hf_api_key` -> deleted. No HuggingFace provider plugin exists; public files
    download without credentials, and a gated-model provider would own its own key.
  - `ca_api_key` -> deleted. It never had a single reader.

Also retypes the `action.fetch_civitai_metadata` automation node to the
provider-agnostic `action.fetch_provider_metadata`, since core no longer names a
marketplace.

See docs/providers.md.
"""

import json

from src.platform.database.database import db

_DEAD_KEYS = ("hf_api_key", "ca_api_key")
_CIVITAI_PLUGIN_ID = "civitai-provider"


def _plugin_settings_table_exists(cursor) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='plugin_settings'"
    )
    return cursor.fetchone() is not None


def up():
    with db.get_cursor() as cursor:
        # 1. Carry the CivitAI key over to the provider plugin, if it has none.
        cursor.execute("SELECT value FROM settings WHERE key = 'civitai_api_key'")
        row = cursor.fetchone()
        civitai_key = (row["value"] or "").strip() if row else ""

        if civitai_key and _plugin_settings_table_exists(cursor):
            # Global settings had no per-user scope, so this becomes the plugin's
            # global (user_id IS NULL) value. Marked secret: it is an API key.
            cursor.execute(
                """
                SELECT 1 FROM plugin_settings
                WHERE plugin_id = ? AND user_id IS NULL AND setting_key = 'api_key'
                """,
                (_CIVITAI_PLUGIN_ID,),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    """
                    INSERT INTO plugin_settings (plugin_id, user_id, setting_key, setting_value, is_secret)
                    VALUES (?, NULL, 'api_key', ?, 1)
                    """,
                    (_CIVITAI_PLUGIN_ID, civitai_key),
                )
                print(f"Migration 071: carried civitai_api_key over to {_CIVITAI_PLUGIN_ID}")

        cursor.execute("DELETE FROM settings WHERE key = 'civitai_api_key'")
        cursor.execute("DELETE FROM settings WHERE key IN (?, ?)", _DEAD_KEYS)

        # 2. The automation node no longer names a marketplace.
        cursor.execute("SELECT id, graph FROM automations")
        for row in cursor.fetchall():
            graph = json.loads(row["graph"]) if row["graph"] else {}
            changed = False
            for node in graph.get("nodes", []):
                if node.get("type") == "action.fetch_civitai_metadata":
                    node["type"] = "action.fetch_provider_metadata"
                    config = node.setdefault("config", {})
                    config.setdefault("provider", "civitai")
                    config.pop("sha256", None)
                    changed = True
            if changed:
                cursor.execute(
                    "UPDATE automations SET graph = ? WHERE id = ?",
                    (json.dumps(graph), row["id"]),
                )
                print(f"Migration 071: retyped fetch_civitai_metadata in automation {row['id']}")

        print("Migration 071: marketplace credentials now live on their providers")


def down():
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT OR REPLACE INTO settings (id, key, value, value_type, description, type)
            VALUES ('setting_civitai_api_key', 'civitai_api_key', '', 'string',
                    'CivitAI API key', 'SYSTEM')
            """
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO settings (id, key, value, value_type, description, type)
            VALUES ('setting_hf_api_key', 'hf_api_key', '', 'string',
                    'Hugging Face API key', 'SYSTEM')
            """
        )

        cursor.execute("SELECT id, graph FROM automations")
        for row in cursor.fetchall():
            graph = json.loads(row["graph"]) if row["graph"] else {}
            changed = False
            for node in graph.get("nodes", []):
                if node.get("type") == "action.fetch_provider_metadata":
                    node["type"] = "action.fetch_civitai_metadata"
                    node.get("config", {}).pop("provider", None)
                    changed = True
            if changed:
                cursor.execute(
                    "UPDATE automations SET graph = ? WHERE id = ?",
                    (json.dumps(graph), row["id"]),
                )

        print("Migration 071: restored global marketplace API key settings")
