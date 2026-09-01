"""
Phrasebook repository for managing categories and values in the database.
Returns Pydantic DTOs directly, encapsulating all DB concerns.
"""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from src.features.phrasebook.dto import (
    PhrasebookCategory,
    PhrasebookValue,
    PhrasebookStateFilter
)

logger = logging.getLogger(__name__)


class PhrasebookCategoryRepository:
    """Repository for phrasebook categories."""

    def _row_to_category(self, row) -> PhrasebookCategory:
        """Convert database row to Pydantic model."""
        return PhrasebookCategory(
            id=row['id'],
            name=row['name'],
            path=row['path'],
            user_id=row['user_id'],
            parent_id=row['parent_id'],
            description=row['description'] or "",
            is_active=bool(row['is_active']) if 'is_active' in row.keys() else True,
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.now(),
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else datetime.now()
        )

    def _build_state_filter_clause(self, state_filter: PhrasebookStateFilter) -> str:
        """Build SQL WHERE clause for state filtering."""
        if state_filter == PhrasebookStateFilter.ACTIVE:
            return " AND is_active = 1"
        elif state_filter == PhrasebookStateFilter.INACTIVE:
            return " AND is_active = 0"
        return ""

    def get_all(
        self,
        user_id: str,
        state_filter: PhrasebookStateFilter = PhrasebookStateFilter.ALL
    ) -> List[PhrasebookCategory]:
        """Get all phrasebook categories for a user with optional state filtering."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            state_clause = self._build_state_filter_clause(state_filter)
            cursor.execute(
                f"SELECT * FROM phrasebook_categories WHERE user_id = ?{state_clause} ORDER BY path",
                (user_id,)
            )
            return [self._row_to_category(row) for row in cursor.fetchall()]

    def get_by_id(self, category_id: str, user_id: Optional[str] = None) -> Optional[PhrasebookCategory]:
        """Get phrasebook category by ID."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute(
                    "SELECT * FROM phrasebook_categories WHERE id = ? AND user_id = ?",
                    (category_id, user_id)
                )
            else:
                cursor.execute(
                    "SELECT * FROM phrasebook_categories WHERE id = ?",
                    (category_id,)
                )
            row = cursor.fetchone()
            return self._row_to_category(row) if row else None

    def get_by_path(self, path: str, user_id: str) -> Optional[PhrasebookCategory]:
        """Get phrasebook category by path."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM phrasebook_categories WHERE path = ? AND user_id = ?",
                (path, user_id)
            )
            row = cursor.fetchone()
            return self._row_to_category(row) if row else None

    def get_children(self, parent_id: Optional[str], user_id: str) -> List[PhrasebookCategory]:
        """Get child categories of a parent."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if parent_id:
                cursor.execute(
                    "SELECT * FROM phrasebook_categories WHERE parent_id = ? AND user_id = ? ORDER BY name",
                    (parent_id, user_id)
                )
            else:
                cursor.execute(
                    "SELECT * FROM phrasebook_categories WHERE parent_id IS NULL AND user_id = ? ORDER BY name",
                    (user_id,)
                )
            return [self._row_to_category(row) for row in cursor.fetchall()]

    def search_by_path_prefix(self, path_prefix: str, user_id: str) -> List[PhrasebookCategory]:
        """Search categories by path prefix (for phrasebook)."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM phrasebook_categories WHERE path LIKE ? AND user_id = ? ORDER BY path",
                (f"{path_prefix}%", user_id)
            )
            return [self._row_to_category(row) for row in cursor.fetchall()]

    def create(self, category: PhrasebookCategory) -> bool:
        """Create new phrasebook category."""
        from src.platform.database.database import db
        try:
            now = datetime.now()
            with db.get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO phrasebook_categories
                    (id, name, path, parent_id, user_id, description, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    category.id, category.name, category.path, category.parent_id,
                    category.user_id or "system", category.description,
                    now.isoformat(), now.isoformat()
                ))
            return True
        except Exception as e:
            logger.error(f"Error creating phrasebook category: {e}")
            return False

    def update(self, category_id: str, category: PhrasebookCategory) -> bool:
        """Update existing phrasebook category."""
        from src.platform.database.database import db
        try:
            now = datetime.now()
            with db.get_cursor() as cursor:
                cursor.execute("""
                    UPDATE phrasebook_categories
                    SET name = ?, path = ?, parent_id = ?, description = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                """, (
                    category.name, category.path, category.parent_id, category.description,
                    now.isoformat(), category_id, category.user_id or "system"
                ))
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating phrasebook category: {e}")
            return False

    def delete(self, category_id: str, user_id: Optional[str] = None) -> bool:
        """Delete phrasebook category (cascades to values)."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute(
                    "DELETE FROM phrasebook_categories WHERE id = ? AND user_id = ?",
                    (category_id, user_id)
                )
            else:
                cursor.execute(
                    "DELETE FROM phrasebook_categories WHERE id = ?",
                    (category_id,)
                )
            return cursor.rowcount > 0

    def update_active_state(self, category_id: str, user_id: str, is_active: bool) -> bool:
        """Update the active state of a category."""
        from src.platform.database.database import db
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE phrasebook_categories
                    SET is_active = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (1 if is_active else 0, datetime.now().isoformat(), category_id, user_id)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating category active state: {e}")
            return False

    def exists(self, category_id: str) -> bool:
        """Check if category exists."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM phrasebook_categories WHERE id = ?", (category_id,))
            return cursor.fetchone() is not None


class PhrasebookValueRepository:
    """Repository for phrasebook values."""

    def _row_to_value(self, row) -> PhrasebookValue:
        """Convert database row to Pydantic model."""
        return PhrasebookValue(
            id=row['id'],
            category_id=row['category_id'],
            label=row['label'],
            value=row['value'],
            user_id=row['user_id'],
            sort_order=row['sort_order'] or 0,
            is_active=bool(row['is_active']) if 'is_active' in row.keys() else True,
            preview_file_id=row['preview_file_id'] if 'preview_file_id' in row.keys() else None,
            preview_generation_id=row['preview_generation_id'] if 'preview_generation_id' in row.keys() else None,
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.now(),
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else datetime.now()
        )

    def _build_state_filter_clause(self, state_filter: PhrasebookStateFilter) -> str:
        """Build SQL WHERE clause for state filtering."""
        if state_filter == PhrasebookStateFilter.ACTIVE:
            return " AND is_active = 1"
        elif state_filter == PhrasebookStateFilter.INACTIVE:
            return " AND is_active = 0"
        return ""

    def get_all(
        self,
        user_id: str,
        state_filter: PhrasebookStateFilter = PhrasebookStateFilter.ALL
    ) -> List[PhrasebookValue]:
        """Get all phrasebook values for a user with optional state filtering."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            state_clause = self._build_state_filter_clause(state_filter)
            cursor.execute(
                f"SELECT * FROM phrasebook_values WHERE user_id = ?{state_clause} ORDER BY sort_order, label",
                (user_id,)
            )
            return [self._row_to_value(row) for row in cursor.fetchall()]

    def get_by_id(self, value_id: str, user_id: Optional[str] = None) -> Optional[PhrasebookValue]:
        """Get phrasebook value by ID."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute(
                    "SELECT * FROM phrasebook_values WHERE id = ? AND user_id = ?",
                    (value_id, user_id)
                )
            else:
                cursor.execute(
                    "SELECT * FROM phrasebook_values WHERE id = ?",
                    (value_id,)
                )
            row = cursor.fetchone()
            return self._row_to_value(row) if row else None

    def get_by_category(
        self,
        category_id: str,
        user_id: Optional[str] = None,
        state_filter: PhrasebookStateFilter = PhrasebookStateFilter.ALL
    ) -> List[PhrasebookValue]:
        """Get all values for a category with optional state filtering."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            state_clause = self._build_state_filter_clause(state_filter)
            if user_id:
                cursor.execute(
                    f"SELECT * FROM phrasebook_values WHERE category_id = ? AND user_id = ?{state_clause} ORDER BY sort_order, label",
                    (category_id, user_id)
                )
            else:
                cursor.execute(
                    f"SELECT * FROM phrasebook_values WHERE category_id = ?{state_clause} ORDER BY sort_order, label",
                    (category_id,)
                )
            return [self._row_to_value(row) for row in cursor.fetchall()]

    def get_by_path(self, path: str, user_id: str) -> List[PhrasebookValue]:
        """Get values for a specific path."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            # First get the category by path
            cursor.execute(
                "SELECT id FROM phrasebook_categories WHERE path = ? AND user_id = ?",
                (path, user_id)
            )
            category_row = cursor.fetchone()

            if not category_row:
                return []

            # Then get values for that category
            cursor.execute(
                "SELECT * FROM phrasebook_values WHERE category_id = ? AND user_id = ? ORDER BY sort_order, label",
                (category_row['id'], user_id)
            )
            return [self._row_to_value(row) for row in cursor.fetchall()]

    def search_by_path_prefix(
        self,
        path_prefix: str,
        user_id: str,
        limit: int = 50,
        state_filter: PhrasebookStateFilter = PhrasebookStateFilter.ACTIVE
    ) -> List[Dict[str, Any]]:
        """Search values by category path prefix with category info and state filtering.

        Default is ACTIVE state to only return active values in active categories.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            # Build state clauses for both category and value
            value_state = ""
            category_state = ""
            if state_filter == PhrasebookStateFilter.ACTIVE:
                value_state = " AND v.is_active = 1"
                category_state = " AND c.is_active = 1"
            elif state_filter == PhrasebookStateFilter.INACTIVE:
                value_state = " AND v.is_active = 0"
                # Still include inactive values from active categories

            cursor.execute(f"""
                SELECT v.*, c.path as category_path, c.name as category_name, c.is_active as category_is_active
                FROM phrasebook_values v
                JOIN phrasebook_categories c ON v.category_id = c.id
                WHERE c.path LIKE ? AND v.user_id = ?{value_state}{category_state}
                ORDER BY c.path, v.sort_order, v.label
                LIMIT ?
            """, (f"{path_prefix}%", user_id, limit))

            results = []
            for row in cursor.fetchall():
                value = self._row_to_value(row)
                value_dict = value.model_dump()
                value_dict['category_path'] = row['category_path']
                value_dict['category_name'] = row['category_name']
                value_dict['category_is_active'] = bool(row['category_is_active'])
                results.append(value_dict)

            return results

    def create(self, value: PhrasebookValue) -> bool:
        """Create new phrasebook value."""
        from src.platform.database.database import db
        try:
            now = datetime.now()
            with db.get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO phrasebook_values
                    (id, category_id, label, value, sort_order, user_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    value.id, value.category_id, value.label, value.value,
                    value.sort_order, value.user_id or "system",
                    now.isoformat(), now.isoformat()
                ))
            return True
        except Exception as e:
            logger.error(f"Error creating phrasebook value: {e}")
            return False

    def create_bulk(self, values: List[PhrasebookValue]) -> int:
        """Create multiple phrasebook values at once."""
        if not values:
            return 0

        from src.platform.database.database import db
        try:
            now = datetime.now()
            with db.get_cursor() as cursor:
                data = [
                    (v.id, v.category_id, v.label, v.value, v.sort_order,
                     v.user_id or "system", now.isoformat(), now.isoformat())
                    for v in values
                ]
                cursor.executemany("""
                    INSERT INTO phrasebook_values
                    (id, category_id, label, value, sort_order, user_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, data)
                return cursor.rowcount
        except Exception as e:
            logger.error(f"Error creating bulk phrasebook values: {e}")
            return 0

    def update(self, value_id: str, value: PhrasebookValue) -> bool:
        """Update existing phrasebook value."""
        from src.platform.database.database import db
        try:
            now = datetime.now()
            with db.get_cursor() as cursor:
                cursor.execute("""
                    UPDATE phrasebook_values
                    SET category_id = ?, label = ?, value = ?, sort_order = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                """, (
                    value.category_id, value.label, value.value, value.sort_order,
                    now.isoformat(), value_id, value.user_id or "system"
                ))
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating phrasebook value: {e}")
            return False

    def delete(self, value_id: str, user_id: Optional[str] = None) -> bool:
        """Delete phrasebook value."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute(
                    "DELETE FROM phrasebook_values WHERE id = ? AND user_id = ?",
                    (value_id, user_id)
                )
            else:
                cursor.execute(
                    "DELETE FROM phrasebook_values WHERE id = ?",
                    (value_id,)
                )
            return cursor.rowcount > 0

    def update_active_state(self, value_id: str, user_id: str, is_active: bool) -> bool:
        """Update the active state of a value."""
        from src.platform.database.database import db
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE phrasebook_values
                    SET is_active = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (1 if is_active else 0, datetime.now().isoformat(), value_id, user_id)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating value active state: {e}")
            return False

    def update_preview_file(
        self,
        value_id: str,
        user_id: str,
        file_id: Optional[str],
        generation_id: Optional[str]
    ) -> bool:
        """Update the preview file ID and generation ID for a value."""
        from src.platform.database.database import db
        try:
            with db.get_cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE phrasebook_values
                    SET preview_file_id = ?, preview_generation_id = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (file_id, generation_id, datetime.now().isoformat(), value_id, user_id)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating value preview file: {e}")
            return False


# Global repository instances
phrasebook_category_repo = PhrasebookCategoryRepository()
phrasebook_value_repo = PhrasebookValueRepository()
