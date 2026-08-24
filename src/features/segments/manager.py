"""Business logic for saved Segments, Segment Templates, and categories."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple


from src.features.segments.dto import (
    SavedSegment,
    SavedSegmentRequest,
    SegmentCategory,
    SegmentCategoryRequest,
    SegmentTemplate,
    SegmentTemplateRequest,
)
from src.platform.plugins import PluginRegistry
from src.features.segments.hooks import SEGMENT_HOOKS
from src.features.segments.repository import (
    SavedSegmentRepository,
    SegmentCategoryRepository,
    SegmentTemplateRepository,
)
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)


class SegmentManager:
    """Coordinates the three distinct library resources.

    A saved Segment is one reusable rich card.  A Segment Template is an
    ordered aggregate of one or more rich cards.  Categories organize saved
    Segments only.
    """

    def __init__(
        self,
        category_repository: SegmentCategoryRepository,
        template_repository: SegmentTemplateRepository,
        plugin_registry: PluginRegistry,
        saved_segment_repository: Optional[SavedSegmentRepository] = None,
    ):
        self.categories = category_repository
        self.segments = saved_segment_repository or SavedSegmentRepository()
        self.templates = template_repository
        self.plugins = plugin_registry

    def _execute_hook(self, hook: str, data: dict) -> Tuple[dict, bool]:
        context, _ = self.plugins.execute_hook(hook, initial_data=data)
        blocked = bool(context.data.get("blocked", False))
        return context.data, blocked

    def _before(self, hook: str, data: dict, fallback_reason: str) -> None:
        hook_data, blocked = self._execute_hook(hook, data)
        if blocked:
            reason = hook_data.get("block_reason", fallback_reason)
            logger.warning("Segment-domain operation blocked by plugin: %s", reason)
            raise ValueError(reason)

    @staticmethod
    def _unique_error(exc: sqlite3.IntegrityError, message: str) -> ValueError:
        if "UNIQUE" in str(exc).upper():
            return ValueError(message)
        return ValueError(f"Database constraint failed: {exc}")

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def get_categories(self, user_id: str) -> List[SegmentCategory]:
        return self.categories.get_all(user_id)

    def get_category_by_id(self, category_id: str, user_id: str) -> SegmentCategory:
        category = self.categories.get_by_id(category_id, user_id)
        if not category:
            raise ValueError("Category not found")
        return category

    def create_category(
        self, request: SegmentCategoryRequest, user_id: str
    ) -> SegmentCategory:
        self._before(
            SEGMENT_HOOKS.before_create_category,
            {
                "name": request.name,
                "description": request.description,
                "color": request.color,
                "user_id": user_id,
            },
            "Category creation blocked",
        )
        if self.categories.get_by_name(request.name, user_id):
            raise ValueError("Category with this name already exists")

        category = SegmentCategory(
            id=generate_ulid(),
            user_id=user_id,
            name=request.name,
            description=request.description,
            color=request.color,
        )
        try:
            created = self.categories.create(category)
        except sqlite3.IntegrityError as exc:
            raise self._unique_error(exc, "Category with this name already exists") from exc

        self._execute_hook(
            SEGMENT_HOOKS.after_create_category,
            {"category_id": created.id, "name": created.name, "user_id": user_id},
        )
        return created

    def update_category(
        self,
        category_id: str,
        request: SegmentCategoryRequest,
        user_id: str,
    ) -> SegmentCategory:
        existing = self.get_category_by_id(category_id, user_id)
        self._before(
            SEGMENT_HOOKS.before_update_category,
            {
                "category_id": category_id,
                "old_name": existing.name,
                "new_name": request.name,
                "description": request.description,
                "color": request.color,
                "user_id": user_id,
            },
            "Category update blocked",
        )
        duplicate = self.categories.get_by_name(request.name, user_id)
        if duplicate and duplicate.id != category_id:
            raise ValueError("Category with this name already exists")

        category = SegmentCategory(
            id=category_id,
            user_id=user_id,
            name=request.name,
            description=request.description,
            color=request.color,
            created_at=existing.created_at,
            updated_at=datetime.now(),
        )
        try:
            updated = self.categories.update(category_id, category, user_id)
        except sqlite3.IntegrityError as exc:
            raise self._unique_error(exc, "Category with this name already exists") from exc
        if not updated:
            raise ValueError("Category not found")

        self._execute_hook(
            SEGMENT_HOOKS.after_update_category,
            {"category_id": updated.id, "name": updated.name, "user_id": user_id},
        )
        return updated

    def delete_category(self, category_id: str, user_id: str) -> bool:
        existing = self.get_category_by_id(category_id, user_id)
        self._before(
            SEGMENT_HOOKS.before_delete_category,
            {"category_id": category_id, "name": existing.name, "user_id": user_id},
            "Category deletion blocked",
        )
        if self.categories.has_saved_segments(category_id, user_id):
            raise ValueError("Cannot delete category with existing saved segments")
        try:
            deleted = self.categories.delete(category_id, user_id)
        except sqlite3.IntegrityError as exc:
            raise ValueError("Cannot delete category with existing saved segments") from exc
        if not deleted:
            raise ValueError("Category not found")
        self._execute_hook(
            SEGMENT_HOOKS.after_delete_category,
            {"category_id": category_id, "name": existing.name, "user_id": user_id},
        )
        return True

    # ------------------------------------------------------------------
    # Saved Segments (single reusable cards)
    # ------------------------------------------------------------------

    def get_segments(
        self, user_id: str, category_id: Optional[str] = None
    ) -> List[SavedSegment]:
        if category_id:
            self.get_category_by_id(category_id, user_id)
        return self.segments.get_all(user_id, category_id)

    def get_segment_by_id(self, segment_id: str, user_id: str) -> SavedSegment:
        segment = self.segments.get_by_id(segment_id, user_id)
        if not segment:
            raise ValueError("Saved Segment not found")
        return segment

    def create_segment(
        self, request: SavedSegmentRequest, user_id: str
    ) -> SavedSegment:
        category = self.get_category_by_id(request.category_id, user_id)
        self._before(
            SEGMENT_HOOKS.before_create_segment,
            {
                "name": request.name,
                "category_id": request.category_id,
                "segment": request.model_dump(mode="json"),
                "user_id": user_id,
            },
            "Saved Segment creation blocked",
        )
        if self.segments.get_by_name(request.name, user_id):
            raise ValueError("Saved Segment with this name already exists")

        segment = SavedSegment(
            id=generate_ulid(),
            user_id=user_id,
            name=request.name,
            category_id=request.category_id,
            type=request.type,
            content=request.content,
            chips=request.chips,
            enabled=request.enabled,
            color=request.color,
            effective_color=request.color or category.color,
            description=request.description,
            tags=request.tags,
        )
        try:
            created = self.segments.create(segment)
        except sqlite3.IntegrityError as exc:
            raise self._unique_error(
                exc, "Saved Segment with this name already exists"
            ) from exc
        if not created:
            raise ValueError("Category not found")

        self._execute_hook(
            SEGMENT_HOOKS.after_create_segment,
            {
                "segment_id": created.id,
                "name": created.name,
                "category_id": created.category_id,
                "user_id": user_id,
            },
        )
        return created

    def update_segment(
        self,
        segment_id: str,
        request: SavedSegmentRequest,
        user_id: str,
    ) -> SavedSegment:
        existing = self.get_segment_by_id(segment_id, user_id)
        category = self.get_category_by_id(request.category_id, user_id)
        self._before(
            SEGMENT_HOOKS.before_update_segment,
            {
                "segment_id": segment_id,
                "old_name": existing.name,
                "new_name": request.name,
                "category_id": request.category_id,
                "segment": request.model_dump(mode="json"),
                "user_id": user_id,
            },
            "Saved Segment update blocked",
        )
        duplicate = self.segments.get_by_name(request.name, user_id)
        if duplicate and duplicate.id != segment_id:
            raise ValueError("Saved Segment with this name already exists")

        segment = SavedSegment(
            id=segment_id,
            user_id=user_id,
            name=request.name,
            category_id=request.category_id,
            type=request.type,
            content=request.content,
            chips=request.chips,
            enabled=request.enabled,
            color=request.color,
            effective_color=request.color or category.color,
            description=request.description,
            tags=request.tags,
            created_at=existing.created_at,
            updated_at=datetime.now(),
        )
        try:
            updated = self.segments.update(segment_id, segment, user_id)
        except sqlite3.IntegrityError as exc:
            raise self._unique_error(
                exc, "Saved Segment with this name already exists"
            ) from exc
        if not updated:
            raise ValueError("Saved Segment not found")

        self._execute_hook(
            SEGMENT_HOOKS.after_update_segment,
            {
                "segment_id": updated.id,
                "name": updated.name,
                "category_id": updated.category_id,
                "user_id": user_id,
            },
        )
        return updated

    def delete_segment(self, segment_id: str, user_id: str) -> bool:
        existing = self.get_segment_by_id(segment_id, user_id)
        self._before(
            SEGMENT_HOOKS.before_delete_segment,
            {
                "segment_id": segment_id,
                "name": existing.name,
                "category_id": existing.category_id,
                "user_id": user_id,
            },
            "Saved Segment deletion blocked",
        )
        if not self.segments.delete(segment_id, user_id):
            raise ValueError("Saved Segment not found")
        self._execute_hook(
            SEGMENT_HOOKS.after_delete_segment,
            {"segment_id": segment_id, "name": existing.name, "user_id": user_id},
        )
        return True

    # ------------------------------------------------------------------
    # Segment Templates (ordered aggregates)
    # ------------------------------------------------------------------

    def get_templates(self, user_id: str) -> List[SegmentTemplate]:
        return self.templates.get_all(user_id)

    def get_template_by_id(self, template_id: str, user_id: str) -> SegmentTemplate:
        template = self.templates.get_by_id(template_id, user_id)
        if not template:
            raise ValueError("Segment Template not found")
        return template

    def create_template(
        self, request: SegmentTemplateRequest, user_id: str
    ) -> SegmentTemplate:
        self._before(
            SEGMENT_HOOKS.before_create_template,
            {
                "name": request.name,
                "description": request.description,
                "tags": request.tags,
                "segments": [segment.model_dump(mode="json") for segment in request.segments],
                "user_id": user_id,
            },
            "Segment Template creation blocked",
        )
        if self.templates.get_by_name(request.name, user_id):
            raise ValueError("Segment Template with this name already exists")

        template = SegmentTemplate(
            id=generate_ulid(),
            user_id=user_id,
            name=request.name,
            description=request.description,
            tags=request.tags,
            segments=request.segments,
        )
        try:
            created = self.templates.create(template)
        except sqlite3.IntegrityError as exc:
            raise self._unique_error(
                exc, "Segment Template with this name already exists"
            ) from exc

        self._execute_hook(
            SEGMENT_HOOKS.after_create_template,
            {
                "template_id": created.id,
                "name": created.name,
                "segment_count": len(created.segments),
                "user_id": user_id,
            },
        )
        return created

    def update_template(
        self,
        template_id: str,
        request: SegmentTemplateRequest,
        user_id: str,
    ) -> SegmentTemplate:
        existing = self.get_template_by_id(template_id, user_id)
        self._before(
            SEGMENT_HOOKS.before_update_template,
            {
                "template_id": template_id,
                "old_name": existing.name,
                "new_name": request.name,
                "description": request.description,
                "tags": request.tags,
                "segments": [segment.model_dump(mode="json") for segment in request.segments],
                "user_id": user_id,
            },
            "Segment Template update blocked",
        )
        duplicate = self.templates.get_by_name(request.name, user_id)
        if duplicate and duplicate.id != template_id:
            raise ValueError("Segment Template with this name already exists")

        template = SegmentTemplate(
            id=template_id,
            user_id=user_id,
            name=request.name,
            description=request.description,
            tags=request.tags,
            segments=request.segments,
            created_at=existing.created_at,
            updated_at=datetime.now(),
        )
        try:
            updated = self.templates.update(template_id, template, user_id)
        except sqlite3.IntegrityError as exc:
            raise self._unique_error(
                exc, "Segment Template with this name already exists"
            ) from exc
        if not updated:
            raise ValueError("Segment Template not found")

        self._execute_hook(
            SEGMENT_HOOKS.after_update_template,
            {
                "template_id": updated.id,
                "name": updated.name,
                "segment_count": len(updated.segments),
                "user_id": user_id,
            },
        )
        return updated

    def delete_template(self, template_id: str, user_id: str) -> bool:
        existing = self.get_template_by_id(template_id, user_id)
        self._before(
            SEGMENT_HOOKS.before_delete_template,
            {"template_id": template_id, "name": existing.name, "user_id": user_id},
            "Segment Template deletion blocked",
        )
        if not self.templates.delete(template_id, user_id):
            raise ValueError("Segment Template not found")
        self._execute_hook(
            SEGMENT_HOOKS.after_delete_template,
            {"template_id": template_id, "name": existing.name, "user_id": user_id},
        )
        return True
