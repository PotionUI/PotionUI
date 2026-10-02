from typing import Any, Dict, Optional

NUMERIC_TYPES = ("slider", "stepper", "number", "integer")
OPTION_TYPES = ("select", "resolution", "sampler", "schedule", "carousel")
MODEL_TYPES = ("model", "models")


def option_values(spec: Dict[str, Any]) -> Optional[list]:
    options = spec.get("options")
    if not isinstance(options, list):
        return None
    return [item.get("value") if isinstance(item, dict) else item for item in options]


def configuration(spec: Dict[str, Any]) -> Dict[str, Any]:
    configuration = spec.get("configuration")
    return configuration if isinstance(configuration, dict) else {}


def numeric_bounds(spec: Dict[str, Any]) -> Dict[str, Any]:
    config = configuration(spec)
    return {
        "min": spec.get("minimum", config.get("min")),
        "max": spec.get("maximum", config.get("max")),
        "step": spec.get("step", config.get("step")),
    }


def field_signature(spec: Dict[str, Any]) -> Dict[str, Any]:
    field_type = spec.get("type")
    signature: Dict[str, Any] = {"type": field_type}
    config = configuration(spec)
    if field_type in NUMERIC_TYPES:
        signature.update(numeric_bounds(spec))
    elif field_type in OPTION_TYPES or field_type == "checkbox_group":
        signature["options"] = option_values(spec)
    elif field_type in MODEL_TYPES:
        signature["model_type"] = config.get("model_type")
        signature["filter_tags"] = config.get("filter_tags")
    elif field_type == "lora_picker":
        signature["model_type"] = config.get("model_type", "lora")
        signature["max_items"] = config.get("max_items", 6)
        signature["filter_tags"] = config.get("filter_tags")
    return signature


COMPANION_SUFFIXES = ("_inpaint_mask", "_tagFilters")


def owning_field(key: str, declared: Dict[str, Any]) -> Optional[str]:
    if key in declared:
        return key
    for name in declared:
        if key.startswith(f"{name}__") or any(key == f"{name}{suffix}" for suffix in COMPANION_SUFFIXES):
            return name
    return None


MEDIA_TYPES = ("image", "video", "audio", "media")

COMPANION_OWNER_TYPES = {
    "_inpaint_mask": MEDIA_TYPES,
    "__origin": MEDIA_TYPES,
    "_tagFilters": ("model", "models", "lora_picker"),
}


def companion_accepted(key: str, owner: str, owner_type: Optional[str]) -> bool:
    if key == owner:
        return True
    suffix = key[len(owner):]
    return owner_type in COMPANION_OWNER_TYPES.get(suffix, ())
