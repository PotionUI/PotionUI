"""
Admin-set preset configuration: validation and `@config:<key>` resolution.

`preset.yml`'s `configuration:` block only declares the *schema* of admin-tunable
knobs (key -> {type, label, description}) - see `ConfigurationEntry` in
`src/features/presets/schema.py`. The *values* an admin sets for those keys are stored
per installed preset (`src/features/presets/records.py`'s `configuration` column)
and merged with the declared schema here and in `PresetManager.get_preset_configuration`.

See docs/presets.md "Configuration (admin-set)".
"""

from typing import Any, Dict, List, Optional

from .schema import CONFIGURATION_TYPES


def validate_configuration_value(config_type: str, value: Any, tag_repository) -> Optional[str]:
    """Validate one configuration value against its declared type.

    Returns an error string, or None if the value is valid. `tag_repository` is a
    `TagRepository` (or anything with `get_tag_by_id`) - passed in rather than
    imported at module scope so this module has no hard dependency on the
    persistence layer's import graph.
    """
    if config_type not in CONFIGURATION_TYPES:
        return f"unsupported configuration type '{config_type}'"

    if config_type == "model_tags":
        if not isinstance(value, list):
            return "value must be a list of tag IDs"
        for tag_id in value:
            if not isinstance(tag_id, str):
                return f"tag id must be a string, got {type(tag_id).__name__}"
            if not tag_repository.get_tag_by_id(tag_id):
                return f"unknown tag id: {tag_id}"
        return None

    # Unreachable while CONFIGURATION_TYPES only has one member, kept so a future
    # type added to the set without a branch here fails loudly instead of silently
    # accepting anything.
    return f"no validator implemented for configuration type '{config_type}'"


def merge_configuration_schema(
    declared: Optional[Dict[str, Dict[str, Any]]],
    stored_values: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Merge a preset's declared `configuration:` schema with its stored values.

    Returns the `entries` list for the `GET .../configuration` contract: one row
    per declared key, `value` is `None` when the admin has never set it.
    """
    entries: List[Dict[str, Any]] = []
    for key, entry in (declared or {}).items():
        entries.append({
            "key": key,
            "type": entry.get("type"),
            "label": entry.get("label"),
            "description": entry.get("description"),
            "value": stored_values.get(key),
        })
    return entries


def resolve_filter_tags(
    raw: Any,
    preset_configuration_values: Dict[str, Any],
) -> Optional[List[str]]:
    """Resolve a `model` field's `filter_tags` configuration to a concrete tag-id list.

    `raw` is either a literal list of tag IDs, or the string `"@config:<key>"` which
    is resolved against the preset's stored configuration values. Missing/empty
    resolved value -> None (no filtering), matching the documented backward-compat
    rule.
    """
    if raw is None:
        return None

    if isinstance(raw, str):
        if not raw.startswith("@config:"):
            return None
        key = raw[len("@config:"):]
        value = preset_configuration_values.get(key)
        if not value:
            return None
        return list(value)

    if isinstance(raw, list) and raw:
        return list(raw)

    return None


def resolve_field_filter_tags(raw: Any, preset_id: Optional[str]) -> Optional[List[str]]:
    """Resolve a field's `filter_tags` at option-listing (form schema) time,
    fetching the preset's stored configuration values when `@config:` indirection
    is used. Shared by every model-sourcing field type (`model`, `lora_picker`).

    The frontend passes the resolved list to `GET /api/presets/{id}/models`
    (`any_tag_ids`, OR semantics); this only resolves what to send.
    """
    if raw is None or not preset_id:
        return None

    preset_configuration_values: Dict[str, Any] = {}
    if isinstance(raw, str) and raw.startswith("@config:"):
        try:
            from src.features.presets.repository import preset_repo
            preset_configuration_values = preset_repo.get_preset_configuration(preset_id)
        except Exception:
            preset_configuration_values = {}

    return resolve_filter_tags(raw, preset_configuration_values)


def resolve_reactions_filter_tags(
    reactions: Optional[List[Dict[str, Any]]],
    preset_id: Optional[str],
) -> Optional[List[Dict[str, Any]]]:
    """Resolve `set_filter_tags` inside a `model`/`lora_picker` field's `reactions`
    (parsed `ReactionSpec` dicts) the same way `resolve_field_filter_tags` resolves
    the field's own static `filter_tags` - reactions are evaluated client-side with
    no DB access, so any `"@config:<key>"` indirection must be resolved here, at
    schema-serve time, into a concrete tag-id list (or None) before the reaction
    reaches the frontend.

    Returns `reactions` unchanged (same object) when there is nothing to resolve,
    so callers can unconditionally reassign `schema['reactions']` with the result.
    """
    if not reactions:
        return reactions

    resolved = []
    changed = False
    for reaction in reactions:
        then = reaction.get('then') if isinstance(reaction, dict) else None
        if not isinstance(then, dict) or 'set_filter_tags' not in then:
            resolved.append(reaction)
            continue

        changed = True
        new_then = dict(then)
        new_then['set_filter_tags'] = resolve_field_filter_tags(then['set_filter_tags'], preset_id)
        resolved.append({**reaction, 'then': new_then})

    return resolved if changed else reactions
