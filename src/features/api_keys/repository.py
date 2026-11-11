import json
from typing import Dict, Iterable, List, Optional, Tuple

from src.features.api_keys.records import ApiKey, ApiKeyEvent, UserApiAccess
from src.platform.database import get_cursor
from src.platform.util.ids import generate_ulid

_COLUMNS = (
    "id, user_id, name, secret_hash, display_prefix, last_four, scopes, created_at, "
    "expires_at, last_used_at, last_used_via, revoked_at, revoked_by"
)

_STATUS_CLAUSES = {
    "active": "revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
    "expired": "revoked_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?",
    "revoked": "revoked_at IS NOT NULL",
}


class ApiKeyRepository:
    def create(self, key: ApiKey) -> ApiKey:
        with get_cursor() as cursor:
            cursor.execute(
                f"INSERT INTO api_keys ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key.id, key.user_id, key.name, key.secret_hash, key.display_prefix, key.last_four,
                    json.dumps(key.scope_values), key.created_at, key.expires_at, key.last_used_at,
                    key.last_used_via, key.revoked_at, key.revoked_by,
                ),
            )
        return key

    def get_by_id(self, key_id: str) -> Optional[ApiKey]:
        with get_cursor() as cursor:
            cursor.execute(f"SELECT {_COLUMNS} FROM api_keys WHERE id = ?", (key_id,))
            row = cursor.fetchone()
            return ApiKey.from_row(row) if row else None

    def get_by_hash(self, secret_hash: str) -> Optional[ApiKey]:
        with get_cursor() as cursor:
            cursor.execute(f"SELECT {_COLUMNS} FROM api_keys WHERE secret_hash = ?", (secret_hash,))
            row = cursor.fetchone()
            return ApiKey.from_row(row) if row else None

    def list_for_user(self, user_id: str) -> List[ApiKey]:
        with get_cursor() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM api_keys WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                (user_id,),
            )
            return [ApiKey.from_row(row) for row in cursor.fetchall()]

    def list_page(
        self, status: str, user_id: Optional[str], now: str, limit: int, offset: int
    ) -> Tuple[List[ApiKey], int]:
        clauses: List[str] = []
        params: List[object] = []
        if status in _STATUS_CLAUSES:
            clauses.append(_STATUS_CLAUSES[status])
            if status != "revoked":
                params.append(now)
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM api_keys {where}", params)
            total = cursor.fetchone()[0]
            cursor.execute(
                f"SELECT {_COLUMNS} FROM api_keys {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )
            return [ApiKey.from_row(row) for row in cursor.fetchall()], total

    def update(self, key_id: str, name: str, scopes_json: str) -> bool:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE api_keys SET name = ?, scopes = ? WHERE id = ? AND revoked_at IS NULL",
                (name, scopes_json, key_id),
            )
            return cursor.rowcount > 0

    def revoke(self, key_id: str, revoked_by: str, revoked_at: str) -> bool:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE api_keys SET revoked_at = ?, revoked_by = ? WHERE id = ? AND revoked_at IS NULL",
                (revoked_at, revoked_by, key_id),
            )
            return cursor.rowcount > 0

    def touch_last_used(self, key_id: str, hour: str, via: str) -> bool:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE api_keys SET last_used_at = ?, last_used_via = ? "
                "WHERE id = ? AND (last_used_at IS NULL OR last_used_at < ?)",
                (hour, via, key_id, hour),
            )
            return cursor.rowcount > 0

    def usernames(self, user_ids: Iterable[str]) -> Dict[str, str]:
        ids = sorted({user_id for user_id in user_ids if user_id})
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with get_cursor() as cursor:
            cursor.execute(f"SELECT id, username FROM users WHERE id IN ({placeholders})", ids)
            return {row["id"]: row["username"] for row in cursor.fetchall()}


class ApiKeyEventRepository:
    def add(self, key_id: str, owner_id: str, actor_id: Optional[str], action: str, detail: dict, created_at: str) -> None:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO api_key_events (id, key_id, owner_id, actor_id, action, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (generate_ulid(), key_id, owner_id, actor_id, action, json.dumps(detail), created_at),
            )

    def list_for_key(self, key_id: str) -> List[ApiKeyEvent]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT id, key_id, owner_id, actor_id, action, detail, created_at FROM api_key_events "
                "WHERE key_id = ? ORDER BY created_at ASC, id ASC",
                (key_id,),
            )
            return [ApiKeyEvent.from_row(row) for row in cursor.fetchall()]


class UserApiAccessRepository:
    def get(self, user_id: str) -> UserApiAccess:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT user_id, keys_enabled, mcp_enabled FROM api_key_user_access WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            return UserApiAccess.from_row(row) if row else UserApiAccess(user_id=user_id)

    def save(self, access: UserApiAccess, updated_at: str, updated_by: Optional[str]) -> None:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO api_key_user_access (user_id, keys_enabled, mcp_enabled, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET keys_enabled = excluded.keys_enabled, "
                "mcp_enabled = excluded.mcp_enabled, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (access.user_id, int(access.keys_enabled), int(access.mcp_enabled), updated_at, updated_by),
            )
