"""Building the wizard's starting point: `default_form`/`default_history`
(see `backend/api.py`'s `/presets/import/analyze` and `.../source`) from a
`suggest.suggest_fields` analysis - the same "obvious" candidates
`suggest.py` already flags, arranged into the contract's `form`/`history`
shape instead of a flat candidate list.

Foundational roles (seed, the two prompts, batch size) never become an
`Item` here - see emit.py's module docstring; they are wired unconditionally
regardless of what the admin's form contains.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .schema import FieldItem, FieldMapping, FormTab, ImportForm, HistoryEntry, Item, SectionItem
from .suggest import AnalyzeResult, InputCandidate

_FOUNDATIONAL_ROLES = frozenset({"seed", "prompt_positive", "prompt_negative", "batch_size"})
_MODEL_ROLES = ("checkpoint", "diffusion_model", "clip", "vae")

_LORA_PICKER_CONFIG = {
    "model_type": "lora",
    "placeholder": "Select a LoRA...",
    "allow_info_modal": True,
    "strength_min": -2.0,
    "strength_max": 2.0,
    "strength_step": 0.1,
    "strength_default": 1.0,
    "max_items": 6,
}


def _tab_id(label: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower()
    return slug or "tab"


def _resolution_item(width: InputCandidate, height: InputCandidate) -> FieldItem:
    default = f"{width.current_value}x{height.current_value}"
    return FieldItem(
        field_name="resolution",
        field_type="resolution",
        label="Image Resolution",
        default=default,
        config={"options": [default]},
        mappings=[
            FieldMapping(node_id=width.node_id, input_name=width.input_name, transform="split_wh_width"),
            FieldMapping(node_id=height.node_id, input_name=height.input_name, transform="split_wh_height"),
        ],
    )


def _model_item(candidate: InputCandidate) -> FieldItem:
    config = dict(candidate.suggested_config)
    config.setdefault("placeholder", f"Select {candidate.suggested_label}...")
    return FieldItem(
        field_name=candidate.suggested_field_name,
        field_type="model",
        label=candidate.suggested_label,
        default=candidate.current_value,
        config=config,
        mappings=[FieldMapping(node_id=candidate.node_id, input_name=candidate.input_name, transform="strip_model_prefix")],
    )


def _lora_item() -> FieldItem:
    return FieldItem(
        field_name="loras",
        field_type="lora_picker",
        label="LoRAs",
        default=[],
        config=dict(_LORA_PICKER_CONFIG),
        mappings=[],
    )


def _image_item(candidate: InputCandidate) -> FieldItem:
    return FieldItem(
        field_name=candidate.suggested_field_name,
        field_type="image",
        label=candidate.suggested_label,
        default=candidate.current_value,
        config=dict(candidate.suggested_config) or None,
        mappings=[FieldMapping(node_id=candidate.node_id, input_name=candidate.input_name, transform="none")],
    )


def _simple_item(candidate: InputCandidate) -> FieldItem:
    return FieldItem(
        field_name=candidate.suggested_field_name,
        field_type=candidate.suggested_field_type,
        label=candidate.suggested_label,
        default=candidate.current_value,
        config=dict(candidate.suggested_config) or None,
        mappings=[FieldMapping(node_id=candidate.node_id, input_name=candidate.input_name, transform="none")],
    )


def _item_for(candidate: InputCandidate) -> FieldItem:
    if candidate.role in _MODEL_ROLES:
        return _model_item(candidate)
    return _simple_item(candidate)


def _build_tab_items(candidates: List[InputCandidate]) -> List[Item]:
    """Resolution's width/height merge into one field first, then one
    `SectionItem` per distinct `InputCandidate.section` in first-seen order
    (`suggest_fields` enumerates loaders before the sampling cluster
    specifically so "Models" comes out ahead of "Sampling" here without a
    hardcoded priority - see its own comment), then every remaining
    sectionless candidate (image inputs, and anything else with no
    `section`) as a plain field."""
    items: List[Item] = []

    width = next((c for c in candidates if c.role == "resolution_width"), None)
    height = next((c for c in candidates if c.role == "resolution_height"), None)
    if width and height:
        items.append(_resolution_item(width, height))

    section_order: List[str] = []
    by_section: Dict[str, List[InputCandidate]] = {}
    for c in candidates:
        if c.role in ("resolution_width", "resolution_height") or not c.section:
            continue
        if c.section not in by_section:
            by_section[c.section] = []
            section_order.append(c.section)
        by_section[c.section].append(c)
    for section in section_order:
        # Named roles (steps, cfg, sampler, ...) lead a section; a catalog
        # `option` knob (ControlNet strength, FreeU scales, ...) follows them,
        # whichever node the analysis enumerated first.
        ordered = sorted(by_section[section], key=lambda c: c.role == "option")
        items.append(SectionItem(title=section, items=[_item_for(c) for c in ordered]))

    for c in candidates:
        if c.role in ("resolution_width", "resolution_height") or c.section:
            continue
        if c.role == "image":
            items.append(_image_item(c))
        else:
            items.append(_simple_item(c))

    return items


def build_default_form(analysis: AnalyzeResult) -> ImportForm:
    """One tab per distinct `suggested_tab` an obvious candidate carries
    (falling back to "Generation" for a candidate with none, or when the
    workflow has no ComfyUI groups at all), each holding that tab's obvious
    fields - resolution merged into one field, model loaders in a Models section.

    A detected LoRA chain (`analysis.lora_chain`) never becomes a field here:
    turning it into a `lora_picker` is now the wizard's explicit "Convert to
    LoRA picker" action (or the assistant's `lora_picker` op) - see
    `schema.LoraChainSelection` - since only that step knows which nodes the
    admin wants kept fixed. The default form leaves every chain node as an
    individually mappable "obvious" candidate instead."""
    obvious = [c for c in analysis.candidates if c.obvious and c.role not in _FOUNDATIONAL_ROLES and c.role != "lora_slot"]

    tab_order: List[str] = []
    by_tab: Dict[str, List[InputCandidate]] = {}
    for c in obvious:
        label = c.suggested_tab or "Generation"
        if label not in by_tab:
            by_tab[label] = []
            tab_order.append(label)
        by_tab[label].append(c)
    if not tab_order:
        tab_order = ["Generation"]
        by_tab["Generation"] = []

    tabs: List[FormTab] = []
    for label in tab_order:
        items = _build_tab_items(by_tab[label])
        tabs.append(FormTab(id=_tab_id(label), label=label, items=items))

    return ImportForm(tabs=tabs)


# field_type -> history format, for whatever default_history derives from a
# default_form field (see build_default_history). Anything not listed here
# falls back to "as_is".
_HISTORY_FORMAT_BY_FIELD_TYPE = {
    "resolution": "wxh",
    "model": "model_name",
    "lora_picker": "list",
    "slider": "number",
    "stepper": "number",
    "number": "number",
    "seed": "number",
}


def build_default_history(form: ImportForm, analysis: Optional[AnalyzeResult] = None) -> List[HistoryEntry]:
    """Every non-image field in `form`, in display order - "sampling fields
    on, image inputs off" per the contract: an image field is never
    something worth recording to generation history. A field's catalog
    `history` hint (`analysis`'s originating candidate, when given) wins
    over the field-type default below - see `node_catalog.InputSpec.history`."""
    from .schema import iter_field_items

    history_by_field_name: Dict[str, str] = {}
    if analysis is not None:
        for c in analysis.candidates:
            if c.obvious and c.history:
                history_by_field_name.setdefault(c.suggested_field_name, c.history)

    entries: List[HistoryEntry] = []
    for tab in form.tabs:
        for field in iter_field_items(tab.items):
            if field.field_type == "image":
                continue
            format_ = history_by_field_name.get(
                field.field_name, _HISTORY_FORMAT_BY_FIELD_TYPE.get(field.field_type, "as_is")
            )
            entries.append(HistoryEntry(field=field.field_name, label=field.label, format=format_))
    return entries
