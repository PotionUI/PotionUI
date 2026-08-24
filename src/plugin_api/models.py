"""Model attribute key identifiers.

`WellKnownModelMetadataField` names the attribute keys core seeds as system
definitions (e.g. a LoRA's `strength`, `triggers`) so a plugin references the
constant instead of retyping the string literal - useful when a plugin reads a
model's `model_metadata` values rather than declaring its own attribute via the
manifest `model_metadata_fields:` section.
"""

from src.features.models.attributes.well_known import WellKnownModelAttribute as WellKnownModelMetadataField

__all__ = [
    "WellKnownModelMetadataField",
]
