from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
import json

@dataclass
class Preset:
    preset_id: str  # The ID from the YAML file
    id: Optional[str] = None
    installed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Admin-set values for this preset's declared `configuration:` schema
    # (preset.yml), e.g. {"checkpoint_tags": ["tag_id_1"]}. See
    # src/features/presets/configuration.py and docs/presets.md "Configuration (admin-set)".
    configuration: Dict[str, Any] = field(default_factory=dict)
    # Admin-set per-field form overrides, keyed by mode then field name, e.g.
    # {"txt2img": {"steps": {"default": 30, "editable": false}}}. Applied to
    # every form variant of the mode. See src/features/presets/form_overrides.py
    # and docs/presets.md "Form overrides (admin-set)".
    form_overrides: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row) -> 'Preset':
        """Create Preset instance from database row"""
        try:
            raw_configuration = row['configuration']
        except (KeyError, IndexError):
            raw_configuration = None
        try:
            raw_form_overrides = row['form_overrides']
        except (KeyError, IndexError):
            raw_form_overrides = None
        return cls(
            id=row['id'],
            preset_id=row['preset_id'],
            installed_at=datetime.fromisoformat(row['installed_at']) if row['installed_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
            configuration=json.loads(raw_configuration) if raw_configuration else {},
            form_overrides=json.loads(raw_form_overrides) if raw_form_overrides else {},
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'preset_id': self.preset_id,
            'installed_at': self.installed_at.isoformat() if self.installed_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'configuration': self.configuration,
            'form_overrides': self.form_overrides,
        }

@dataclass
class UserPreset:
    user_id: str
    preset_id: str  # References the database preset ID, not the YAML preset_id
    id: Optional[str] = None
    assigned_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @classmethod
    def from_row(cls, row) -> 'UserPreset':
        """Create UserPreset instance from database row"""
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            preset_id=row['preset_id'],
            assigned_at=datetime.fromisoformat(row['assigned_at']) if row['assigned_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'preset_id': self.preset_id,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }