"""Test-only helpers for building a `form`/`history` payload from a
`suggest.suggest_fields` analysis - the test suite's analogue of picking
"choices" under the old fields[] model, built directly on top of
`defaults.py`'s own per-role item builders so a test exercises the exact
same field/mapping shapes `build_default_form` would produce, just for an
arbitrary role set (including non-"obvious" ones like sampler/scheduler/
denoise, which `build_default_form` itself never includes).
"""

from __future__ import annotations

from typing import Iterable, List, Tuple

from backend.preset_import.defaults import (
    _audio_item,
    _image_item,
    _lora_item,
    _model_item,
    _resolution_item,
    _simple_item,
    _video_item,
)
from backend.preset_import.schema import FormTab, HistoryEntry, ImportForm

_MODEL_ROLES = ("checkpoint", "diffusion_model", "clip", "vae")
_MEDIA_ROLES = {"image": _image_item, "video": _video_item, "audio": _audio_item}
_SPECIAL_ROLES = frozenset({"resolution_width", "resolution_height", *_MODEL_ROLES, "lora_slot", *_MEDIA_ROLES})


def form_from_roles(analysis, roles: Iterable[str], *, tab_id: str = "generation", tab_label: str = "Generation") -> ImportForm:
    """One tab holding one field per candidate whose `role` is in `roles` -
    resolution's width/height merge into a single field, model loaders each
    get their own field, and any lora_slot candidates collapse into one
    `loras` field, exactly like `defaults.build_default_form`."""
    wanted = set(roles)
    by_role = {}
    for c in analysis.candidates:
        if c.role in wanted:
            by_role.setdefault(c.role, []).append(c)

    items = []
    width = by_role.get("resolution_width")
    height = by_role.get("resolution_height")
    if width and height:
        items.append(_resolution_item(width[0], height[0]))

    for role in _MODEL_ROLES:
        for c in by_role.get(role, []):
            items.append(_model_item(c))

    if by_role.get("lora_slot"):
        items.append(_lora_item())

    for role, item_fn in _MEDIA_ROLES.items():
        for c in by_role.get(role, []):
            items.append(item_fn(c))

    for role, candidates in by_role.items():
        if role in _SPECIAL_ROLES:
            continue
        for c in candidates:
            items.append(_simple_item(c))

    return ImportForm(tabs=[FormTab(id=tab_id, label=tab_label, items=items)])


def multi_tab_form(tabs: List[Tuple[str, str, "ImportForm"]]) -> ImportForm:
    """Merge several single-tab `ImportForm`s (as `form_from_roles` builds)
    into one multi-tab form, re-labelling each tab - used where a test needs
    more than one tab (e.g. asserting a dedicated LoRA/Advanced tab file)."""
    merged_tabs = []
    for tab_id, tab_label, form in tabs:
        for tab in form.tabs:
            merged_tabs.append(FormTab(id=tab_id, label=tab_label, items=tab.items))
    return ImportForm(tabs=merged_tabs)


_TRANSFORM_BY_ROLE = {
    "checkpoint": "strip_model_prefix",
    "diffusion_model": "strip_model_prefix",
    "clip": "strip_model_prefix",
    "vae": "strip_model_prefix",
    "seed": "seed",
}


def raw_form_dict_for_roles(analysis_dict: dict, roles: Iterable[str], *, tab_id: str = "generation", tab_label: str = "Generation") -> dict:
    """The plain-dict analogue of `form_from_roles`, for tests driving the
    HTTP routes directly (`api.analyze_workflow`/`api.import_workflow`),
    where the analysis comes back as JSON-shaped dicts (`AnalyzeResult
    .to_dict()`'s candidates), not the dataclasses `suggest_fields` returns.
    One field per matching candidate - no resolution/model/lora grouping,
    since the callers below only ever pick a single simple role."""
    wanted = set(roles)
    items = []
    for c in analysis_dict["candidates"]:
        if c["role"] not in wanted:
            continue
        items.append(
            {
                "kind": "field",
                "field_name": c["suggested_field_name"],
                "field_type": c["suggested_field_type"],
                "label": c["suggested_label"],
                "mappings": [
                    {
                        "node_id": c["node_id"],
                        "input_name": c["input_name"],
                        "transform": _TRANSFORM_BY_ROLE.get(c["role"], "none"),
                    }
                ],
            }
        )
    return {"tabs": [{"id": tab_id, "label": tab_label, "items": items}]}


def history_for(form: ImportForm, fields: Iterable[str]) -> List[HistoryEntry]:
    """History entries for a fixed set of field names already present in
    `form`, one `as_is` entry each - most ported tests don't assert on
    param_emitter output, so an empty list is usually simpler; this is for
    the handful that do."""
    return [HistoryEntry(field=name, label=name, format="as_is") for name in fields]
