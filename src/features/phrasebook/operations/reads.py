"""
Resolve a single phrasebook Category / Value by id, enforcing ownership.

Not routes (each resource has its own `GET /{id}`, already a thin repository
passthrough) - these are the shared "resolve or raise" building block every
mutation in this package needs, and that outside callers (the phrasebook
chat/MCP tool surface) also reach for directly.
"""
from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookValue
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)


def get_category(
    category_repository: PhrasebookCategoryRepository, category_id: str, user_id: str
) -> PhrasebookCategory:
    category = category_repository.get_by_id(category_id, user_id)
    if not category:
        raise ValueError("Category not found")
    return category


def get_value(
    value_repository: PhrasebookValueRepository, value_id: str, user_id: str
) -> PhrasebookValue:
    value = value_repository.get_by_id(value_id, user_id)
    if not value:
        raise ValueError("Value not found")
    return value
