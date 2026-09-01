import logging
import sqlite3
from typing import List, Optional, Tuple
from datetime import datetime
from src.platform.security.user import User, AccountType
from src.platform.util.ids import generate_ulid
from src.features.segments.repository import DEFAULT_SEGMENT_CATEGORIES
from src.features.user_groups.constants import ALL_ADMINS_GROUP_ID, ALL_USERS_GROUP_ID

logger = logging.getLogger(__name__)


class UserRepository:
    def get_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return User.from_row(row) if row else None
    
    def get_by_username(self, username: str) -> Optional[User]:
        """Get user by username"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            return User.from_row(row) if row else None
    
    def get_all(self) -> List[User]:
        """Get all users"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
            return [User.from_row(row) for row in cursor.fetchall()]
    
    def _insert_user(self, cursor, user_id: str, username: str, email: str,
                     password_hash: str, account_type: AccountType) -> None:
        """Insert a user row and seed its personal defaults, on the given cursor.

        Runs inside the caller's transaction so `create` and
        `create_claiming_instance` share one atomic unit each. Both
        self-registration and admin-created users pass through here, so a new
        account never observes a category-less Segment library and is always
        joined to the built-in groups (see `_join_builtin_groups`).
        """
        cursor.execute("""
            INSERT INTO users (id, username, email, password_hash, account_type)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, email, password_hash, account_type.value))

        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='segment_categories'"
        )
        if cursor.fetchone():
            for name, description, color in DEFAULT_SEGMENT_CATEGORIES:
                cursor.execute(
                    """INSERT OR IGNORE INTO segment_categories
                       (id, user_id, name, description, color)
                       VALUES (?, ?, ?, ?, ?)""",
                    (generate_ulid(), user_id, name, description, color),
                )

        self._join_builtin_groups(cursor, user_id, account_type)

    def _join_builtin_groups(self, cursor, user_id: str, account_type: AccountType) -> None:
        """Add `user_id` to the built-in ALL_USERS group (and ALL_ADMINS when
        `account_type` is ADMIN), on the given cursor - called from
        `_insert_user`, so every creation path (admin panel, self-registration,
        instance claim) joins the same groups inside the same transaction as
        the user row.

        A group can be missing (migration 095 not yet applied, or deleted out
        of band) - membership insert then fails its foreign key rather than
        being silently ignored by `INSERT OR IGNORE`, so each is wrapped and
        logged rather than allowed to abort user creation.
        """
        group_ids = [ALL_USERS_GROUP_ID]
        if account_type == AccountType.ADMIN:
            group_ids.append(ALL_ADMINS_GROUP_ID)

        for group_id in group_ids:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_group_members (id, group_id, user_id) VALUES (?, ?, ?)",
                    (generate_ulid(), group_id, user_id),
                )
            except sqlite3.IntegrityError:
                logger.warning(
                    f"Could not add user {user_id} to built-in group '{group_id}': group does not exist"
                )

    def create(self, username: str, email: str, password_hash: str,
               account_type: AccountType = AccountType.USER) -> User:
        """Create a new user"""
        user_id = generate_ulid()

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            self._insert_user(cursor, user_id, username, email, password_hash, account_type)

        return self.get_by_id(user_id)

    def create_claiming_instance(
        self, username: str, email: str, password_hash: str
    ) -> Tuple[User, bool]:
        """Create a user and atomically attempt to claim the instance as owner.

        The single-row `instance_claim` sentinel (PRIMARY KEY CHECK (id = 1)) is
        inserted BEFORE the user row, in one transaction, so whether this
        registration is the owner is decided by the DB constraint, not by a
        racy row count. A concurrent second registration's sentinel insert is
        rejected (IntegrityError) and that user is created as a regular USER.
        SQLite serialises the two write transactions, so exactly one caller ever
        sees `became_owner=True`.

        The owner (account_type ADMIN) is joined to both built-in groups and
        every other registration to ALL_USERS only, via `_insert_user` ->
        `_join_builtin_groups`, on this same transaction.
        """
        user_id = generate_ulid()

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            try:
                # id/username/claimed_at only; no FK, since the user row does
                # not exist yet. The insert either wins (first claim) or raises.
                cursor.execute(
                    "INSERT INTO instance_claim (id, owner_user_id, owner_username) "
                    "VALUES (1, ?, ?)",
                    (user_id, username),
                )
                became_owner = True
            except sqlite3.IntegrityError:
                # Sentinel already present: the instance is claimed. The failed
                # statement is rolled back but the transaction stays usable, so
                # we still create this account as a regular user below.
                became_owner = False

            account_type = AccountType.ADMIN if became_owner else AccountType.USER
            self._insert_user(cursor, user_id, username, email, password_hash, account_type)

        return self.get_by_id(user_id), became_owner
    
    def update(self, user_id: str, **kwargs) -> Optional[User]:
        """Update user fields"""
        allowed_fields = {'username', 'email', 'password_hash', 'account_type', 'last_login', 'avatar_filename'}
        update_fields = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not update_fields:
            return self.get_by_id(user_id)
        
        # Handle account_type enum
        if 'account_type' in update_fields:
            update_fields['account_type'] = update_fields['account_type'].value
        
        set_clause = ", ".join([f"{k} = ?" for k in update_fields])
        values = list(update_fields.values()) + [user_id]

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
            if cursor.rowcount == 0:
                return None
        
        return self.get_by_id(user_id)
    
    def update_last_login(self, user_id: str) -> Optional[User]:
        """Update user's last login timestamp"""
        return self.update(user_id, last_login=datetime.utcnow().isoformat())

    def update_password(self, user_id: str, password_hash: str) -> Optional[User]:
        """Update user's password hash (used by the auth change-password flow)"""
        return self.update(user_id, password_hash=password_hash)
    
    def delete(self, user_id: str) -> bool:
        """Delete user by ID"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0
    
    def exists_by_username(self, username: str) -> bool:
        """Check if username exists"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE username = ? LIMIT 1", (username,))
            return cursor.fetchone() is not None
    
    def exists_by_email(self, email: str) -> bool:
        """Check if email exists"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE email = ? LIMIT 1", (email,))
            return cursor.fetchone() is not None

# Global repository instance
user_repo = UserRepository()
