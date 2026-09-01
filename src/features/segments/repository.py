"""Persistence for saved Segments, Segment Templates, and their categories.

All public reads and writes are user-scoped.  A Segment Template is an
aggregate whose ordered child collection is replaced in the same SQLite
transaction as its parent update.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List, Optional

from src.features.segments.dto import (
    RichSegment,
    SavedSegment,
    SegmentCategory,
    SegmentTemplate,
)
from src.platform.util.ids import generate_ulid

from src.platform.database.rows import json_column


DEFAULT_SEGMENT_CATEGORIES = (
    (
        "Quality & Technical",
        "Quality enhancing and technical prompt fragments",
        "#10B981",
    ),
    ("Art Style", "Artistic styles and aesthetic directions", "#8B5CF6"),
    ("Environment", "Lighting, atmosphere, and environments", "#F59E0B"),
    ("Composition", "Camera, framing, and composition", "#EF4444"),
)


def _datetime(value: Any, default: Optional[datetime] = None) -> Optional[datetime]:
    if value is None:
        return default
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _json_dumps(value: Any) -> str:
    """Serialize Pydantic models nested inside chip dictionaries as JSON."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, dict):
        value = {
            key: item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for key, item in value.items()
        }
    return json.dumps(value)


class SegmentCategoryRepository:
    """User-scoped category persistence with lazy default seeding."""

    def ensure_defaults(self, user_id: str) -> None:
        """Seed defaults once for users created after the reset migration.

        We intentionally do not recreate individual defaults after a user has
        renamed or deleted them.  Any existing category means the user's
        category workspace has already been initialized.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM segment_categories WHERE user_id = ? LIMIT 1",
                (user_id,),
            )
            if cursor.fetchone():
                return
            for name, description, color in DEFAULT_SEGMENT_CATEGORIES:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO segment_categories
                        (id, user_id, name, description, color)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (generate_ulid(), user_id, name, description, color),
                )

    def _row_to_category(self, row) -> SegmentCategory:
        return SegmentCategory(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            color=row["color"] or "#3B82F6",
            user_id=row["user_id"],
            created_at=_datetime(row["created_at"], datetime.now()),
            updated_at=_datetime(row["updated_at"]),
        )

    def get_all(self, user_id: str, *, ensure_defaults: bool = True) -> List[SegmentCategory]:
        if ensure_defaults:
            self.ensure_defaults(user_id)
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM segment_categories WHERE user_id = ? ORDER BY name COLLATE NOCASE",
                (user_id,),
            )
            return [self._row_to_category(row) for row in cursor.fetchall()]

    def get_by_id(self, category_id: str, user_id: str) -> Optional[SegmentCategory]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM segment_categories WHERE id = ? AND user_id = ?",
                (category_id, user_id),
            )
            row = cursor.fetchone()
            return self._row_to_category(row) if row else None

    def get_by_name(self, name: str, user_id: str) -> Optional[SegmentCategory]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM segment_categories
                WHERE user_id = ? AND name = ? COLLATE NOCASE
                """,
                (user_id, name),
            )
            row = cursor.fetchone()
            return self._row_to_category(row) if row else None

    def create(self, category: SegmentCategory) -> SegmentCategory:
        if not category.user_id:
            raise ValueError("user_id is required")
        user_id = category.user_id
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO segment_categories
                    (id, user_id, name, description, color, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    category.id,
                    user_id,
                    category.name,
                    category.description,
                    category.color,
                ),
            )
        return self.get_by_id(category.id, user_id)

    def update(
        self, category_id: str, category: SegmentCategory, user_id: str
    ) -> Optional[SegmentCategory]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE segment_categories
                SET name = ?, description = ?, color = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (
                    category.name,
                    category.description,
                    category.color,
                    category_id,
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_by_id(category_id, user_id)

    def delete(self, category_id: str, user_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM segment_categories WHERE id = ? AND user_id = ?",
                (category_id, user_id),
            )
            return cursor.rowcount > 0

    def has_saved_segments(self, category_id: str, user_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM saved_segments
                WHERE category_id = ? AND user_id = ? LIMIT 1
                """,
                (category_id, user_id),
            )
            return cursor.fetchone() is not None


class SavedSegmentRepository:
    """Persistence for reusable single rich Segments."""

    def _row_to_segment(self, row) -> SavedSegment:
        override_color = row["color"]
        category_color = row["category_color"]
        return SavedSegment(
            id=row["id"],
            user_id=row["user_id"],
            category_id=row["category_id"],
            name=row["name"],
            type=row["type"],
            content=row["content"] or "",
            chips=json_column(row["chips"], {}),
            enabled=bool(row["is_enabled"]),
            color=override_color,
            effective_color=override_color or category_color,
            description=row["description"],
            tags=json_column(row["tags"], []),
            created_at=_datetime(row["created_at"], datetime.now()),
            updated_at=_datetime(row["updated_at"], datetime.now()),
        )

    @staticmethod
    def _select_sql(where: str) -> str:
        return f"""
            SELECT s.*, c.color AS category_color
            FROM saved_segments s
            JOIN segment_categories c
              ON c.id = s.category_id AND c.user_id = s.user_id
            WHERE {where}
        """

    def get_all(
        self, user_id: str, category_id: Optional[str] = None
    ) -> List[SavedSegment]:
        where = "s.user_id = ?"
        params: List[Any] = [user_id]
        if category_id:
            where += " AND s.category_id = ?"
            params.append(category_id)
        query = self._select_sql(where) + " ORDER BY s.name COLLATE NOCASE"
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [self._row_to_segment(row) for row in cursor.fetchall()]

    def get_by_id(self, segment_id: str, user_id: str) -> Optional[SavedSegment]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                self._select_sql("s.id = ? AND s.user_id = ?"),
                (segment_id, user_id),
            )
            row = cursor.fetchone()
            return self._row_to_segment(row) if row else None

    def get_by_name(self, name: str, user_id: str) -> Optional[SavedSegment]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                self._select_sql("s.user_id = ? AND s.name = ? COLLATE NOCASE"),
                (user_id, name),
            )
            row = cursor.fetchone()
            return self._row_to_segment(row) if row else None

    def create(self, segment: SavedSegment) -> Optional[SavedSegment]:
        """Create only when the category belongs to the same user."""
        if not segment.user_id:
            raise ValueError("user_id is required")
        user_id = segment.user_id
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO saved_segments (
                    id, user_id, category_id, name, type, content, chips,
                    is_enabled, color, description, tags, created_at, updated_at
                )
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                WHERE EXISTS (
                    SELECT 1 FROM segment_categories WHERE id = ? AND user_id = ?
                )
                """,
                (
                    segment.id,
                    user_id,
                    segment.category_id,
                    segment.name,
                    segment.type,
                    segment.content,
                    _json_dumps(segment.chips),
                    1 if segment.enabled else 0,
                    segment.color,
                    segment.description,
                    _json_dumps(segment.tags),
                    segment.category_id,
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_by_id(segment.id, user_id)

    def update(
        self, segment_id: str, segment: SavedSegment, user_id: str
    ) -> Optional[SavedSegment]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE saved_segments
                SET category_id = ?, name = ?, type = ?, content = ?, chips = ?,
                    is_enabled = ?, color = ?, description = ?, tags = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                  AND EXISTS (
                    SELECT 1 FROM segment_categories WHERE id = ? AND user_id = ?
                  )
                """,
                (
                    segment.category_id,
                    segment.name,
                    segment.type,
                    segment.content,
                    _json_dumps(segment.chips),
                    1 if segment.enabled else 0,
                    segment.color,
                    segment.description,
                    _json_dumps(segment.tags),
                    segment_id,
                    user_id,
                    segment.category_id,
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_by_id(segment_id, user_id)

    def delete(self, segment_id: str, user_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM saved_segments WHERE id = ? AND user_id = ?",
                (segment_id, user_id),
            )
            return cursor.rowcount > 0


class SegmentTemplateRepository:
    """Persistence for ordered, multi-segment Template aggregates."""

    @staticmethod
    def _row_to_child(row) -> RichSegment:
        return RichSegment(
            id=row["id"],
            type=row["type"],
            content=row["content"] or "",
            chips=json_column(row["chips"], {}),
            enabled=bool(row["is_enabled"]),
            name=row["name"],
            color=row["color"],
            description=row["description"],
        )

    def _children(self, cursor, template_id: str) -> List[RichSegment]:
        cursor.execute(
            """
            SELECT * FROM segment_template_segments
            WHERE template_id = ? ORDER BY position ASC, id ASC
            """,
            (template_id,),
        )
        return [self._row_to_child(row) for row in cursor.fetchall()]

    def _row_to_template(self, cursor, row) -> SegmentTemplate:
        return SegmentTemplate(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            description=row["description"] or "",
            tags=json_column(row["tags"], []),
            segments=self._children(cursor, row["id"]),
            created_at=_datetime(row["created_at"], datetime.now()),
            updated_at=_datetime(row["updated_at"], datetime.now()),
        )

    def get_all(self, user_id: str) -> List[SegmentTemplate]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM segment_templates
                WHERE user_id = ? ORDER BY name COLLATE NOCASE
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
            return [self._row_to_template(cursor, row) for row in rows]

    def get_by_id(self, template_id: str, user_id: str) -> Optional[SegmentTemplate]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM segment_templates WHERE id = ? AND user_id = ?",
                (template_id, user_id),
            )
            row = cursor.fetchone()
            return self._row_to_template(cursor, row) if row else None

    def get_by_name(self, name: str, user_id: str) -> Optional[SegmentTemplate]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM segment_templates
                WHERE user_id = ? AND name = ? COLLATE NOCASE
                """,
                (user_id, name),
            )
            row = cursor.fetchone()
            return self._row_to_template(cursor, row) if row else None

    @staticmethod
    def _replace_children(cursor, template_id: str, segments: List[RichSegment]) -> None:
        if not segments:
            raise ValueError("a segment template must contain at least one segment")
        cursor.execute(
            "DELETE FROM segment_template_segments WHERE template_id = ?",
            (template_id,),
        )
        for position, segment in enumerate(segments):
            cursor.execute(
                """
                INSERT INTO segment_template_segments (
                    id, template_id, position, type, content, chips, is_enabled,
                    name, color, description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generate_ulid(),
                    template_id,
                    position,
                    segment.type,
                    segment.content,
                    _json_dumps(segment.chips),
                    1 if segment.enabled else 0,
                    segment.name,
                    segment.color,
                    segment.description,
                ),
            )

    def create(self, template: SegmentTemplate) -> SegmentTemplate:
        if not template.user_id:
            raise ValueError("user_id is required")
        user_id = template.user_id
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO segment_templates
                    (id, user_id, name, description, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    template.id,
                    user_id,
                    template.name,
                    template.description,
                    _json_dumps(template.tags),
                ),
            )
            self._replace_children(cursor, template.id, template.segments)
        return self.get_by_id(template.id, user_id)

    def update(
        self, template_id: str, template: SegmentTemplate, user_id: str
    ) -> Optional[SegmentTemplate]:
        """Atomically update the parent and replace its full ordered collection."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE segment_templates
                SET name = ?, description = ?, tags = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (
                    template.name,
                    template.description,
                    _json_dumps(template.tags),
                    template_id,
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            self._replace_children(cursor, template_id, template.segments)
        return self.get_by_id(template_id, user_id)

    def delete(self, template_id: str, user_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM segment_templates WHERE id = ? AND user_id = ?",
                (template_id, user_id),
            )
            return cursor.rowcount > 0


# Module-level instances remain available to simple callers and tests.
segment_category_repo = SegmentCategoryRepository()
saved_segment_repo = SavedSegmentRepository()
segment_template_repo = SegmentTemplateRepository()
