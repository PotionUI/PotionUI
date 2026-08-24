from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Any
import json


class SettingType(Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"


class SettingValueType(Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    JSON = "json"


def _typed_value(value: str, value_type: "SettingValueType") -> Any:
    """Convert a stored string value to its proper type."""
    if value_type == SettingValueType.STRING:
        return value
    elif value_type == SettingValueType.INTEGER:
        return int(value)
    elif value_type == SettingValueType.FLOAT:
        return float(value)
    elif value_type == SettingValueType.BOOLEAN:
        return value.lower() in ('true', '1', 'yes', 'on')
    elif value_type == SettingValueType.JSON:
        return json.loads(value)
    else:
        return value


@dataclass
class Setting:
    """Database model for application settings"""
    id: str
    key: str
    value: str  # Always stored as string, converted based on value_type
    value_type: SettingValueType
    description: Optional[str]
    type: SettingType
    created_at: datetime
    updated_at: datetime

    def get_typed_value(self) -> Any:
        """Convert the string value to its proper type"""
        return _typed_value(self.value, self.value_type)

    @staticmethod
    def serialize_value(value: Any, value_type: SettingValueType) -> str:
        """Convert a typed value to string for storage"""
        if value_type == SettingValueType.JSON:
            return json.dumps(value)
        elif value_type == SettingValueType.BOOLEAN:
            return str(bool(value)).lower()
        else:
            return str(value)


@dataclass
class UserSetting:
    """Database model for user-specific setting assignments"""
    id: str
    user_id: str
    setting_id: str
    value: str  # User's override value
    created_at: datetime
    updated_at: datetime

    def get_typed_value(self, value_type: SettingValueType) -> Any:
        """Convert the string value to its proper type"""
        return _typed_value(self.value, value_type)