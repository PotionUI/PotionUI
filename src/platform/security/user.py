"""The identity every request carries.

`User` is what authentication resolves a token into and what authorization
decides against, so it belongs to the security layer rather than to any one
feature: `get_current_active_user` returns it, `AccountType` gates admin
routes, and features and plugins alike receive it as their caller.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from enum import Enum

class AccountType(Enum):
    USER = "USER"
    ADMIN = "ADMIN"

@dataclass
class User:
    username: str
    email: str
    password_hash: str
    account_type: AccountType = AccountType.USER
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    avatar_filename: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> 'User':
        """Create User instance from database row"""
        row_keys = row.keys()
        return cls(
            id=row['id'],
            username=row['username'],
            email=row['email'],
            password_hash=row['password_hash'],
            account_type=AccountType(row['account_type']),
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
            last_login=datetime.fromisoformat(row['last_login']) if row['last_login'] else None,
            avatar_filename=row['avatar_filename'] if 'avatar_filename' in row_keys else None
        )

    def to_dict(self, exclude_password=True) -> dict:
        """Convert to dictionary for API responses"""
        data = {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'account_type': self.account_type.value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'avatar_url': f'/api/users/avatars/{self.avatar_filename}' if self.avatar_filename else None
        }
        if not exclude_password:
            data['password_hash'] = self.password_hash
        return data