from dataclasses import dataclass
from typing import Optional


@dataclass
class McpToken:
    id: str
    user_id: str
    name: str
    token_hash: str
    token_prefix: str
    created_at: str
    last_used_at: Optional[str] = None
    revoked_at: Optional[str] = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @classmethod
    def from_row(cls, row) -> "McpToken":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            token_hash=row["token_hash"],
            token_prefix=row["token_prefix"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            revoked_at=row["revoked_at"],
        )
