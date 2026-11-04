from typing import Any, Dict, Iterable, List, Mapping

from src.features.presets.templates import default_form_name

Catalog = Dict[str, Dict[str, Any]]


def director_shapes(director: Mapping[str, Any], preset_mode: str) -> List[str]:
    modes = dict(director.get("modes") or {})
    override = (director.get("preset_mode_overrides") or {}).get(preset_mode)
    if isinstance(override, Mapping) and isinstance(override.get("modes"), Mapping):
        for shape, value in override["modes"].items():
            modes[shape] = value
    return sorted(shape for shape, value in modes.items() if value is not None)


def _describe_fields(fields: Iterable[Any], out: Dict[str, Dict[str, Any]]) -> None:
    for form_field in fields or []:
        if form_field.name and form_field.name not in out:
            out[form_field.name] = {
                "type": form_field.type,
                "required": bool(form_field.required),
                "multi": bool((form_field.configuration or {}).get("multi")),
            }
        if isinstance(form_field.children, list):
            _describe_fields(form_field.children, out)


def _mode_forms(mode: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    forms: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for form in mode.forms:
        described: Dict[str, Dict[str, Any]] = {}
        _describe_fields(form.fields, described)
        forms[form.name] = described
    return forms


def catalog_entry(template: Any) -> Dict[str, Any]:
    director = (template.vars or {}).get("video_director") or {}
    preset_modes = list(director.get("preset_modes") or [])
    return {
        "name": template.name,
        "director_modes": preset_modes,
        "director_shapes": {preset_mode: director_shapes(director, preset_mode) for preset_mode in preset_modes},
        "modes": {
            mode_name: {"default_form": default_form_name(mode), "forms": _mode_forms(mode)}
            for mode_name, mode in (template.modes or {}).items()
        },
    }


def installed_catalog(presets: Any, preset_ids: Iterable[str]) -> Catalog:
    catalog: Catalog = {}
    for preset_id in sorted({preset_id for preset_id in preset_ids if preset_id}):
        template = presets.find_preset_by_id(preset_id)
        if template is not None:
            catalog[preset_id] = catalog_entry(template)
    return catalog
