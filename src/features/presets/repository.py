from typing import Any, Dict, List, Optional
import json

from src.features.presets.records import Preset, UserPreset
from src.platform.util.ids import generate_ulid

class DatabasePresetRepository:
    # ===== Preset Management (Admin Operations) =====
    
    def get_installed_preset_by_id(self, preset_db_id: str) -> Optional[Preset]:
        """Get installed preset by database ID"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM presets WHERE id = ?", (preset_db_id,))
            row = cursor.fetchone()
            return Preset.from_row(row) if row else None
    
    def get_installed_preset_by_preset_id(self, preset_id: str) -> Optional[Preset]:
        """Get installed preset by YAML preset_id"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM presets WHERE preset_id = ?", (preset_id,))
            row = cursor.fetchone()
            return Preset.from_row(row) if row else None
    
    def get_all_installed_presets(self) -> List[Preset]:
        """Get all installed presets"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM presets ORDER BY installed_at DESC")
            return [Preset.from_row(row) for row in cursor.fetchall()]
    
    def is_preset_installed(self, preset_id: str) -> bool:
        """Check if preset is installed (exists in presets table)"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM presets WHERE preset_id = ? LIMIT 1", (preset_id,))
            return cursor.fetchone() is not None
    
    def install_preset(self, preset_id: str) -> Preset:
        """Install a preset (admin operation)"""
        preset_db_id = generate_ulid()
        
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO presets (id, preset_id)
                VALUES (?, ?)
            """, (preset_db_id, preset_id))
        
        return self.get_installed_preset_by_id(preset_db_id)
    
    def uninstall_preset(self, preset_id: str) -> bool:
        """Uninstall a preset (admin operation) - removes all user assignments too"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM presets WHERE preset_id = ?", (preset_id,))
            return cursor.rowcount > 0

    # ===== Configuration (admin-set) =====

    def get_preset_configuration(self, preset_id: str) -> Dict[str, Any]:
        """Stored admin-set configuration values for an installed preset (YAML preset_id).

        Empty dict if the preset isn't installed or has never had values set.
        """
        installed = self.get_installed_preset_by_preset_id(preset_id)
        if not installed:
            return {}
        return installed.configuration or {}

    def set_preset_configuration(self, preset_id: str, values: Dict[str, Any]) -> Optional[Preset]:
        """Replace the stored configuration values for an installed preset.

        Returns None if the preset isn't installed (caller should have already
        checked - see operations.set_preset_configuration).
        """
        installed = self.get_installed_preset_by_preset_id(preset_id)
        if not installed:
            return None

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE presets SET configuration = ? WHERE preset_id = ?",
                (json.dumps(values), preset_id),
            )

        return self.get_installed_preset_by_preset_id(preset_id)

    # ===== Form overrides (admin-set) =====

    def get_preset_form_overrides(self, preset_id: str) -> Dict[str, Any]:
        """Stored admin-set form overrides for an installed preset (YAML preset_id).

        Shape: `{mode: {field_name: {"default"?, "editable"?, "visible"?}}}`.
        Empty dict if the preset isn't installed or has never had overrides set.
        """
        installed = self.get_installed_preset_by_preset_id(preset_id)
        if not installed:
            return {}
        return installed.form_overrides or {}

    def set_preset_form_overrides(self, preset_id: str, overrides: Dict[str, Any]) -> Optional[Preset]:
        """Replace the stored form overrides for an installed preset (all modes).

        Returns None if the preset isn't installed (caller should have already
        checked - see operations.set_form_overrides).
        """
        installed = self.get_installed_preset_by_preset_id(preset_id)
        if not installed:
            return None

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE presets SET form_overrides = ? WHERE preset_id = ?",
                (json.dumps(overrides), preset_id),
            )

        return self.get_installed_preset_by_preset_id(preset_id)

    def find_presets_referencing_tag(self, tag_id: str) -> List[Dict[str, str]]:
        """Installed presets whose stored `configuration` values reference `tag_id`.

        Returns `[{"preset_id": <YAML preset_id>, "key": <configuration key>}, ...]`
        (one entry per referencing key, a preset could reference the same tag under
        more than one key). Used by tag deletion (`src.features.tags.operations.delete_tag`) to refuse
        removing a tag an admin has wired into a preset's `model_tags`
        configuration - see docs/presets.md "Configuration (admin-set)". A simple
        linear scan: the `presets` table holds one row per installed preset, never
        large enough to warrant indexing into the JSON blob.
        """
        matches: List[Dict[str, str]] = []
        for preset in self.get_all_installed_presets():
            for key, value in (preset.configuration or {}).items():
                if isinstance(value, list) and tag_id in value:
                    matches.append({"preset_id": preset.preset_id, "key": key})
        return matches

    # ===== User-Preset Assignment Operations =====
    
    def get_user_preset_by_id(self, user_preset_id: str) -> Optional[UserPreset]:
        """Get user preset assignment by ID"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_presets WHERE id = ?", (user_preset_id,))
            row = cursor.fetchone()
            return UserPreset.from_row(row) if row else None
    
    def get_preset_users(self, preset_db_id: str) -> List[UserPreset]:
        """Get all users assigned to a preset"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_presets WHERE preset_id = ? ORDER BY assigned_at DESC", (preset_db_id,))
            return [UserPreset.from_row(row) for row in cursor.fetchall()]
    
    def get_available_preset_ids_for_user(self, user_id: str) -> List[str]:
        """Get list of YAML preset_ids that are available to a user (direct + group assignments)"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT p.preset_id
                FROM presets p
                WHERE p.id IN (
                    SELECT up.preset_id FROM user_presets up WHERE up.user_id = ?
                    UNION
                    SELECT ugp.preset_id FROM user_group_presets ugp
                    JOIN user_group_members ugm ON ugp.group_id = ugm.group_id
                    WHERE ugm.user_id = ?
                )
            """, (user_id, user_id))
            return [row['preset_id'] for row in cursor.fetchall()]
    
    def is_preset_assigned_to_user(self, preset_id: str, user_id: str) -> bool:
        """Check if a preset (by YAML preset_id) is assigned to a user (direct + group)"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT 1
                FROM presets p
                WHERE p.preset_id = ? AND p.id IN (
                    SELECT up.preset_id FROM user_presets up WHERE up.user_id = ?
                    UNION
                    SELECT ugp.preset_id FROM user_group_presets ugp
                    JOIN user_group_members ugm ON ugp.group_id = ugm.group_id
                    WHERE ugm.user_id = ?
                )
                LIMIT 1
            """, (preset_id, user_id, user_id))
            return cursor.fetchone() is not None

    def is_preset_directly_assigned_to_user(self, preset_id: str, user_id: str) -> bool:
        """Check for a removable direct assignment, excluding inherited access."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT 1
                FROM user_presets up
                JOIN presets p ON p.id = up.preset_id
                WHERE p.preset_id = ? AND up.user_id = ?
                LIMIT 1
            """, (preset_id, user_id))
            return cursor.fetchone() is not None
    
    def assign_preset_to_user(self, preset_id: str, user_id: str) -> Optional[UserPreset]:
        """Assign an installed preset to a user"""
        # First check if preset is installed
        installed_preset = self.get_installed_preset_by_preset_id(preset_id)
        if not installed_preset:
            return None
        
        user_preset_id = generate_ulid()
        
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_presets (id, user_id, preset_id)
                VALUES (?, ?, ?)
            """, (user_preset_id, user_id, installed_preset.id))
        
        return self.get_user_preset_by_id(user_preset_id)
    
    def unassign_preset_from_user(self, preset_id: str, user_id: str) -> bool:
        """Unassign a preset from a user"""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM user_presets 
                WHERE user_id = ? AND preset_id IN (
                    SELECT id FROM presets WHERE preset_id = ?
                )
            """, (user_id, preset_id))
            return cursor.rowcount > 0
    
    def assign_preset_to_users(self, preset_id: str, user_ids: List[str]) -> List[UserPreset]:
        """Assign a preset to multiple users"""
        # First check if preset is installed
        installed_preset = self.get_installed_preset_by_preset_id(preset_id)
        if not installed_preset:
            return []
        
        assignments = []
        for user_id in user_ids:
            # Group access is inherited and cannot replace a direct assignment:
            # admins must still be able to create/remove the explicit user link.
            if not self.is_preset_directly_assigned_to_user(preset_id, user_id):
                assignment = self.assign_preset_to_user(preset_id, user_id)
                if assignment:
                    assignments.append(assignment)
        
        return assignments
    
    def get_preset_assignment_summary(self, preset_id: str) -> dict:
        """Get summary of preset assignments"""
        installed_preset = self.get_installed_preset_by_preset_id(preset_id)
        if not installed_preset:
            return {
                'installed': False,
                'total_assignments': 0,
                'assignments': []
            }
        
        assignments = self.get_preset_users(installed_preset.id)
        
        return {
            'installed': True,
            'preset_db_id': installed_preset.id,
            'total_assignments': len(assignments),
            'assignments': [assignment.to_dict() for assignment in assignments]
        }

# Global repository instance
preset_repo = DatabasePresetRepository()
