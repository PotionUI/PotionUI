from typing import Any, Dict, Mapping, Optional

from src.features.presets.templates import PresetTemplate, default_form_name


def _form_declaration(preset_template: PresetTemplate, mode: str, form_name: Optional[str]) -> Optional[Any]:
    mode_data = (preset_template.modes or {}).get(mode)
    if mode_data is None:
        return None
    name = form_name or default_form_name(mode_data)
    for form in mode_data.forms:
        if form.name == name and form.negative_prompt:
            return form.negative_prompt.get("applies_when")
    return None


def negative_applies_when(
    preset_template: PresetTemplate, mode: str, form_name: Optional[str] = None
) -> Optional[Any]:
    declared = _form_declaration(preset_template, mode, form_name)
    if declared is not None:
        return declared
    return (preset_template.negative_prompt or {}).get("applies_when")


def negative_prompt_applies(
    preset_template: PresetTemplate,
    mode: str,
    form_name: Optional[str],
    values: Optional[Mapping[str, Any]],
) -> Optional[bool]:
    when = negative_applies_when(preset_template, mode, form_name)
    if when is None:
        return None
    if isinstance(when, bool):
        return when
    from src.features.forms.binding import condition_matches

    return condition_matches(
        when,
        dict(values or {}),
        context=f"negative_prompt.applies_when (preset '{preset_template.id}' mode '{mode}')",
    )


def negative_prompt_declarations(preset_template: PresetTemplate) -> Optional[Dict[str, Any]]:
    preset_level = (preset_template.negative_prompt or {}).get("applies_when")
    declared_anywhere = preset_level is not None
    modes: Dict[str, Any] = {}
    for mode_name, mode_data in (preset_template.modes or {}).items():
        declared_anywhere = declared_anywhere or any(form.negative_prompt for form in mode_data.forms)
        modes[mode_name] = {
            "default": negative_applies_when(preset_template, mode_name),
            "variants": {
                form.name: negative_applies_when(preset_template, mode_name, form.name)
                for form in mode_data.forms
            },
        }
    if not declared_anywhere:
        return None
    return {"applies_when": preset_level, "modes": modes}
