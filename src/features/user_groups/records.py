from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class UserGroup:
    name: str
    description: Optional[str] = None
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # True for the built-in groups seeded by migration 095 (ALL_USERS/ALL_ADMINS,
    # see src.features.user_groups.constants). operations.groups.delete_group
    # refuses to delete a group with is_system=True.
    is_system: bool = False

    @classmethod
    def from_row(cls, row) -> 'UserGroup':
        """Create UserGroup instance from database row"""
        return cls(
            id=row['id'],
            name=row['name'],
            description=row['description'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
            is_system=bool(row['is_system']) if 'is_system' in row.keys() else False,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'is_system': self.is_system,
        }

@dataclass
class UserGroupMember:
    group_id: str
    user_id: str
    id: Optional[str] = None
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'UserGroupMember':
        """Create UserGroupMember instance from database row"""
        return cls(
            id=row['id'],
            group_id=row['group_id'],
            user_id=row['user_id'],
            assigned_at=datetime.fromisoformat(row['assigned_at']) if row['assigned_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'group_id': self.group_id,
            'user_id': self.user_id,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

@dataclass
class UserGroupPreset:
    group_id: str
    preset_id: str
    id: Optional[str] = None
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'UserGroupPreset':
        """Create UserGroupPreset instance from database row"""
        return cls(
            id=row['id'],
            group_id=row['group_id'],
            preset_id=row['preset_id'],
            assigned_at=datetime.fromisoformat(row['assigned_at']) if row['assigned_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'group_id': self.group_id,
            'preset_id': self.preset_id,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

@dataclass
class UserGroupLLM:
    group_id: str
    llm_config_id: str
    id: Optional[str] = None
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'UserGroupLLM':
        """Create UserGroupLLM instance from database row"""
        return cls(
            id=row['id'],
            group_id=row['group_id'],
            llm_config_id=row['llm_config_id'],
            assigned_at=datetime.fromisoformat(row['assigned_at']) if row['assigned_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'group_id': self.group_id,
            'llm_config_id': self.llm_config_id,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

@dataclass
class UserGroupModel:
    group_id: str
    model_id: str
    id: Optional[str] = None
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'UserGroupModel':
        """Create UserGroupModel instance from database row"""
        return cls(
            id=row['id'],
            group_id=row['group_id'],
            model_id=row['model_id'],
            assigned_at=datetime.fromisoformat(row['assigned_at']) if row['assigned_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'group_id': self.group_id,
            'model_id': self.model_id,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
