"""Existence validation for model-picker values an LLM proposes for a form
field.

`start_generation` lets a model set an arbitrary `model`-type field (e.g.
`diffusion_model`) directly, with no live picker to constrain it to an
installed model - it types a filename or `model:<id>` from memory (its own
guess, or something it read out of `get_preset_info`/`list_models` a while
ago and half-remembered).

Exactly like `media_values.py`'s reasoning for media fields: `bind_form`
resolves a `model:<id>` ref later, per-backend, and does not even look at a
plain filename string - it is neither required-empty nor a model ref, so it
sails straight through and the generation is only discovered dead deep inside
a pipe's model loader, long after the model could have corrected itself and
long after the caller has moved on. This lives at the tool boundary, using
the same lookup (`utils.lookup_model`) get_model_info's callers already share,
so the answer here always agrees with what the model would see if it had
called get_model_info itself.
"""

import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Imported lazily inside validate_model_value(), not at module level:
# builtin/__init__.py imports every builtin tool eagerly, including
# start_generation_tool, which imports this module - importing
# `src.features.llm.tools.builtin.utils` up here would force that package
# import mid-way through its own initialization and fail with a circular
# import.
MODEL_FIELD_TYPES = {"model"}


def _walk_model_field_names(node: Any, result: Set[str]) -> None:
    if isinstance(node, dict):
        name = node.get("name")
        if isinstance(name, str):
            config = node.get("configuration") or {}
            if node.get("type") in MODEL_FIELD_TYPES or config.get("model_type"):
                result.add(name)
        for child in node.get("children") or []:
            _walk_model_field_names(child, result)
    elif isinstance(node, list):
        for child in node:
            _walk_model_field_names(child, result)


def model_field_names(schema_properties: Optional[Dict[str, Any]]) -> Set[str]:
    """Names of the model-carrying fields in a serialized form schema.

    `get_form_schema`'s `form_schema.properties` is not a flat `{name:
    spec}` map - every preset lays fields out under a `tabs` root whose
    `children` nest `tab`/`section`/`row` wrappers arbitrarily deep before
    reaching an actual field (see docs/presets.md's form layout). This walks
    that tree rather than assuming the flat JSON-schema shape."""
    result: Set[str] = set()
    if isinstance(schema_properties, dict):
        for spec in schema_properties.values():
            _walk_model_field_names(spec, result)
    return result


def validate_model_value(
    field_name: str,
    value: Any,
    model_index_manager: Any,
) -> List[str]:
    """Errors for one LLM-proposed model field value; empty means acceptable.

    A `None`/empty value is a clear, not a selection, and is always allowed -
    bind_form's own required-field check is the one responsible for rejecting
    that. A `None` model_index_manager means there is no way to check - skip
    rather than guess, exactly as the media validator does for a missing
    storage_dir.
    """
    if value is None or value == "" or value == []:
        return []
    if model_index_manager is None:
        logger.warning(
            "no model_index_manager available to validate LLM-proposed model for '%s'", field_name
        )
        return []

    from src.features.llm.tools.builtin.utils import lookup_model

    info = lookup_model(model_index_manager, value)
    if info.get("description") == "Model not found in index":
        return [
            f"'{field_name}': no installed model matches {value!r}. Call get_preset_info or "
            f"list_models to see the models actually installed, and use the exact filename or "
            f"`model:<id>` reference they return."
        ]
    return []


def preset_form_model_errors(
    preset_manager: Any,
    model_index_manager: Any,
    preset_id: str,
    mode: str,
    proposed: Dict[str, Any],
) -> List[str]:
    """Errors for `proposed` field values against `preset_id`/`mode`'s model
    fields, loading the form schema itself. Mirrors
    `media_values.preset_form_media_errors`."""
    if not proposed or preset_manager is None:
        return []
    try:
        schema_data = preset_manager.get_form_schema(preset_id, mode=mode)
        fields = model_field_names(schema_data.get("form_schema", {}).get("properties", {}))
    except Exception as e:
        logger.debug(f"Could not load form schema for model validation: {e}")
        return []

    errors: List[str] = []
    for field_name in fields:
        if field_name in proposed:
            errors.extend(validate_model_value(field_name, proposed[field_name], model_index_manager))
    return errors
