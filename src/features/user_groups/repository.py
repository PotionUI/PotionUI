from typing import List, Optional
from src.platform.database import db
from src.features.user_groups.records import UserGroup, UserGroupMember, UserGroupPreset, UserGroupLLM, UserGroupModel
from src.platform.util.ids import generate_ulid


class UserGroupRepository:
    def _resolve_preset_db_id(self, preset_id: str) -> Optional[str]:
        """Resolve either a public preset ID or an installed-preset database ID.

        Preset APIs expose the ID declared in ``preset.yml`` while the group
        relationship table correctly references ``presets.id``.  Keeping the
        translation here gives every caller the same safe contract and avoids
        leaking the storage ID into assignment commands.
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(
                    (SELECT id FROM presets WHERE preset_id = ?),
                    (SELECT id FROM presets WHERE id = ?)
                ) AS id
                """,
                (preset_id, preset_id)
            )
            row = cursor.fetchone()
            return row['id'] if row and row['id'] else None

    # ===== Group CRUD =====

    def create_group(self, name: str, description: Optional[str] = None) -> UserGroup:
        """Create a new user group"""
        group_id = generate_ulid()
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_groups (id, name, description) VALUES (?, ?, ?)",
                (group_id, name, description)
            )
        return self.get_group_by_id(group_id)

    def get_group_by_id(self, group_id: str) -> Optional[UserGroup]:
        """Get a group by its ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_groups WHERE id = ?", (group_id,))
            row = cursor.fetchone()
            return UserGroup.from_row(row) if row else None

    def get_group_by_name(self, name: str) -> Optional[UserGroup]:
        """Get a group by its name"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_groups WHERE name = ?", (name,))
            row = cursor.fetchone()
            return UserGroup.from_row(row) if row else None

    def get_all_groups(self) -> List[UserGroup]:
        """Get all groups"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_groups ORDER BY name")
            return [UserGroup.from_row(row) for row in cursor.fetchall()]

    def update_group(self, group_id: str, name: Optional[str] = None, description: Optional[str] = None) -> Optional[UserGroup]:
        """Update a group's name and/or description"""
        group = self.get_group_by_id(group_id)
        if not group:
            return None

        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if not updates:
            return group

        params.append(group_id)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"UPDATE user_groups SET {', '.join(updates)} WHERE id = ?",
                tuple(params)
            )
        return self.get_group_by_id(group_id)

    def delete_group(self, group_id: str) -> bool:
        """Delete a group (cascades to memberships and assignments)"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM user_groups WHERE id = ?", (group_id,))
            return cursor.rowcount > 0

    # ===== Member Management =====

    def add_user_to_group(self, group_id: str, user_id: str) -> Optional[UserGroupMember]:
        """Add a user to a group"""
        member_id = generate_ulid()
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO user_group_members (id, group_id, user_id) VALUES (?, ?, ?)",
                    (member_id, group_id, user_id)
                )
            return self.get_member(member_id)
        except Exception:
            return None

    def get_member(self, member_id: str) -> Optional[UserGroupMember]:
        """Get a member record by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_group_members WHERE id = ?", (member_id,))
            row = cursor.fetchone()
            return UserGroupMember.from_row(row) if row else None

    def remove_user_from_group(self, group_id: str, user_id: str) -> bool:
        """Remove a user from a group"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM user_group_members WHERE group_id = ? AND user_id = ?",
                (group_id, user_id)
            )
            return cursor.rowcount > 0

    def get_group_members(self, group_id: str) -> List[UserGroupMember]:
        """Get all members of a group"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_group_members WHERE group_id = ? ORDER BY assigned_at DESC",
                (group_id,)
            )
            return [UserGroupMember.from_row(row) for row in cursor.fetchall()]

    def get_user_groups(self, user_id: str) -> List[UserGroup]:
        """Get all groups a user belongs to"""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT ug.* FROM user_groups ug
                JOIN user_group_members ugm ON ug.id = ugm.group_id
                WHERE ugm.user_id = ?
                ORDER BY ug.name
            """, (user_id,))
            return [UserGroup.from_row(row) for row in cursor.fetchall()]

    def is_user_in_group(self, group_id: str, user_id: str) -> bool:
        """Check if a user is in a group"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM user_group_members WHERE group_id = ? AND user_id = ? LIMIT 1",
                (group_id, user_id)
            )
            return cursor.fetchone() is not None

    # ===== Preset Assignment =====

    def assign_preset_to_group(self, group_id: str, preset_id: str) -> Optional[UserGroupPreset]:
        """Assign an installed preset to a group by public or database ID."""
        preset_db_id = self._resolve_preset_db_id(preset_id)
        if not preset_db_id:
            return None

        assignment_id = generate_ulid()
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO user_group_presets (id, group_id, preset_id) VALUES (?, ?, ?)",
                    (assignment_id, group_id, preset_db_id)
                )
            return self.get_group_preset(assignment_id)
        except Exception:
            return None

    def get_group_preset(self, assignment_id: str) -> Optional[UserGroupPreset]:
        """Get a group preset assignment by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_group_presets WHERE id = ?", (assignment_id,))
            row = cursor.fetchone()
            return UserGroupPreset.from_row(row) if row else None

    def unassign_preset_from_group(self, group_id: str, preset_id: str) -> bool:
        """Unassign a preset from a group by public or database ID."""
        preset_db_id = self._resolve_preset_db_id(preset_id)
        if not preset_db_id:
            return False

        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM user_group_presets WHERE group_id = ? AND preset_id = ?",
                (group_id, preset_db_id)
            )
            return cursor.rowcount > 0

    def get_group_presets(self, group_id: str) -> List[UserGroupPreset]:
        """Get all presets assigned to a group"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_group_presets WHERE group_id = ? ORDER BY assigned_at DESC",
                (group_id,)
            )
            return [UserGroupPreset.from_row(row) for row in cursor.fetchall()]

    # ===== LLM Assignment =====

    def assign_llm_to_group(self, group_id: str, llm_config_id: str) -> Optional[UserGroupLLM]:
        """Assign an LLM configuration to a group"""
        assignment_id = generate_ulid()
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO user_group_llms (id, group_id, llm_config_id) VALUES (?, ?, ?)",
                    (assignment_id, group_id, llm_config_id)
                )
            return self.get_group_llm(assignment_id)
        except Exception:
            return None

    def get_group_llm(self, assignment_id: str) -> Optional[UserGroupLLM]:
        """Get a group LLM assignment by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_group_llms WHERE id = ?", (assignment_id,))
            row = cursor.fetchone()
            return UserGroupLLM.from_row(row) if row else None

    def unassign_llm_from_group(self, group_id: str, llm_config_id: str) -> bool:
        """Unassign an LLM configuration from a group"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM user_group_llms WHERE group_id = ? AND llm_config_id = ?",
                (group_id, llm_config_id)
            )
            return cursor.rowcount > 0

    def get_group_llms(self, group_id: str) -> List[UserGroupLLM]:
        """Get all LLM configurations assigned to a group"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_group_llms WHERE group_id = ? ORDER BY assigned_at DESC",
                (group_id,)
            )
            return [UserGroupLLM.from_row(row) for row in cursor.fetchall()]

    # ===== Access Queries =====

    def get_group_member_count(self, group_id: str) -> int:
        """Get the number of members in a group"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM user_group_members WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row['count'] if row else 0

    def get_group_preset_count(self, group_id: str) -> int:
        """Get the number of presets assigned to a group"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM user_group_presets WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row['count'] if row else 0

    def get_groups_for_preset(self, preset_id: str) -> List[UserGroupPreset]:
        """Get group assignments for a preset by public or database ID."""
        preset_db_id = self._resolve_preset_db_id(preset_id)
        if not preset_db_id:
            return []

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_group_presets WHERE preset_id = ? ORDER BY assigned_at DESC",
                (preset_db_id,)
            )
            return [UserGroupPreset.from_row(row) for row in cursor.fetchall()]

    def get_group_count_for_preset(self, preset_id: str) -> int:
        """Count group assignments for a preset by public or database ID."""
        preset_db_id = self._resolve_preset_db_id(preset_id)
        if not preset_db_id:
            return 0

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) as count FROM user_group_presets WHERE preset_id = ?",
                (preset_db_id,)
            )
            row = cursor.fetchone()
            return row['count'] if row else 0

    def get_group_llm_count(self, group_id: str) -> int:
        """Get the number of LLMs assigned to a group"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM user_group_llms WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row['count'] if row else 0

    # ===== Model Assignment =====

    def assign_model_to_group(self, group_id: str, model_id: str) -> Optional[UserGroupModel]:
        """Assign a model to a group"""
        assignment_id = generate_ulid()
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO user_group_models (id, group_id, model_id) VALUES (?, ?, ?)",
                    (assignment_id, group_id, model_id)
                )
            return self.get_group_model(assignment_id)
        except Exception:
            return None

    def get_group_model(self, assignment_id: str) -> Optional[UserGroupModel]:
        """Get a group model assignment by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_group_models WHERE id = ?", (assignment_id,))
            row = cursor.fetchone()
            return UserGroupModel.from_row(row) if row else None

    def unassign_model_from_group(self, group_id: str, model_id: str) -> bool:
        """Unassign a model from a group"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM user_group_models WHERE group_id = ? AND model_id = ?",
                (group_id, model_id)
            )
            return cursor.rowcount > 0

    def get_group_models(self, group_id: str) -> List[UserGroupModel]:
        """Get all models assigned to a group"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_group_models WHERE group_id = ? ORDER BY assigned_at DESC",
                (group_id,)
            )
            return [UserGroupModel.from_row(row) for row in cursor.fetchall()]

    def get_group_model_count(self, group_id: str) -> int:
        """Get the number of models assigned to a group"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) as count FROM user_group_models WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            return row['count'] if row else 0


# Global repository instance
user_group_repo = UserGroupRepository()
