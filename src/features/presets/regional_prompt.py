from typing import Any, Dict, List, Optional

OPTION_KEY = "regional_prompt"


def regional_prompt_modes(preset_vars: Any) -> List[str]:
    option = (preset_vars or {}).get(OPTION_KEY) if isinstance(preset_vars, dict) else None
    modes = option.get("modes") if isinstance(option, dict) else None
    if not isinstance(modes, list):
        return []
    return [mode for mode in modes if isinstance(mode, str)]


def offers_regional_prompt(preset_vars: Any, mode: Optional[str]) -> bool:
    return bool(mode) and mode in regional_prompt_modes(preset_vars)


def regional_prompt_guide(preset_vars: Any) -> Optional[Dict[str, Any]]:
    modes = regional_prompt_modes(preset_vars)
    if not modes:
        return None
    return {
        "modes": modes,
        "how": (
            "Set form_overrides.regional_prompt = {enabled: true, items: [{x, y, w, h, caption}]} "
            "in start_generation, or call set_regional_prompt in chat."
        ),
        "coordinates": (
            "x, y, w, h are fractions 0..1 of the image, origin at the top-left; the boxes follow "
            "the resolution field, so they stay put when the size changes."
        ),
        "text": "Words in double quotes inside a caption are rendered as exact text in that region.",
        "prompt": "The global prompt still describes the whole image; each caption describes only its box.",
    }
