"""Dictionary/regex utility functions used by template processing.

Templates access the ``form``/``preset``/etc. context objects via native `.`
attribute access rather than dict-path helper functions; see docs/presets.md.
"""

import re
from typing import Any, Dict, List


def active_loras(value: Any) -> List[Any]:
    """
    Keep only the LoRA entries that actually affect a generation.

    An entry is dropped when its ``strength`` is exactly zero. Everything else
    is kept:

    - A **negative** strength is meaningful (inverted LoRA), so "not zero" is
      the test, never "greater than zero".
    - A **missing** ``strength`` key means "not specified"; `lora_picker`
      substitutes its ``strength_default`` (1.0) for those, so they are real
      LoRAs.
    - A **non-numeric** strength is malformed rather than zero, so the entry
      survives and stays visible instead of vanishing silently.

    Args:
        value: The LoRA list. Anything that is not a list yields ``[]``.

    Returns:
        The filtered list.
    """
    if not isinstance(value, list):
        return []

    kept: List[Any] = []
    for item in value:
        if not isinstance(item, dict) or 'strength' not in item:
            kept.append(item)
            continue

        try:
            strength = float(item['strength'])
        except (TypeError, ValueError):
            kept.append(item)
            continue

        if strength != 0.0:
            kept.append(item)

    return kept


def regex_search(value: str, pattern: str) -> bool:
    """
    Check if a regex pattern matches a value.

    Args:
        value: The string to search in.
        pattern: The regex pattern to match.

    Returns:
        True if the pattern matches, False otherwise.
    """
    return bool(re.search(pattern, value))


# Sentinel distinguishing "no default given" from "default explicitly set to
# None" - get_speed_profile_value must raise when the caller passes neither,
# but None is otherwise a perfectly legitimate default value to request.
_NO_DEFAULT = object()


def get_speed_profile_value(
    context: Dict[str, Any],
    profile_name: str,
    default: Any = _NO_DEFAULT,
) -> Any:
    """
    Look up a named entry from preset.yml's `speed_profiles:` block.

    Args:
        context: The template context (`preset.speed_profiles` + `preset.name`).
        profile_name: The profile name to look up (e.g. 'draft').
        default: Returned when the profile is missing. If omitted, a missing
            profile raises ``ValueError`` naming both the preset and the
            profile, rather than silently rendering an empty/None profile.

    Returns:
        The profile's dict of overrides (e.g. {'steps': 6, 'guidance': 1.0}).

    Usage:
        get_speed_profile('draft')
        get_speed_profile('experimental', {})   # explicit default suppresses the error
    """
    profiles = context.get('preset', {}).get('speed_profiles') or {}
    if profile_name in profiles:
        return profiles[profile_name]
    if default is not _NO_DEFAULT:
        return default

    preset_name = context.get('preset', {}).get('name') or '<unknown preset>'
    available = sorted(profiles.keys())
    raise ValueError(
        f"speed_profiles: preset '{preset_name}' has no profile named '{profile_name}' "
        f"(declared profiles: {available})"
    )
