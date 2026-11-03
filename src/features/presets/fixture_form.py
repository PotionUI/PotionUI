from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.features.presets.templates import FieldTemplate, PresetTemplate, default_form_name
from src.features.video_director import apply_preset_mode_overlay
from src.features.video_director.normalize import allowed_durations

MODEL_FIELD_TYPES = frozenset({"model", "models"})
LORA_PICKER_FIELD_TYPE = "lora_picker"
MEDIA_FIELD_TYPES = frozenset({"image", "video", "audio", "media", "file"})
MEDIA_EXTENSIONS = {"image": "png", "video": "mp4", "audio": "wav", "media": "png", "file": "bin"}
TEXT_FIELD_TYPES = frozenset({"string", "textbox"})
VIDEO_DIRECTOR_FIXTURE_PROMPT = "a calm lake at sunrise, gentle ripples, soft morning light"


def requires_by_reaction(field: FieldTemplate) -> bool:
    for reaction in field.reactions or []:
        validation = (reaction.get("then") or {}).get("update_validation") or {}
        if validation.get("required") is True:
            return True
    return False


def requires_mask_by_reaction(field: FieldTemplate) -> bool:
    for reaction in field.reactions or []:
        validation = (reaction.get("then") or {}).get("update_validation") or {}
        if validation.get("mask_required") is True:
            return True
    return False


def collect_named_fields(
    fields: List[FieldTemplate],
    form_data: Dict[str, Any],
    reactive_defaults: Optional[Dict[str, Any]] = None,
) -> None:
    for field in fields:
        if field.name and field.default is not None:
            form_data[field.name] = field.default
            if reactive_defaults is not None and field.reactions:
                reactive_defaults[field.name] = field.default
        if isinstance(field.children, list):
            collect_named_fields(field.children, form_data, reactive_defaults)


def leave_reactive_defaults_to_binding(
    form_data: Dict[str, Any], reactive_defaults: Dict[str, Any], overrides: Dict[str, Any]
) -> None:
    for name, default in reactive_defaults.items():
        if name not in overrides and form_data.get(name) == default:
            form_data.pop(name, None)


def inject_unresolvable_defaults(fields: List[FieldTemplate], form_data: Dict[str, Any], tag: str) -> None:
    for field in fields:
        name = field.name
        if name and name not in form_data:
            if field.type in MODEL_FIELD_TYPES:
                form_data[name] = f"/{tag}/models/{name}.safetensors"
            elif field.type == LORA_PICKER_FIELD_TYPE:
                form_data[name] = []
            elif field.type in MEDIA_FIELD_TYPES and field.required:
                placeholder = f"/{tag}/media/{name}.{MEDIA_EXTENSIONS.get(field.type, 'bin')}"
                multi = bool((field.configuration or {}).get("multi"))
                form_data[name] = [placeholder] if multi else placeholder
            if field.type in MEDIA_FIELD_TYPES and requires_mask_by_reaction(field):
                form_data.setdefault(f"{name}_inpaint_mask", f"/{tag}/media/{name}_inpaint_mask.png")
            elif (field.type in TEXT_FIELD_TYPES or field.type == "tags") and (field.required or requires_by_reaction(field)):
                form_data[name] = f"{tag} placeholder text"
        if isinstance(field.children, list):
            inject_unresolvable_defaults(field.children, form_data, tag)


def default_form_fields(preset: PresetTemplate, mode: str) -> List[FieldTemplate]:
    mode_data = preset.modes[mode]
    form_name = default_form_name(mode_data)
    if form_name is None:
        return []
    return next(f for f in mode_data.forms if f.name == form_name).fields


def build_default_form_data(
    preset: PresetTemplate,
    mode: str,
    tag: str,
    reactive_defaults: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[FieldTemplate]]:
    fields = default_form_fields(preset, mode)
    form_data: Dict[str, Any] = {}
    collect_named_fields(fields, form_data, reactive_defaults)
    inject_unresolvable_defaults(fields, form_data, tag)
    return form_data, fields


def fixture_video_director_capabilities(preset: PresetTemplate, mode: str) -> Optional[Dict[str, Any]]:
    capabilities = (getattr(preset, "vars", None) or {}).get("video_director") or {}
    if mode not in (capabilities.get("preset_modes") or []):
        return None
    capabilities = apply_preset_mode_overlay(capabilities, mode)
    if "t2v" not in (capabilities.get("modes") or {}):
        return None
    return capabilities


def fixture_video_director_document(
    preset: PresetTemplate, mode: str, form_data: Dict[str, Any], prompt: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    capabilities = fixture_video_director_capabilities(preset, mode)
    if capabilities is None:
        return None
    limits = capabilities.get("limits") or {}
    durations = allowed_durations(limits)
    seed = form_data.get("seed")
    return {
        "schema_version": 1,
        "mode": "t2v",
        "settings": {
            "fps": form_data.get("fps") or limits.get("default_fps", 24),
            "duration": min(durations) if durations else 1,
            "resolution": form_data.get("resolution") or "",
            "seed": seed if isinstance(seed, int) and not isinstance(seed, bool) else -1,
        },
        "segments": [
            {"id": "setup-check", "prompt": prompt or VIDEO_DIRECTOR_FIXTURE_PROMPT, "negative_prompt": ""}
        ],
        "media": [],
    }
