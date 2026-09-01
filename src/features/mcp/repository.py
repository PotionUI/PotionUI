from datetime import datetime, timezone
from typing import List, Optional

from src.features.mcp.records import McpToken


class McpTokenRepository:
    """Persistence for minted MCP tokens. Only the hash is ever stored — see
    src.features.mcp.manager for the plaintext-minting flow."""

    def create(self, id: str, user_id: str, name: str, token_hash: str, token_prefix: str) -> McpToken:
        now = datetime.now(timezone.utc).isoformat()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO mcp_tokens (id, user_id, name, token_hash, token_prefix, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (id, user_id, name, token_hash, token_prefix, now),
            )
        return McpToken(
            id=id, user_id=user_id, name=name, token_hash=token_hash,
            token_prefix=token_prefix, created_at=now,
        )

    def get_by_id(self, token_id: str) -> Optional[McpToken]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM mcp_tokens WHERE id = ?", (token_id,))
            row = cursor.fetchone()
            return McpToken.from_row(row) if row else None

    def get_by_hash(self, token_hash: str) -> Optional[McpToken]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM mcp_tokens WHERE token_hash = ?", (token_hash,))
            row = cursor.fetchone()
            return McpToken.from_row(row) if row else None

    def list_for_user(self, user_id: str) -> List[McpToken]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM mcp_tokens WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
            return [McpToken.from_row(row) for row in cursor.fetchall()]

    def revoke(self, token_id: str, user_id: str) -> bool:
        """Soft-revoke a token owned by `user_id`. No-op (returns False) for
        someone else's token or one already revoked."""
        now = datetime.now(timezone.utc).isoformat()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE mcp_tokens SET revoked_at = ?
                WHERE id = ? AND user_id = ? AND revoked_at IS NULL
                """,
                (now, token_id, user_id),
            )
            return cursor.rowcount > 0

    def touch_last_used(self, token_id: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE mcp_tokens SET last_used_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), token_id),
            )
