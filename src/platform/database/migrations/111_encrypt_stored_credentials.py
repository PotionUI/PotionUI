"""Encrypt stored credentials at rest and add a settings audit trail.

Two jobs:

1. Create ``plugin_setting_audit`` - who changed which plugin setting, when.
   Never the value.
2. Encrypt what is already stored: plugin settings flagged ``is_secret``, and
   every ``llm_configurations.api_key``. Both are rewritten as envelopes, so a
   user who configured credentials before this migration does not have to
   re-enter them.

Backend configs are handled separately, by BackendConfigManager.encrypt_stored_credentials()
at startup: which config fields are secret is declared by the engine's config
class, and plugin-contributed engines only exist once the plugin registry has
been built - which happens after migrations run. Guessing here would either miss
a credential or corrupt an ordinary field.
"""

import logging

from src.platform.database.database import db
from src.platform.security.secrets import get_secret_cipher

logger = logging.getLogger(__name__)


def _encrypt_plugin_settings(cursor, cipher) -> int:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='plugin_settings'"
    )
    if not cursor.fetchone():
        return 0

    cursor.execute(
        "SELECT id, plugin_id, setting_key, setting_value FROM plugin_settings "
        "WHERE is_secret = 1 AND setting_value IS NOT NULL AND setting_value != ''"
    )
    rows = cursor.fetchall()
    encrypted = 0
    for row in rows:
        value = row['setting_value']
        if cipher.is_encrypted(value):
            continue
        cursor.execute(
            "UPDATE plugin_settings SET setting_value = ? WHERE id = ?",
            (cipher.encrypt(value), row['id']),
        )
        encrypted += 1
    return encrypted


def _encrypt_llm_api_keys(cursor, cipher) -> int:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_configurations'"
    )
    if not cursor.fetchone():
        return 0

    cursor.execute(
        "SELECT id, api_key FROM llm_configurations "
        "WHERE api_key IS NOT NULL AND api_key != ''"
    )
    rows = cursor.fetchall()
    encrypted = 0
    for row in rows:
        value = row['api_key']
        if cipher.is_encrypted(value):
            continue
        cursor.execute(
            "UPDATE llm_configurations SET api_key = ? WHERE id = ?",
            (cipher.encrypt(value), row['id']),
        )
        encrypted += 1
    return encrypted


def up():
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS plugin_setting_audit (
                id TEXT PRIMARY KEY,
                plugin_id TEXT NOT NULL,
                setting_key TEXT NOT NULL,
                scope_user_id TEXT,
                actor_user_id TEXT,
                actor_username TEXT,
                action TEXT NOT NULL,
                is_secret INTEGER NOT NULL DEFAULT 0,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_plugin_setting_audit_plugin "
            "ON plugin_setting_audit (plugin_id, changed_at)"
        )

        cipher = get_secret_cipher()
        settings_count = _encrypt_plugin_settings(cursor, cipher)
        llm_count = _encrypt_llm_api_keys(cursor, cipher)

    if settings_count or llm_count:
        logger.info(
            "Encrypted %d plugin credential(s) and %d LLM API key(s) at rest.",
            settings_count, llm_count,
        )


def down():
    """Drops the audit table only.

    Decrypting back to plaintext is deliberately not offered: a downgrade path
    that rewrites every credential in the clear is a worse outcome than a failed
    downgrade, and the read path still accepts plaintext values anyway.
    """
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_plugin_setting_audit_plugin")
        cursor.execute("DROP TABLE IF EXISTS plugin_setting_audit")
