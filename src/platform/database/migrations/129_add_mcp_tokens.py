"""Migration 129: MCP (Model Context Protocol) support.

`mcp_tokens` holds one row per minted token: the plaintext is shown to the
user exactly once at creation and never stored — only `token_hash` (a SHA-256
digest, see src.features.mcp.manager for the rationale) and `token_prefix`
(first 12 chars of the plaintext, for display) persist. `revoked_at` is a
soft-delete so a revoked token's audit trail (name, created_at, last_used_at)
survives.

Two settings gate the MCP surface at request time: `mcp_enabled` (SYSTEM,
default off — an admin must opt the instance in) and `mcp_user_enabled`
(USER, default on — an admin can then turn it off for individual users via
the per-user override, the same USER-scope mechanism every other per-user
setting uses).
"""

import random
import string
import time

from src.platform.database.database import db

_MCP_ENABLED_KEY = "mcp_enabled"
_MCP_USER_ENABLED_KEY = "mcp_user_enabled"


def _generate_id():
    timestamp = int(time.time() * 1000)
    randomness = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    return f"{timestamp:013d}{randomness}"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mcp_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                token_prefix TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked_at TEXT
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcp_tokens_user ON mcp_tokens(user_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcp_tokens_hash ON mcp_tokens(token_hash)"
        )

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if not cursor.fetchone():
            return

        cursor.execute(
            """
            INSERT OR IGNORE INTO settings (
                id, key, value, value_type, description, type
            ) VALUES (?, ?, 'false', 'boolean', ?, 'SYSTEM')
            """,
            [
                _generate_id(),
                _MCP_ENABLED_KEY,
                "Whether the MCP (Model Context Protocol) server endpoint is reachable at all.",
            ],
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO settings (
                id, key, value, value_type, description, type
            ) VALUES (?, ?, 'true', 'boolean', ?, 'USER')
            """,
            [
                _generate_id(),
                _MCP_USER_ENABLED_KEY,
                "Whether this user's MCP tokens are allowed to authenticate. "
                "Admin-controlled per user.",
            ],
        )


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_mcp_tokens_hash")
        cursor.execute("DROP INDEX IF EXISTS idx_mcp_tokens_user")
        cursor.execute("DROP TABLE IF EXISTS mcp_tokens")
        cursor.execute("DELETE FROM settings WHERE key IN (?, ?)", [_MCP_ENABLED_KEY, _MCP_USER_ENABLED_KEY])
