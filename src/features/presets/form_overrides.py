"""
Admin per-field form overrides: validation and schema-merge.

`preset.yml`'s form fields declare their own `default`/visibility, but an
admin sometimes needs to pin, lock, or hide a specific field for every user of
an installed preset without editing the preset's YAML. Overrides are stored
per installed preset (`src/features/presets/records.py`'s `form_overrides`
column), keyed by mode name and then field name:

    {"txt2img": {"steps": {"default": 30, "editable": false}}}

An override applies to every form *variant* of its mode (the field inventory
used for validation is the union of fields across all variants - see
`_mode_field_inventory` below): overrides are per-mode, not per-variant.

Three independent knobs per field:
  - `default`: replaces the field's declared default.
  - `editable: false`: the field becomes read-only; client-supplied values are
    ignored (see `src/features/forms/binding.py::bind_form`'s `field_overrides`
    handling - locked/hidden fields never take a client value).
  - `visible: false`: the field is removed from the rendered form schema
    entirely (server-side removal, not just a presentation hint).

A field's override is CLEARED by sending an empty object (`{}`) or `null` for
that field in the PUT payload - see `PresetManager.set_form_overrides`.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List

from src.features.forms.binding import _expand_form_fields, _flatten_fields, _validate_field
from src.features.presets.templates import FieldTemplate, PresetTemplate

_OVERRIDE_KEYS = frozenset({"default", "editable", "visible"})

# Field types that are pure layout - no name, no value of their own - and so
# have no reason to render once every one of their children has been removed
# by a `visible: false` override. `gate` deliberately excluded: it keeps its
# own name and a real boolean value (see src/features/fields/gate.py), so it
# stays even when the children it governs are all hidden.
_LAYOUT_CONTAINER_TYPES = frozenset({"tabs", "tab", "row", "group", "accordion", "section"})


def is_clear_signal(value: Any) -> bool:
    """An override value that clears (removes) the stored override for a field."""
    return not value


def mode_field_inventory(preset_template: PresetTemplate, mode: str) -> Dict[str, FieldTemplate]:
    """Every named field across every form variant of `mode`, expanded (no
    `@loop`/external-`children` declarations left unresolved) and flattened.

    First-seen-variant wins when the same field name appears in more than one
    variant with a different declaration - variants sharing a field name are
    expected to agree on its shape.
    """
    mode_data = (preset_template.modes or {}).get(mode)
    if mode_data is None or not mode_data.forms:
        return {}

    inventory: Dict[str, FieldTemplate] = {}
    for form in mode_data.forms:
        resolved_fields = _expand_form_fields(form.fields, preset_template)
        variant_index: Dict[str, FieldTemplate] = {}
        _flatten_fields(resolved_fields, variant_index)
        for name, spec in variant_index.items():
            inventory.setdefault(name, spec)

    return inventory


def validate_form_overrides(
    preset_template: PresetTemplate,
    mode: str,
    overrides: Dict[str, Any],
) -> List[str]:
    """Validate a PUT payload's `overrides` dict against `mode`'s field inventory.

    Returns a list of error strings (empty if valid). A "clear" entry (`{}` or
    `null`) for an unknown field is not an error - clearing a stale override
    for a field that no longer exists is harmless.
    """
    errors: List[str] = []
    inventory = mode_field_inventory(preset_template, mode)

    for name, override in overrides.items():
        if is_clear_signal(override):
            continue

        if not isinstance(override, dict):
            errors.append(f"{name}: override must be an object")
            continue

        spec = inventory.get(name)
        if spec is None:
            errors.append(f"unknown field '{name}' for mode '{mode}'")
            continue

        unknown_keys = sorted(set(override.keys()) - _OVERRIDE_KEYS)
        if unknown_keys:
            errors.append(f"{name}: unknown override key(s) {unknown_keys}")

        if "editable" in override and not isinstance(override["editable"], bool):
            errors.append(f"{name}: 'editable' must be a boolean")

        if "visible" in override and not isinstance(override["visible"], bool):
            errors.append(f"{name}: 'visible' must be a boolean")

        if "default" in override:
            value = override["default"]
            value_errors: List[str] = []
            _validate_field(name, value, spec, value_errors, {})
            errors.extend(value_errors)

    return errors


def build_inventory_entries(
    preset_template: PresetTemplate,
    mode: str,
    stored_overrides_for_mode: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """The unmerged `fields` list for the `GET .../form-overrides` contract:
    one entry per field in the mode's inventory, `override` is `None` when the
    admin has never set one for that field."""
    inventory = mode_field_inventory(preset_template, mode)
    entries: List[Dict[str, Any]] = []
    for name, spec in inventory.items():
        entries.append({
            "name": name,
            "label": spec.label or name.replace("_", " ").title(),
            "type": spec.type,
            "preset_default": spec.default,
            "override": stored_overrides_for_mode.get(name) or None,
        })
    return entries


def apply_overrides_to_fields(
    fields: List[FieldTemplate],
    overrides_for_mode: Dict[str, Any],
) -> List[FieldTemplate]:
    """Apply `overrides_for_mode` ({field_name: {default?, editable?, visible?}})
    onto an already-resolved `FieldTemplate` tree and return the resulting list.

    Called by `PresetFormSerializer.process_form_fields` immediately after
    `_resolve_external_children` (which expands `@loop`/external-`children`
    declarations) - the single funnel every rendered field passes through, so
    expanded field names (e.g. `slot_2_model`) match the override keys, and
    the objects being walked are already freshly-constructed copies (never
    the preset loader's cached originals) safe to replace.

    - `visible: false` REMOVES the field entirely, including nested inside a
      container's `children` - server-side removal is the security boundary
      (client-side visibility such as `audience`/`reactions` is presentation
      only). Because removal happens here, before `_process_field_recursive`
      builds the schema's `properties`/`required`, a removed field can never
      appear in either.
    - A layout container (`_LAYOUT_CONTAINER_TYPES`) that had children before
      filtering and has none left afterward is removed too - recursively, so
      a section whose only child was a now-empty group also disappears.
      Recursion into a field's own children happens before this check runs on
      the field itself, so the fold-up is bottom-up: a grandchild's removal
      can empty a child, which can in turn empty its parent.
    - `editable: false` sets `FieldTemplate.readonly = True`, which
      `base_field.py`'s `create_base_schema` turns into `readonly: true` on
      the rendered schema for every field type (container and leaf alike).
    - `default` replaces the field's `default`.

    See docs/presets.md "Form overrides (admin-set)".
    """
    if not overrides_for_mode:
        return fields

    result: List[FieldTemplate] = []
    for f in fields:
        override = overrides_for_mode.get(f.name) if f.name else None
        if override and override.get("visible") is False:
            continue

        new_children = f.children
        if isinstance(f.children, list):
            new_children = apply_overrides_to_fields(f.children, overrides_for_mode)
            if f.type in _LAYOUT_CONTAINER_TYPES and f.children and not new_children:
                continue

        changes: Dict[str, Any] = {}
        if new_children is not f.children:
            changes["children"] = new_children
        if override:
            if "default" in override:
                changes["default"] = override["default"]
            if override.get("editable") is False:
                changes["readonly"] = True

        result.append(dataclasses.replace(f, **changes) if changes else f)

    return result
