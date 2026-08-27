"""Mutations on attribute definitions and their per-user value overlay.

Reads (list/get) go straight from the route to `AttributeDefinitionRepository` -
this manager holds mutations only (create/update/delete a definition, a
plugin's manifest upsert/removal, and the per-user overlay write).
"""

import re
from typing import Any, Dict, List, Optional

from src.features.models.attributes.exceptions import (
    AttributeDefinitionNotFoundException,
    InvalidAttributeDefinitionException,
    SystemAttributeDefinitionException,
)
from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.attributes.repository import AttributeDefinitionRepository
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.attributes.validation import coerce_attribute_value
from src.features.models.exceptions import InvalidModelMetadataException

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
FIELD_TYPES = {"slider", "number", "text", "select", "checkbox", "tags"}


class ModelAttributeDefinitionsEditor:

    def __init__(
        self,
        definition_repository: AttributeDefinitionRepository,
        user_attribute_repository: UserModelAttributeRepository,
    ):
        self.definitions = definition_repository
        self.user_attributes = user_attribute_repository

    # --- Definition CRUD (admin) ---

    def create(self, data: Dict[str, Any]) -> ModelAttributeDefinition:
        key = (data.get("key") or "").strip()
        self._validate_new_key(key)
        field_type = data.get("field_type")
        if field_type not in FIELD_TYPES:
            raise InvalidAttributeDefinitionException(f"Unknown field_type: {field_type!r}")

        definition = ModelAttributeDefinition(
            key=key,
            label=data.get("label") or key,
            field_type=field_type,
            model_types=list(data.get("model_types") or []),
            config=dict(data.get("config") or {}),
            default_value=data.get("default_value"),
            description=data.get("description"),
            per_user=bool(data.get("per_user", False)),
            admin_only=bool(data.get("admin_only", False)),
            system=False,
            source="user",
        )
        return self.definitions.create(definition)

    def update(self, definition_id: str, data: Dict[str, Any]) -> ModelAttributeDefinition:
        existing = self.definitions.get_by_id(definition_id)
        if not existing:
            raise AttributeDefinitionNotFoundException(f"Attribute definition '{definition_id}' not found")

        if existing.system:
            if "key" in data and data["key"] != existing.key:
                raise SystemAttributeDefinitionException(
                    f"'{existing.key}' is a system attribute - its key can't change"
                )
            if "field_type" in data and data["field_type"] != existing.field_type:
                raise SystemAttributeDefinitionException(
                    f"'{existing.key}' is a system attribute - its field_type can't change"
                )
            key, field_type = existing.key, existing.field_type
        else:
            key = (data.get("key") or existing.key).strip()
            if key != existing.key:
                self._validate_new_key(key)
            field_type = data.get("field_type", existing.field_type)
            if field_type not in FIELD_TYPES:
                raise InvalidAttributeDefinitionException(f"Unknown field_type: {field_type!r}")

        existing.key = key
        existing.field_type = field_type
        existing.label = data.get("label", existing.label)
        existing.model_types = list(data["model_types"]) if "model_types" in data else existing.model_types
        existing.config = dict(data["config"]) if "config" in data else existing.config
        existing.default_value = data.get("default_value", existing.default_value)
        existing.description = data.get("description", existing.description)
        existing.per_user = bool(data.get("per_user", existing.per_user))
        existing.admin_only = bool(data.get("admin_only", existing.admin_only))

        updated = self.definitions.update(existing)
        if not updated:
            raise AttributeDefinitionNotFoundException(f"Attribute definition '{definition_id}' not found")
        return updated

    def delete(self, definition_id: str) -> None:
        existing = self.definitions.get_by_id(definition_id)
        if not existing:
            raise AttributeDefinitionNotFoundException(f"Attribute definition '{definition_id}' not found")
        if existing.system:
            raise SystemAttributeDefinitionException(f"'{existing.key}' is a system attribute and can't be deleted")
        self.definitions.delete(definition_id)

    def _validate_new_key(self, key: str) -> None:
        if not KEY_PATTERN.match(key or ""):
            raise InvalidAttributeDefinitionException(
                f"'{key}' is not a valid attribute key (must match ^[a-z][a-z0-9_]*$)"
            )
        if self.definitions.get_by_key(key):
            raise InvalidAttributeDefinitionException(f"An attribute with key '{key}' already exists")

    # --- Plugin manifest wiring (model_metadata_fields:) ---

    def upsert_from_plugin(self, plugin_id: str, entries: List[Dict[str, Any]]) -> Optional[str]:
        """Upsert a plugin's `model_metadata_fields:` entries as definitions it
        owns (`source=plugin_id`, `system=False`). Returns an error message
        naming the key on a collision with a definition owned by anyone else
        (including core), or None on success."""
        for entry in entries:
            key = entry.get("key")
            field_type = entry.get("field_type")
            label = entry.get("label")
            if not key or not field_type or not label:
                return "model_metadata_fields entry missing 'key', 'label' or 'field_type'"

            existing = self.definitions.get_by_key(key)
            if existing and existing.source != plugin_id:
                return f"Model attribute already registered: '{key}' (owned by '{existing.source}')"

            definition = ModelAttributeDefinition(
                id=existing.id if existing else None,
                key=key,
                label=label,
                field_type=field_type,
                model_types=list(entry.get("model_types") or []),
                config=dict(entry.get("config") or {}),
                default_value=entry.get("default_value"),
                description=entry.get("description"),
                per_user=bool(entry.get("per_user", False)),
                admin_only=bool(entry.get("admin_only", False)),
                system=False,
                source=plugin_id,
            )
            if existing:
                self.definitions.update(definition)
            else:
                self.definitions.create(definition)

        return None

    def remove_source(self, source: str) -> None:
        self.definitions.delete_by_source(source)

    # --- Per-user value overlay ---

    def update_user_values(self, model_id: str, user_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert `values` into the caller's per-model overlay. Every key must
        name a `per_user` definition - a shared-only key is rejected rather than
        silently accepted into an overlay nobody but this user will ever read."""
        by_key = {d.key: d for d in self.definitions.list_all()}

        coerced: Dict[str, Any] = {}
        for key, raw_value in values.items():
            definition = by_key.get(key)
            if definition is None or not definition.per_user:
                raise InvalidModelMetadataException(f"'{key}' is not a per-user attribute")
            coerced[key] = coerce_attribute_value(definition, raw_value)

        return self.user_attributes.upsert_many(user_id, model_id, coerced)
