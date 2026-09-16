from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.platform.database.rows import dt_column, dt_iso


@dataclass
class ExternalIdentity:
    id: str
    issuer: str
    subject: str
    user_id: str
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "ExternalIdentity":
        return cls(
            id=row["id"],
            issuer=row["issuer"],
            subject=row["subject"],
            user_id=row["user_id"],
            created_at=dt_column(row["created_at"]),
            last_login_at=dt_column(row["last_login_at"]),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "issuer": self.issuer,
            "subject": self.subject,
            "user_id": self.user_id,
            "created_at": dt_iso(self.created_at),
            "last_login_at": dt_iso(self.last_login_at),
        }
