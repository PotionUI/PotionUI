import re
from typing import Any, List, Optional

from src.features.presets.templates import ModeTemplate

QUANTITY_CONFIG_KEY = "quantity"

_TEMPLATE_REGION_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_FORM_REF_RE = re.compile(r"\bform\.([A-Za-z_][A-Za-z0-9_]*)")


def _pipe_configuration(pipe: Any) -> dict:
    config = pipe.get("configuration") if isinstance(pipe, dict) else getattr(pipe, "configuration", None)
    return config if isinstance(config, dict) else {}


def mode_quantity_fields(mode_data: Optional[ModeTemplate]) -> List[str]:
    names: List[str] = []
    for pipe in getattr(mode_data, "pipes", None) or []:
        value = _pipe_configuration(pipe).get(QUANTITY_CONFIG_KEY)
        if not isinstance(value, str):
            continue
        for region in _TEMPLATE_REGION_RE.findall(value):
            for name in _FORM_REF_RE.findall(region):
                if name not in names:
                    names.append(name)
    return names


def preset_quantity_fields(preset_template: Any, mode: str) -> List[str]:
    modes = getattr(preset_template, "modes", None) or {}
    return mode_quantity_fields(modes.get(mode))
