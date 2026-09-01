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

    if field_type == "range":
        return _coerce_range(definition, raw_value, config)

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


def _coerce_range(definition: ModelAttributeDefinition, raw_value: Any, config: dict) -> Any:
    """A closed numeric interval, stored as the two-element list `[low, high]`.

    `None` means "not set" and is the only way to clear one - a range attribute
    describes a property the model may simply not declare (a LoRA whose author
    published no recommended strength), unlike a slider whose default always
    stands in. A bare number or a one-element list is the degenerate interval
    `[x, x]`, so a caller holding a single value never has to widen it itself.
    An inverted pair is rejected rather than sorted: the writer meant something
    the stored interval wouldn't say back.
    """
    if raw_value is None:
        return None

    if isinstance(raw_value, (list, tuple)):
        bounds = list(raw_value)
        if len(bounds) not in (1, 2):
            raise InvalidModelMetadataException(
                f"'{definition.key}' must be a [low, high] pair, got {raw_value!r}"
            )
    else:
        bounds = [raw_value]

    try:
        low, high = (float(bounds[0]), float(bounds[-1]))
    except (TypeError, ValueError):
        raise InvalidModelMetadataException(f"'{definition.key}' bounds must be numbers, got {raw_value!r}")

    if low > high:
        raise InvalidModelMetadataException(f"'{definition.key}' low bound must not exceed high, got {raw_value!r}")

    minimum = config.get("min")
    maximum = config.get("max")
    if minimum is not None and low < minimum:
        raise InvalidModelMetadataException(f"'{definition.key}' must be >= {minimum}, got {low}")
    if maximum is not None and high > maximum:
        raise InvalidModelMetadataException(f"'{definition.key}' must be <= {maximum}, got {high}")

    return [low, high]
