from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Any, List
import json


@dataclass
class Plugin:
    id: str
    name: str
    version: str
    type: str  # 'frontend-only', 'backend-only', 'full-stack'
    enabled: bool
    manifest_path: str
    description: Optional[str] = None
    author: Optional[str] = None
    installed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'Plugin':
        """Create Plugin instance from database row"""
        return cls(
            id=row['id'],
            name=row['name'],
            version=row['version'],
            type=row['type'],
            enabled=bool(row['enabled']),
            manifest_path=row['manifest_path'],
            description=row['description'],
            author=row['author'],
            installed_at=datetime.fromisoformat(row['installed_at']) if row['installed_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'name': self.name,
            'version': self.version,
            'type': self.type,
            'enabled': self.enabled,
            'manifest_path': self.manifest_path,
            'description': self.description,
            'author': self.author,
            'installed_at': self.installed_at.isoformat() if self.installed_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


@dataclass
class PluginSetting:
    id: int
    plugin_id: str
    setting_key: str
    setting_value: Optional[str] = None
    user_id: Optional[str] = None  # NULL for global settings
    is_secret: bool = False
    # True when a stored secret exists but cannot be decrypted with the current
    # key. The value itself is withheld (None) so it can never be used; the
    # flag lets a settings screen say "re-enter this" instead of showing an
    # unexplained empty field.
    value_unreadable: bool = False

    @classmethod
    def from_row(cls, row) -> 'PluginSetting':
        """Create PluginSetting instance from database row"""
        return cls(
            id=row['id'],
            plugin_id=row['plugin_id'],
            setting_key=row['setting_key'],
            setting_value=row['setting_value'],
            user_id=row['user_id'],
            is_secret=bool(row['is_secret'])
        )

    def get_value(self, expected_type: type = str) -> Any:
        """Parse the setting value to the expected type (handles JSON)"""
        if self.setting_value is None:
            return None

        # If expecting a string, return as-is
        if expected_type == str:
            return self.setting_value

        # For other types, try to parse as JSON first
        try:
            parsed = json.loads(self.setting_value)

            # If we got the expected type, return it
            if isinstance(parsed, expected_type):
                return parsed

            # Try to convert to expected type
            if expected_type == bool:
                if isinstance(parsed, str):
                    return parsed.lower() in ('true', '1', 'yes', 'on')
                return bool(parsed)
            elif expected_type == int:
                return int(parsed)
            elif expected_type == float:
                return float(parsed)
            elif expected_type in (list, dict):
                return parsed

            return parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            # If JSON parsing fails, try direct type conversion
            try:
                if expected_type == bool:
                    return self.setting_value.lower() in ('true', '1', 'yes', 'on')
                elif expected_type == int:
                    return int(self.setting_value)
                elif expected_type == float:
                    return float(self.setting_value)
                else:
                    return self.setting_value
            except (ValueError, AttributeError):
                return self.setting_value

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'plugin_id': self.plugin_id,
            'setting_key': self.setting_key,
            'setting_value': '***' if self.is_secret else self.setting_value,
            'user_id': self.user_id,
            'is_secret': self.is_secret
        }

    def serialize_value(self, value: Any) -> str:
        """Serialize value for database storage"""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value)


@dataclass
class PluginHook:
    id: int
    plugin_id: str
    hook_name: str
    hook_type: str  # 'backend' or 'frontend'
    handler_path: Optional[str] = None
    component_path: Optional[str] = None
    position: Optional[str] = None
    sort_order: int = 0

    @classmethod
    def from_row(cls, row) -> 'PluginHook':
        """Create PluginHook instance from database row"""
        return cls(
            id=row['id'],
            plugin_id=row['plugin_id'],
            hook_name=row['hook_name'],
            hook_type=row['hook_type'],
            handler_path=row['handler_path'],
            component_path=row['component_path'],
            position=row['position'],
            sort_order=int(row['sort_order']) if row['sort_order'] is not None else 0
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'plugin_id': self.plugin_id,
            'hook_name': self.hook_name,
            'hook_type': self.hook_type,
            'handler_path': self.handler_path,
            'component_path': self.component_path,
            'position': self.position,
            'sort_order': self.sort_order
        }


@dataclass
class PluginPage:
    id: int
    plugin_id: str
    route: str
    component_path: str
    label: str
    icon_svg: Optional[str] = None
    sidebar_order: int = 100
    show_in_sidebar: bool = True
    require_role: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'PluginPage':
        """Create PluginPage instance from database row"""
        try:
            require_role = row['require_role']
        except (KeyError, IndexError):
            require_role = None

        return cls(
            id=row['id'],
            plugin_id=row['plugin_id'],
            route=row['route'],
            component_path=row['component_path'],
            label=row['label'],
            icon_svg=row['icon_svg'],
            sidebar_order=int(row['sidebar_order']) if row['sidebar_order'] is not None else 100,
            show_in_sidebar=bool(row['show_in_sidebar']),
            require_role=require_role,
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'plugin_id': self.plugin_id,
            'route': self.route,
            'component_path': self.component_path,
            'label': self.label,
            'icon_svg': self.icon_svg,
            'sidebar_order': self.sidebar_order,
            'show_in_sidebar': self.show_in_sidebar,
            'require_role': self.require_role,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
