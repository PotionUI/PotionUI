"""Attributes v2: DB-backed, UI-managed model attribute definitions with
per-user value overlays. Supersedes the migration-133 code registry
(`src.platform.plugins.model_metadata_fields`).
"""

from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.attributes.editor import ModelAttributeDefinitionsEditor
from src.features.models.attributes.exceptions import (
    AttributeDefinitionNotFoundException,
    InvalidAttributeDefinitionException,
    SystemAttributeDefinitionException,
)
from src.features.models.attributes.seeding import ensure_builtin_attribute_definitions
from src.features.models.attributes.well_known import WellKnownModelAttribute

__all__ = [
    "ModelAttributeDefinition",
    "AttributeDefinitionRepository",
    "UserModelAttributeRepository",
    "ModelAttributeDefinitionsEditor",
    "AttributeDefinitionNotFoundException",
    "InvalidAttributeDefinitionException",
    "SystemAttributeDefinitionException",
    "ensure_builtin_attribute_definitions",
    "WellKnownModelAttribute",
]
