"""Coerces a raw value against a `ModelAttributeDefinition`'s `field_type`,
shared by the shared-value editor (`ModelMetadataEditor.update_model_metadata`)
and the per-user overlay endpoint, so both reject the same way."""

from typing import Any

from src.features.models.attributes.records import ModelAttributeDefinition
from src.features.models.exceptions import InvalidModelMetadataException


def coerce_attribute_value(definition: ModelAttributeDefinition, raw_value: Any) -> Any:
    """Coerce `raw_value` to `definition.field_type`, raising
    `InvalidModelMetadataException` naming the field on failure. Rejects
    out-of-range/undeclared-option values rather than clamping or dropping them -
    a caller bug should surface, not be silently corrected."""
    field_type = definition.field_type
    config = definition.config or {}

    if field_type in ("slider", "number"):
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise InvalidModelMetadataException(f"'{definition.key}' must be a number, got {raw_value!r}")
        minimum = config.get("min")
        maximum = config.get("max")
        if minimum is not None and value < minimum:
            raise InvalidModelMetadataException(f"'{definition.key}' must be >= {minimum}, got {value}")
        if maximum is not None and value > maximum:
            raise InvalidModelMetadataException(f"'{definition.key}' must be <= {maximum}, got {value}")
        return value

    if field_type == "checkbox":
        if not isinstance(raw_value, bool):
            raise InvalidModelMetadataException(f"'{definition.key}' must be a boolean, got {raw_value!r}")
        return raw_value

    if field_type == "select":
        options = {opt.get("value") for opt in config.get("options", [])}
        if raw_value not in options:
            raise InvalidModelMetadataException(
                f"'{definition.key}' must be one of {sorted(options)}, got {raw_value!r}"
            )
        return raw_value

    if field_type == "tags":
        if not isinstance(raw_value, list):
            raise InvalidModelMetadataException(f"'{definition.key}' must be a list of strings, got {raw_value!r}")
        cleaned = []
        seen = set()
        for tag in raw_value:
            if not isinstance(tag, str):
                raise InvalidModelMetadataException(f"'{definition.key}' entries must be strings, got {tag!r}")
            tag = tag.strip()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            cleaned.append(tag)
        return cleaned

    # 'text' and any other field type: pass through as a string.
    if not isinstance(raw_value, str):
        raise InvalidModelMetadataException(f"'{definition.key}' must be a string, got {raw_value!r}")
    return raw_value
