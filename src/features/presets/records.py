from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import json

from src.platform.database.rows import dt_column, dt_iso

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
    source_preset_id: Optional[str] = None
    display_name: Optional[str] = None
    display_description: Optional[str] = None
    display_cover: Optional[str] = None
    offered_modes: Optional[List[str]] = None
    first_mode: Optional[str] = None

    @property
    def is_mirror(self) -> bool:
        return self.source_preset_id is not None

    @property
    def has_display(self) -> bool:
        return any(value is not None for value in (self.display_name, self.display_description, self.display_cover))

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
            installed_at=dt_column(row['installed_at']),
            updated_at=dt_column(row['updated_at']),
            configuration=json.loads(raw_configuration) if raw_configuration else {},
            form_overrides=json.loads(raw_form_overrides) if raw_form_overrides else {},
            source_preset_id=_column(row, 'source_preset_id'),
            display_name=_column(row, 'display_name'),
            display_description=_column(row, 'display_description'),
            display_cover=_column(row, 'display_cover'),
            offered_modes=_json_list(_column(row, 'offered_modes')),
            first_mode=_column(row, 'first_mode'),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'preset_id': self.preset_id,
            'installed_at': dt_iso(self.installed_at),
            'updated_at': dt_iso(self.updated_at),
            'configuration': self.configuration,
            'form_overrides': self.form_overrides,
            'source_preset_id': self.source_preset_id,
            'display_name': self.display_name,
            'display_description': self.display_description,
            'display_cover': self.display_cover,
            'offered_modes': self.offered_modes,
            'first_mode': self.first_mode,
        }


@dataclass(frozen=True)
class PresetTombstone:
    preset_id: str
    name: str
    source_preset_id: Optional[str]
    deleted_at: str

    @classmethod
    def from_row(cls, row) -> 'PresetTombstone':
        return cls(
            preset_id=row['preset_id'],
            name=row['name'],
            source_preset_id=row['source_preset_id'],
            deleted_at=row['deleted_at'],
        )

    def to_dict(self) -> dict:
        return {'preset_id': self.preset_id, 'name': self.name}


def _column(row, name: str) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


def _json_list(raw: Any) -> Optional[List[str]]:
    if not raw:
        return None
    value = json.loads(raw)
    return [str(item) for item in value] if isinstance(value, list) else None

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
            assigned_at=dt_column(row['assigned_at']),
            updated_at=dt_column(row['updated_at'])
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'preset_id': self.preset_id,
            'assigned_at': dt_iso(self.assigned_at),
            'updated_at': dt_iso(self.updated_at)
        }