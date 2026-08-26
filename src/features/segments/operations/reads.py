"""
Resolve a single Segment Category / saved Segment / Segment Template by id,
enforcing ownership.

Not routes (each resource has its own `GET /{id}`, already a thin repository
passthrough) - these are the shared "resolve or raise" building block every
mutation in this package needs, and that outside callers (the `manage_segments`
chat/MCP tool surface) also reach for directly.
"""
from src.features.segments.dto import SavedSegment, SegmentCategory, SegmentTemplate
from src.features.segments.repository import (
    SavedSegmentRepository,
    SegmentCategoryRepository,
    SegmentTemplateRepository,
)


def get_category(category_repository: SegmentCategoryRepository, category_id: str, user_id: str) -> SegmentCategory:
    category = category_repository.get_by_id(category_id, user_id)
    if not category:
        raise ValueError("Category not found")
    return category


def get_segment(segment_repository: SavedSegmentRepository, segment_id: str, user_id: str) -> SavedSegment:
    segment = segment_repository.get_by_id(segment_id, user_id)
    if not segment:
        raise ValueError("Saved Segment not found")
    return segment


def get_template(template_repository: SegmentTemplateRepository, template_id: str, user_id: str) -> SegmentTemplate:
    template = template_repository.get_by_id(template_id, user_id)
    if not template:
        raise ValueError("Segment Template not found")
    return template
