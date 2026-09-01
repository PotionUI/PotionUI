"""Startup seeding for core's system attribute definitions (mirrors
`register_builtin_templates`'s idiom, but writes DB rows instead of populating
an in-memory registry - definitions are UI-managed, so this only *ensures they
exist*, it never overwrites an admin's edits to label/config/default)."""

from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.well_known import WellKnownModelAttribute

BUILTIN_DEFINITIONS = [
    ModelAttributeDefinition(
        key=WellKnownModelAttribute.TRIGGERS,
        label="Trigger words",
        field_type="tags",
        model_types=[],
        per_user=False,
        admin_only=False,
        system=True,
        source="core",
    ),
    ModelAttributeDefinition(
        key=WellKnownModelAttribute.LORA_STRENGTH,
        label="Recommended strength",
        field_type="range",
        model_types=["lora"],
        config={"min": -2, "max": 2, "step": 0.05},
        default_value=None,
        description="Strength range this LoRA's author recommends; a single value is a 1:1 range",
        per_user=False,
        admin_only=False,
        system=True,
        source="core",
    ),
]


def ensure_builtin_attribute_definitions(repository: AttributeDefinitionRepository) -> None:
    """Insert each builtin definition that isn't already in the DB by key.
    A definition already present (including one an admin has since edited)
    is left untouched."""
    for definition in BUILTIN_DEFINITIONS:
        if repository.get_by_key(definition.key) is None:
            repository.create(definition)
