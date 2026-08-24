import logging
from typing import Any, Dict, List, Optional
from src.platform.database import db
from src.features.plugins.records import Plugin, PluginSetting, PluginHook, PluginPage
from src.platform.security.redaction import SECRET_MASK
from src.platform.security.secrets import get_secret_cipher, SecretDecryptionError
from src.platform.util.ids import generate_ulid


class PluginRepository:
    """Repository for managing plugin data persistence"""

    @staticmethod
    def _setting_context(plugin_id: str, setting_key: str) -> str:
        return f"plugin_settings:{plugin_id}/{setting_key}"

    @classmethod
    def _decrypt_setting(cls, setting: PluginSetting) -> PluginSetting:
        try:
            setting.setting_value = get_secret_cipher().decrypt_if_encrypted(
                setting.setting_value,
                context=cls._setting_context(setting.plugin_id, setting.setting_key),
            )
        except SecretDecryptionError as exc:
            # An unreadable credential must not take the settings screen (or a
            # plugin's own reads) down with it - that screen is where the
            # operator re-enters it. The value is withheld, never passed
            # through encrypted, so a consumer sees "not configured" rather
            # than garbage.
            logging.getLogger(__name__).warning("%s", exc)
            setting.setting_value = None
            setting.value_unreadable = True
        return setting

    # ========== Plugin CRUD ==========

    def get_all_plugins(self) -> List[Plugin]:
        """Get all plugins"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM plugins ORDER BY name")
            return [Plugin.from_row(row) for row in cursor.fetchall()]

    def get_plugin_by_id(self, plugin_id: str) -> Optional[Plugin]:
        """Get plugin by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM plugins WHERE id = ?", (plugin_id,))
            row = cursor.fetchone()
            return Plugin.from_row(row) if row else None

    def get_enabled_plugins(self) -> List[Plugin]:
        """Get all enabled plugins"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM plugins WHERE enabled = 1 ORDER BY name")
            return [Plugin.from_row(row) for row in cursor.fetchall()]

    def get_plugins_by_type(self, plugin_type: str) -> List[Plugin]:
        """Get plugins by type (frontend-only, backend-only, full-stack)"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM plugins WHERE type = ? ORDER BY name", (plugin_type,))
            return [Plugin.from_row(row) for row in cursor.fetchall()]

    def create_plugin(self, plugin: Plugin) -> Plugin:
        """Create a new plugin record"""
        if not plugin.id:
            plugin.id = generate_ulid()

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO plugins (
                    id, name, version, type, enabled, manifest_path,
                    description, author, installed_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                plugin.id,
                plugin.name,
                plugin.version,
                plugin.type,
                plugin.enabled,
                plugin.manifest_path,
                plugin.description,
                plugin.author,
                plugin.installed_at.isoformat() if plugin.installed_at else None,
                plugin.updated_at.isoformat() if plugin.updated_at else None
            ))

        return self.get_plugin_by_id(plugin.id)

    def update_plugin(self, plugin_id: str, plugin: Plugin) -> Optional[Plugin]:
        """Update an existing plugin"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE plugins
                SET name = ?, version = ?, type = ?, enabled = ?, manifest_path = ?,
                    description = ?, author = ?, updated_at = ?
                WHERE id = ?
            """, (
                plugin.name,
                plugin.version,
                plugin.type,
                plugin.enabled,
                plugin.manifest_path,
                plugin.description,
                plugin.author,
                plugin.updated_at.isoformat() if plugin.updated_at else None,
                plugin_id
            ))

            if cursor.rowcount == 0:
                return None

        return self.get_plugin_by_id(plugin_id)

    def delete_plugin(self, plugin_id: str) -> bool:
        """Delete plugin by ID (cascades to settings and hooks)"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM plugins WHERE id = ?", (plugin_id,))
            return cursor.rowcount > 0

    def enable_plugin(self, plugin_id: str) -> bool:
        """Enable a plugin"""
        with db.get_cursor() as cursor:
            cursor.execute("UPDATE plugins SET enabled = 1 WHERE id = ?", (plugin_id,))
            return cursor.rowcount > 0

    def disable_plugin(self, plugin_id: str) -> bool:
        """Disable a plugin"""
        with db.get_cursor() as cursor:
            cursor.execute("UPDATE plugins SET enabled = 0 WHERE id = ?", (plugin_id,))
            return cursor.rowcount > 0

    # ========== Plugin Settings ==========

    def get_plugin_settings(self, plugin_id: str, user_id: Optional[str] = None) -> List[PluginSetting]:
        """Get settings for a plugin (user-specific if user_id provided, otherwise global)"""
        with db.get_cursor() as cursor:
            if user_id is not None:
                cursor.execute("""
                    SELECT * FROM plugin_settings
                    WHERE plugin_id = ? AND user_id = ?
                    ORDER BY setting_key
                """, (plugin_id, user_id))
            else:
                cursor.execute("""
                    SELECT * FROM plugin_settings
                    WHERE plugin_id = ? AND user_id IS NULL
                    ORDER BY setting_key
                """, (plugin_id,))
            return [
                self._decrypt_setting(PluginSetting.from_row(row))
                for row in cursor.fetchall()
            ]

    def get_plugin_setting(self, plugin_id: str, setting_key: str, user_id: Optional[str] = None) -> Optional[PluginSetting]:
        """Get a specific setting"""
        with db.get_cursor() as cursor:
            if user_id is not None:
                cursor.execute("""
                    SELECT * FROM plugin_settings
                    WHERE plugin_id = ? AND setting_key = ? AND user_id = ?
                """, (plugin_id, setting_key, user_id))
            else:
                cursor.execute("""
                    SELECT * FROM plugin_settings
                    WHERE plugin_id = ? AND setting_key = ? AND user_id IS NULL
                """, (plugin_id, setting_key))
            row = cursor.fetchone()
            if not row:
                return None
            return self._decrypt_setting(PluginSetting.from_row(row))

    def set_plugin_setting(self, plugin_id: str, setting_key: str, setting_value: str,
                          user_id: Optional[str] = None, is_secret: bool = False) -> PluginSetting:
        """Set a plugin setting (upsert).

        A secret is encrypted before it reaches the database. Writing the mask a
        read hands out is treated as "unchanged" rather than as a new value, so
        saving a settings form without touching the credential preserves it.

        The mask check deliberately does NOT consult `is_secret`. That flag is
        recomputed from the manifest on every save, so a plugin that is
        uninstalled, mid-reload, or briefly missing from the registry arrives
        here with is_secret=False - which is exactly when the caller has lost
        the knowledge that this row holds a credential, and exactly when the
        guard has to hold. Nobody's real credential is three asterisks, so
        refusing the mask for every setting costs nothing. Returning early also
        leaves the stored `is_secret` intact, instead of demoting the row.
        """
        if setting_value == SECRET_MASK:
            existing = self.get_plugin_setting(plugin_id, setting_key, user_id)
            if existing is not None:
                return existing

        stored_value = setting_value
        if is_secret and setting_value:
            stored_value = get_secret_cipher().encrypt(setting_value)

        with db.get_cursor() as cursor:
            # Check if setting exists
            if user_id is not None:
                cursor.execute("""
                    SELECT * FROM plugin_settings
                    WHERE plugin_id = ? AND setting_key = ? AND user_id = ?
                """, (plugin_id, setting_key, user_id))
            else:
                cursor.execute("""
                    SELECT * FROM plugin_settings
                    WHERE plugin_id = ? AND setting_key = ? AND user_id IS NULL
                """, (plugin_id, setting_key))

            existing = cursor.fetchone()

            if existing:
                # Update existing setting
                cursor.execute("""
                    UPDATE plugin_settings
                    SET setting_value = ?, is_secret = ?
                    WHERE plugin_id = ? AND setting_key = ? AND (
                        (user_id IS NULL AND ? IS NULL) OR user_id = ?
                    )
                """, (stored_value, is_secret, plugin_id, setting_key, user_id, user_id))
            else:
                # Insert new setting
                cursor.execute("""
                    INSERT INTO plugin_settings (plugin_id, setting_key, setting_value, user_id, is_secret)
                    VALUES (?, ?, ?, ?, ?)
                """, (plugin_id, setting_key, stored_value, user_id, is_secret))

        # Return the setting after transaction is complete
        return self.get_plugin_setting(plugin_id, setting_key, user_id)

    def delete_plugin_setting(self, plugin_id: str, setting_key: str, user_id: Optional[str] = None) -> bool:
        """Delete a specific setting"""
        with db.get_cursor() as cursor:
            if user_id is not None:
                cursor.execute("""
                    DELETE FROM plugin_settings
                    WHERE plugin_id = ? AND setting_key = ? AND user_id = ?
                """, (plugin_id, setting_key, user_id))
            else:
                cursor.execute("""
                    DELETE FROM plugin_settings
                    WHERE plugin_id = ? AND setting_key = ? AND user_id IS NULL
                """, (plugin_id, setting_key))
            return cursor.rowcount > 0

    # ========== Setting Audit ==========

    def record_setting_change(
        self,
        plugin_id: str,
        setting_key: str,
        action: str,
        actor_user_id: Optional[str] = None,
        actor_username: Optional[str] = None,
        scope_user_id: Optional[str] = None,
        is_secret: bool = False,
    ) -> None:
        """Append an audit entry for a settings change.

        Records who touched which key and when. The value is deliberately not a
        parameter - an audit table that holds credentials is a second copy of
        the thing being protected.
        """
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO plugin_setting_audit (
                    id, plugin_id, setting_key, scope_user_id,
                    actor_user_id, actor_username, action, is_secret
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                generate_ulid(),
                plugin_id,
                setting_key,
                scope_user_id,
                actor_user_id,
                actor_username,
                action,
                1 if is_secret else 0,
            ))

    def get_setting_audit(
        self,
        plugin_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Read the settings audit trail, newest first."""
        with db.get_cursor() as cursor:
            if plugin_id:
                cursor.execute("""
                    SELECT * FROM plugin_setting_audit
                    WHERE plugin_id = ?
                    ORDER BY changed_at DESC, id DESC
                    LIMIT ?
                """, (plugin_id, limit))
            else:
                cursor.execute("""
                    SELECT * FROM plugin_setting_audit
                    ORDER BY changed_at DESC, id DESC
                    LIMIT ?
                """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    # ========== Encryption Maintenance ==========

    def iter_encrypted_settings(self) -> List[Dict[str, Any]]:
        """Every stored setting whose value is an encryption envelope.

        Raw rows, undecrypted - the preflight check and the rotation script both
        need the ciphertext itself, not a decrypted view that would raise.
        """
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT id, plugin_id, setting_key, setting_value, user_id
                FROM plugin_settings
                WHERE is_secret = 1 AND setting_value LIKE 'enc:%'
            """)
            return [dict(row) for row in cursor.fetchall()]

    def replace_encrypted_value(self, setting_id: int, stored_value: str) -> None:
        """Overwrite one row's stored ciphertext, addressed by primary key."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE plugin_settings SET setting_value = ? WHERE id = ?",
                (stored_value, setting_id),
            )

    # ========== Plugin Hooks ==========

    def get_plugin_hooks(self, plugin_id: str) -> List[PluginHook]:
        """Get all hooks for a plugin"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM plugin_hooks
                WHERE plugin_id = ?
                ORDER BY hook_name, sort_order
            """, (plugin_id,))
            return [PluginHook.from_row(row) for row in cursor.fetchall()]

    def get_hooks_by_name(self, hook_name: str) -> List[PluginHook]:
        """Get all hooks with a specific name (sorted by sort_order)"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT h.* FROM plugin_hooks h
                JOIN plugins p ON h.plugin_id = p.id
                WHERE h.hook_name = ? AND p.enabled = 1
                ORDER BY h.sort_order, h.id
            """, (hook_name,))
            return [PluginHook.from_row(row) for row in cursor.fetchall()]

    def get_hooks_by_type(self, hook_type: str) -> List[PluginHook]:
        """Get all hooks by type (backend or frontend)"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT h.* FROM plugin_hooks h
                JOIN plugins p ON h.plugin_id = p.id
                WHERE h.hook_type = ? AND p.enabled = 1
                ORDER BY h.hook_name, h.sort_order, h.id
            """, (hook_type,))
            return [PluginHook.from_row(row) for row in cursor.fetchall()]

    def register_hook(self, hook: PluginHook) -> PluginHook:
        """Register a new hook"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO plugin_hooks (
                    plugin_id, hook_name, hook_type, handler_path,
                    component_path, position, sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                hook.plugin_id,
                hook.hook_name,
                hook.hook_type,
                hook.handler_path,
                hook.component_path,
                hook.position,
                hook.sort_order
            ))
            hook.id = cursor.lastrowid
        return hook

    def update_hook(self, hook_id: int, hook: PluginHook) -> Optional[PluginHook]:
        """Update an existing hook"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE plugin_hooks
                SET plugin_id = ?, hook_name = ?, hook_type = ?, handler_path = ?,
                    component_path = ?, position = ?, sort_order = ?
                WHERE id = ?
            """, (
                hook.plugin_id,
                hook.hook_name,
                hook.hook_type,
                hook.handler_path,
                hook.component_path,
                hook.position,
                hook.sort_order,
                hook_id
            ))

            if cursor.rowcount == 0:
                return None

            # Fetch and return the updated hook
            cursor.execute("SELECT * FROM plugin_hooks WHERE id = ?", (hook_id,))
            row = cursor.fetchone()
            return PluginHook.from_row(row) if row else None

    def unregister_hook(self, hook_id: int) -> bool:
        """Unregister a hook"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM plugin_hooks WHERE id = ?", (hook_id,))
            return cursor.rowcount > 0

    def clear_plugin_hooks(self, plugin_id: str) -> int:
        """Clear all hooks for a plugin, return count deleted"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM plugin_hooks WHERE plugin_id = ?", (plugin_id,))
            return cursor.rowcount

    # ========== Plugin Pages ==========

    def get_plugin_pages(self, plugin_id: str) -> List[PluginPage]:
        """Get all pages for a plugin"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM plugin_pages
                WHERE plugin_id = ?
                ORDER BY sidebar_order, label
            """, (plugin_id,))
            return [PluginPage.from_row(row) for row in cursor.fetchall()]

    def get_all_active_pages(self) -> List[PluginPage]:
        """Get pages from enabled plugins only"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT pp.* FROM plugin_pages pp
                JOIN plugins p ON pp.plugin_id = p.id
                WHERE p.enabled = 1
                ORDER BY pp.sidebar_order, pp.label
            """)
            return [PluginPage.from_row(row) for row in cursor.fetchall()]

    def create_plugin_page(self, page: PluginPage) -> PluginPage:
        """Create a new plugin page record"""
        with db.get_cursor() as cursor:
            # Check if require_role column exists
            cursor.execute("PRAGMA table_info(plugin_pages)")
            columns = {row['name'] for row in cursor.fetchall()}
            has_require_role = 'require_role' in columns

            if has_require_role:
                cursor.execute("""
                    INSERT INTO plugin_pages (
                        plugin_id, route, component_path, label,
                        icon_svg, sidebar_order, show_in_sidebar, require_role
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    page.plugin_id, page.route, page.component_path, page.label,
                    page.icon_svg, page.sidebar_order, page.show_in_sidebar,
                    getattr(page, 'require_role', None)
                ))
            else:
                cursor.execute("""
                    INSERT INTO plugin_pages (
                        plugin_id, route, component_path, label,
                        icon_svg, sidebar_order, show_in_sidebar
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    page.plugin_id, page.route, page.component_path, page.label,
                    page.icon_svg, page.sidebar_order, page.show_in_sidebar
                ))
            page.id = cursor.lastrowid
        return page

    def delete_plugin_pages(self, plugin_id: str) -> int:
        """Delete all pages for a plugin, return count deleted"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM plugin_pages WHERE plugin_id = ?", (plugin_id,))
            return cursor.rowcount


# Global repository instance
plugin_repo = PluginRepository()
