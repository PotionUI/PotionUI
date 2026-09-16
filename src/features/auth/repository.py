from typing import List, Optional

from src.features.auth.records import ExternalIdentity
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid


class ExternalIdentityRepository:
    def get(self, issuer: str, subject: str) -> Optional[ExternalIdentity]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM external_identities WHERE issuer = ? AND subject = ?",
                (issuer, subject),
            )
            row = cursor.fetchone()
            return ExternalIdentity.from_row(row) if row else None

    def get_by_id(self, identity_id: str) -> Optional[ExternalIdentity]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM external_identities WHERE id = ?", (identity_id,)
            )
            row = cursor.fetchone()
            return ExternalIdentity.from_row(row) if row else None

    def list_for_user(self, user_id: str) -> List[ExternalIdentity]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM external_identities WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            )
            return [ExternalIdentity.from_row(row) for row in cursor.fetchall()]

    def create(self, issuer: str, subject: str, user_id: str) -> ExternalIdentity:
        from src.platform.database.database import db
        identity_id = generate_ulid()
        stamp = now_iso()
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO external_identities "
                "(id, issuer, subject, user_id, created_at, last_login_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (identity_id, issuer, subject, user_id, stamp, stamp),
            )
        return self.get_by_id(identity_id)

    def touch_last_login(self, identity_id: str) -> Optional[ExternalIdentity]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE external_identities SET last_login_at = ? WHERE id = ?",
                (now_iso(), identity_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_by_id(identity_id)

    def delete(self, identity_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM external_identities WHERE id = ?", (identity_id,)
            )
            return cursor.rowcount > 0
