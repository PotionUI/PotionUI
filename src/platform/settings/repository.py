from typing import List, Optional, Dict, Any
from datetime import datetime

from src.platform.settings.records import Setting, UserSetting, SettingType, SettingValueType
from src.platform.util.ids import generate_ulid

_SETTING_COLUMNS = "id, key, value, value_type, description, type, created_at, updated_at"
_USER_SETTING_COLUMNS = "id, user_id, setting_id, value, created_at, updated_at"


def _setting_from_row(row) -> Setting:
    return Setting(
        id=row[0],
        key=row[1],
        value=row[2],
        value_type=SettingValueType(row[3]),
        description=row[4],
        type=SettingType(row[5]),
        created_at=datetime.fromisoformat(row[6]),
        updated_at=datetime.fromisoformat(row[7])
    )


def _user_setting_from_row(row) -> UserSetting:
    return UserSetting(
        id=row[0],
        user_id=row[1],
        setting_id=row[2],
        value=row[3],
        created_at=datetime.fromisoformat(row[4]),
        updated_at=datetime.fromisoformat(row[5])
    )


class SettingRepository:
    """Repository for managing settings in the database"""

    def get_setting_by_key(self, key: str) -> Optional[Setting]:
        """Get a setting by its key"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {_SETTING_COLUMNS}
                FROM settings
                WHERE key = ?
            """, (key,))

            row = cursor.fetchone()
            return _setting_from_row(row) if row else None

    def get_setting_by_id(self, setting_id: str) -> Optional[Setting]:
        """Get a setting by its ID"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {_SETTING_COLUMNS}
                FROM settings
                WHERE id = ?
            """, (setting_id,))

            row = cursor.fetchone()
            return _setting_from_row(row) if row else None

    def get_all_settings(self, setting_type: Optional[SettingType] = None) -> List[Setting]:
        """Get all settings, optionally filtered by type"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if setting_type:
                cursor.execute(f"""
                    SELECT {_SETTING_COLUMNS}
                    FROM settings
                    WHERE type = ?
                    ORDER BY key
                """, (setting_type.value,))
            else:
                cursor.execute(f"""
                    SELECT {_SETTING_COLUMNS}
                    FROM settings
                    ORDER BY key
                """)

            return [_setting_from_row(row) for row in cursor.fetchall()]

    def create_setting(
        self,
        key: str,
        value: str,
        value_type: SettingValueType,
        description: Optional[str] = None,
        setting_type: SettingType = SettingType.SYSTEM
    ) -> Setting:
        """Create a new setting"""
        setting_id = generate_ulid()
        now = datetime.utcnow()

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (setting_id, key, value, value_type.value, description, setting_type.value, now, now))
        
        return Setting(
            id=setting_id,
            key=key,
            value=value,
            value_type=value_type,
            description=description,
            type=setting_type,
            created_at=now,
            updated_at=now
        )

    def update_setting_value(self, setting_id: str, value: str) -> bool:
        """Update a setting's value"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE settings
                SET value = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (value, setting_id))
            return cursor.rowcount > 0

    def update_setting_value_by_key(self, key: str, value: str) -> bool:
        """Update a setting's value addressed by key"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE settings
                SET value = ?, updated_at = CURRENT_TIMESTAMP
                WHERE key = ?
            """, (value, key))
            return cursor.rowcount > 0

    def update_setting(
        self,
        setting_id: str,
        value: Optional[str] = None,
        description: Optional[str] = None
    ) -> bool:
        """Update a setting's value and/or description"""
        updates = []
        params = []
        
        if value is not None:
            updates.append("value = ?")
            params.append(value)
        
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        
        if not updates:
            return False
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(setting_id)

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"""
                UPDATE settings
                SET {', '.join(updates)}
                WHERE id = ?
            """, params)
            return cursor.rowcount > 0

    def apply_bulk_updates(
        self,
        system_updates: List[tuple],
        user_updates: List[tuple],
    ) -> None:
        """Apply many setting writes as one all-or-nothing transaction.

        `system_updates` is a list of ``(setting_id, str_value)``; `user_updates`
        is a list of ``(user_id, setting_id, str_value)``. Every write shares a
        single cursor, so `db.get_cursor()` commits them together or rolls the
        whole batch back if any statement raises - a bulk settings update can
        never leave earlier keys persisted while a later one fails.
        """
        now = datetime.utcnow()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            for setting_id, value in system_updates:
                cursor.execute(
                    "UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (value, setting_id),
                )
            for user_id, setting_id, value in user_updates:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO user_settings (
                        id, user_id, setting_id, value, created_at, updated_at
                    )
                    VALUES (
                        COALESCE((SELECT id FROM user_settings WHERE user_id = ? AND setting_id = ?), ?),
                        ?, ?, ?,
                        COALESCE((SELECT created_at FROM user_settings WHERE user_id = ? AND setting_id = ?), ?),
                        ?
                    )
                    """,
                    (
                        user_id, setting_id, generate_ulid(),
                        user_id, setting_id, value,
                        user_id, setting_id, now,
                        now,
                    ),
                )

    # User Settings methods
    def get_user_setting(self, user_id: str, setting_id: str) -> Optional[UserSetting]:
        """Get a user's override for a specific setting"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {_USER_SETTING_COLUMNS}
                FROM user_settings
                WHERE user_id = ? AND setting_id = ?
            """, (user_id, setting_id))

            row = cursor.fetchone()
            return _user_setting_from_row(row) if row else None

    def get_user_settings(self, user_id: str) -> List[UserSetting]:
        """Get all user setting overrides for a user"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {_USER_SETTING_COLUMNS}
                FROM user_settings
                WHERE user_id = ?
                ORDER BY created_at
            """, (user_id,))

            return [_user_setting_from_row(row) for row in cursor.fetchall()]

    def update_user_setting(self, user_id: str, setting_id: str, value: str) -> bool:
        """Update or create a user setting override"""
        self.apply_bulk_updates(system_updates=[], user_updates=[(user_id, setting_id, value)])
        return True

    def delete_user_setting(self, user_id: str, setting_id: str) -> bool:
        """Delete a user setting override"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM user_settings
                WHERE user_id = ? AND setting_id = ?
            """, (user_id, setting_id))
            return cursor.rowcount > 0

    def get_effective_settings(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get effective settings for a user (system defaults + user overrides)"""
        # Get all system settings
        system_settings = self.get_all_settings()
        result = {}
        
        for setting in system_settings:
            result[setting.key] = setting.get_typed_value()
        
        # Apply user overrides if user_id provided
        if user_id:
            user_settings = self.get_user_settings(user_id)
            user_overrides = {}
            
            # Build lookup of setting_id to setting for type info
            setting_lookup = {s.id: s for s in system_settings}
            
            for user_setting in user_settings:
                if user_setting.setting_id in setting_lookup:
                    setting = setting_lookup[user_setting.setting_id]
                    user_overrides[setting.key] = user_setting.get_typed_value(setting.value_type)
            
            result.update(user_overrides)
        
        return result

    def get_user_setting_by_key(self, user_id: str, key: str) -> Optional[Any]:
        """Get effective value for a setting key for a specific user"""
        # First get the base setting
        setting = self.get_setting_by_key(key)
        if not setting:
            return None
        
        # Check for user override
        user_setting = self.get_user_setting(user_id, setting.id)
        if user_setting:
            return user_setting.get_typed_value(setting.value_type)
        
        return setting.get_typed_value()

    def set_user_setting_by_key(self, user_id: str, key: str, value: Any) -> bool:
        """Set a user setting by key, handling type conversion"""
        # Get the base setting to determine type
        setting = self.get_setting_by_key(key)
        if not setting:
            return False
        
        # Convert value to string for storage
        str_value = Setting.serialize_value(value, setting.value_type)
        
        return self.update_user_setting(user_id, setting.id, str_value)