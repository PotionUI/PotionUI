"""
Keybinding Repository

Handles database operations for keybinding defaults and user overrides.
"""
from typing import List, Optional, Dict, Any
from src.features.keybindings.records import KeybindingDefault, UserKeybinding
import logging

logger = logging.getLogger(__name__)


class KeybindingRepository:
    """Repository for managing keybinding defaults and user overrides"""

    def get_all_defaults(self) -> List[KeybindingDefault]:
        """Get all default keybindings ordered by sort_order"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT id, key, modifiers, label, category, context, description, enabled, source, sort_order
                FROM keybinding_defaults
                ORDER BY sort_order ASC
            """)
            return [KeybindingDefault.from_row(row) for row in cursor.fetchall()]

    def get_user_overrides(self, user_id: str) -> List[UserKeybinding]:
        """Get all user keybinding overrides for a specific user"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT id, user_id, action_id, key, modifiers, enabled
                FROM user_keybindings
                WHERE user_id = ?
            """, (user_id,))
            return [UserKeybinding.from_row(row) for row in cursor.fetchall()]

    def get_effective_keybindings(self, user_id: str) -> List[Dict[str, Any]]:
        """Get merged keybindings (defaults + user overrides) for a user"""
        defaults = self.get_all_defaults()
        overrides = self.get_user_overrides(user_id)

        # Build override lookup by action_id
        override_map = {o.action_id: o for o in overrides}

        result = []
        for d in defaults:
            override = override_map.get(d.id)
            if override:
                result.append({
                    'action_id': d.id,
                    'key': override.key if override.key is not None else d.key,
                    'modifiers': override.modifiers if override.modifiers is not None else d.modifiers,
                    'label': d.label,
                    'category': d.category,
                    'context': d.context,
                    'description': d.description,
                    'enabled': override.enabled,
                    'is_custom': True,
                })
            else:
                result.append({
                    'action_id': d.id,
                    'key': d.key,
                    'modifiers': d.modifiers,
                    'label': d.label,
                    'category': d.category,
                    'context': d.context,
                    'description': d.description,
                    'enabled': d.enabled,
                    'is_custom': False,
                })

        return result

    def set_user_keybinding(self, user_id: str, action_id: str, key: Optional[str],
                            modifiers: str = '', enabled: bool = True) -> None:
        """Upsert a user keybinding override"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_keybindings (user_id, action_id, key, modifiers, enabled)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, action_id) DO UPDATE SET
                    key = excluded.key,
                    modifiers = excluded.modifiers,
                    enabled = excluded.enabled
            """, (user_id, action_id, key, modifiers, int(enabled)))

    def reset_user_keybinding(self, user_id: str, action_id: str) -> bool:
        """Reset a single user keybinding override back to default"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM user_keybindings
                WHERE user_id = ? AND action_id = ?
            """, (user_id, action_id))
            return cursor.rowcount > 0

    def reset_all_user_keybindings(self, user_id: str) -> int:
        """Reset all user keybinding overrides back to defaults"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM user_keybindings
                WHERE user_id = ?
            """, (user_id,))
            return cursor.rowcount

    def register_default(self, id: str, key: str, modifiers: str = '', label: str = '',
                         category: str = 'general', context: str = 'global',
                         description: Optional[str] = None, source: str = 'system',
                         sort_order: int = 0) -> None:
        """Register or replace a default keybinding (used by plugins)"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT OR REPLACE INTO keybinding_defaults
                    (id, key, modifiers, label, category, context, description, enabled, source, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (id, key, modifiers, label, category, context, description, source, sort_order))

    def unregister_defaults_by_source(self, source: str) -> int:
        """Remove all default keybindings from a specific source (e.g. plugin cleanup)"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM keybinding_defaults
                WHERE source = ?
            """, (source,))
            return cursor.rowcount


# Global repository instance
keybinding_repo = KeybindingRepository()
