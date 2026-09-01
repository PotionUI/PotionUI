import json
from typing import List, Optional

from src.platform.util.ids import generate_ulid

from src.features.models.attributes.records import ModelAttributeDefinition


class AttributeDefinitionRepository:
    """CRUD on `model_attribute_definitions`."""

    def list_all(self) -> List[ModelAttributeDefinition]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM model_attribute_definitions ORDER BY key ASC")
            return [ModelAttributeDefinition.from_row(row) for row in cursor.fetchall()]

    def get_by_id(self, definition_id: str) -> Optional[ModelAttributeDefinition]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM model_attribute_definitions WHERE id = ?", (definition_id,))
            row = cursor.fetchone()
            return ModelAttributeDefinition.from_row(row) if row else None

    def get_by_key(self, key: str) -> Optional[ModelAttributeDefinition]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM model_attribute_definitions WHERE key = ?", (key,))
            row = cursor.fetchone()
            return ModelAttributeDefinition.from_row(row) if row else None

    def for_model_type(self, model_type: str) -> List[ModelAttributeDefinition]:
        """Every definition that applies to `model_type` (declared for it, or
        declared for none - meaning "every type")."""
        return [d for d in self.list_all() if d.applies_to(model_type)]

    def create(self, definition: ModelAttributeDefinition) -> ModelAttributeDefinition:
        definition.id = definition.id or generate_ulid()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO model_attribute_definitions (
                    id, key, label, field_type, model_types, config, default_value,
                    description, per_user, admin_only, system, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                definition.id,
                definition.key,
                definition.label,
                definition.field_type,
                json.dumps(definition.model_types),
                json.dumps(definition.config),
                json.dumps(definition.default_value) if definition.default_value is not None else None,
                definition.description,
                int(definition.per_user),
                int(definition.admin_only),
                int(definition.system),
                definition.source,
            ))
        return self.get_by_id(definition.id)

    def update(self, definition: ModelAttributeDefinition) -> Optional[ModelAttributeDefinition]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE model_attribute_definitions
                SET key = ?, label = ?, field_type = ?, model_types = ?, config = ?,
                    default_value = ?, description = ?, per_user = ?, admin_only = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                definition.key,
                definition.label,
                definition.field_type,
                json.dumps(definition.model_types),
                json.dumps(definition.config),
                json.dumps(definition.default_value) if definition.default_value is not None else None,
                definition.description,
                int(definition.per_user),
                int(definition.admin_only),
                definition.id,
            ))
            if cursor.rowcount == 0:
                return None
        return self.get_by_id(definition.id)

    def delete(self, definition_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM model_attribute_definitions WHERE id = ?", (definition_id,))
            return cursor.rowcount > 0

    def delete_by_source(self, source: str) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM model_attribute_definitions WHERE source = ?", (source,))
            return cursor.rowcount
