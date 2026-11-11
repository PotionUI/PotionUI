import json

from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

_NAME = "095_api_keys"

_TABLES = (
    "CREATE TABLE IF NOT EXISTS api_keys ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "name TEXT NOT NULL, "
    "secret_hash TEXT NOT NULL UNIQUE, "
    "display_prefix TEXT NOT NULL, "
    "last_four TEXT, "
    "scopes TEXT NOT NULL, "
    "created_at TEXT NOT NULL, "
    "expires_at TEXT, "
    "last_used_at TEXT, "
    "last_used_via TEXT, "
    "revoked_at TEXT, "
    "revoked_by TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys (user_id, revoked_at)",
    "CREATE TABLE IF NOT EXISTS api_key_events ("
    "id TEXT PRIMARY KEY, "
    "key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE, "
    "owner_id TEXT NOT NULL, "
    "actor_id TEXT, "
    "action TEXT NOT NULL, "
    "detail TEXT, "
    "created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_api_key_events_key ON api_key_events (key_id, created_at)",
    "CREATE TABLE IF NOT EXISTS api_key_user_access ("
    "user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, "
    "keys_enabled INTEGER NOT NULL DEFAULT 1, "
    "mcp_enabled INTEGER NOT NULL DEFAULT 1, "
    "updated_at TEXT, "
    "updated_by TEXT)",
    "CREATE TABLE IF NOT EXISTS mcp_tool_ceiling ("
    "tool_name TEXT PRIMARY KEY, "
    "enabled INTEGER NOT NULL, "
    "updated_at TEXT, "
    "updated_by TEXT)",
    "CREATE TABLE IF NOT EXISTS mcp_tool_defaults ("
    "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "tool_name TEXT NOT NULL, "
    "enabled INTEGER NOT NULL, "
    "updated_at TEXT, "
    "PRIMARY KEY (user_id, tool_name))",
    "CREATE TABLE IF NOT EXISTS mcp_tool_default_policy ("
    "user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, "
    "policy TEXT NOT NULL, "
    "updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS mcp_key_tool_overrides ("
    "key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE, "
    "tool_name TEXT NOT NULL, "
    "updated_at TEXT, "
    "PRIMARY KEY (key_id, tool_name))",
)

_SETTINGS = (
    (
        "api_keys_enabled", "true", "boolean",
        "Whether users may create and use API keys for the HTTP API and MCP.",
    ),
    (
        "api_key_scopes_allowed", json.dumps(["read", "generate", "content", "mcp"]), "json",
        "Scopes users may grant to an API key. Read is always allowed; add admin to let administrators create admin keys.",
    ),
    (
        "api_key_max_lifetime_days", "0", "integer",
        "Longest expiry a new API key may get, in days. 0 means no limit. Keys with the admin scope are capped at 90 days regardless.",
    ),
    (
        "mcp_tools_ceiling_policy", "all_on", "string",
        "Which MCP tools are available to users when the administrator has not decided on them: all_on or read_only_on.",
    ),
)

_COPY_MCP_TOKENS = (
    "INSERT OR IGNORE INTO api_keys ("
    "id, user_id, name, secret_hash, display_prefix, last_four, scopes, created_at, "
    "expires_at, last_used_at, last_used_via, revoked_at, revoked_by) "
    "SELECT t.id, t.user_id, t.name, t.token_hash, t.token_prefix, NULL, '[\"mcp\"]', t.created_at, "
    "NULL, t.last_used_at, CASE WHEN t.last_used_at IS NULL THEN NULL ELSE 'mcp' END, "
    "t.revoked_at, CASE WHEN t.revoked_at IS NULL THEN NULL ELSE t.user_id END "
    "FROM mcp_tokens t JOIN users u ON u.id = t.user_id"
)

_MOVE_USER_SWITCH = (
    "INSERT OR IGNORE INTO api_key_user_access (user_id, keys_enabled, mcp_enabled, updated_at, updated_by) "
    "SELECT us.user_id, 1, 0, ?, NULL "
    "FROM user_settings us "
    "JOIN settings s ON s.id = us.setting_id "
    "JOIN users u ON u.id = us.user_id "
    "WHERE s.key = 'mcp_user_enabled' AND lower(trim(us.value)) IN ('false', '0')"
)


def _table_exists(cursor, table):
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
    return cursor.fetchone() is not None


def up():
    now = now_iso()
    with db.get_cursor() as cursor:
        for statement in _TABLES:
            cursor.execute(statement)

        copied = 0
        if _table_exists(cursor, "mcp_tokens"):
            cursor.execute(_COPY_MCP_TOKENS)
            copied = cursor.rowcount
            cursor.execute("DROP TABLE mcp_tokens")

        cursor.execute(_MOVE_USER_SWITCH, (now,))
        moved = cursor.rowcount
        cursor.execute(
            "DELETE FROM user_settings WHERE setting_id IN (SELECT id FROM settings WHERE key = 'mcp_user_enabled')"
        )
        cursor.execute("DELETE FROM settings WHERE key = 'mcp_user_enabled'")

        for key, value, value_type, description in _SETTINGS:
            cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if cursor.fetchone() is None:
                cursor.execute(
                    "INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'SYSTEM', ?, ?)",
                    (generate_ulid(), key, value, value_type, description, now, now),
                )
    print(
        f"Migration {_NAME}: api key tables ready, {copied} MCP tokens became keys, "
        f"{moved} users switched off MCP moved to api_key_user_access"
    )
