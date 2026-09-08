"""Dictionary/regex utility functions used by template processing.

Templates access the ``form``/``preset``/etc. context objects via native `.`
attribute access rather than dict-path helper functions; see docs/presets.md.
"""

import re
from typing import Any, Dict, List

from src.platform.filesystem.model_types import MODEL_DIRECTORY_NAMES

# `clip` is the pre-migration depot directory name for what is now
# `text_encoders` (see `migrations/002_rename_clip_to_text_encoder.py`); it is
# not a current `MODEL_DIRECTORY_NAMES` entry, but a form value saved before
# that migration - or the ComfyUI-vocabulary spelling some importer fields
# still carry - can still hold it, so it must strip the same as any live
# directory name.
_STRIPPABLE_MODEL_DIRS = MODEL_DIRECTORY_NAMES + ('clip',)

# A depot type directory, anchored so it only matches a real path segment
# (start-of-string or right after a "/") - never the middle of an unrelated
# name like "custom_models/loras/x". Matched greedily left-to-right; the
# LAST occurrence in the value is used, so a leading storage root that
# itself contains "models/<dir>/" (an oddly-named root, or a value with the
# prefix repeated) still resolves to the real, innermost type boundary.
_MODEL_DIR_PREFIX_RE = re.compile(
    r'(?:^|/)models/(?:' + '|'.join(re.escape(name) for name in _STRIPPABLE_MODEL_DIRS) + r')/'
)


def strip_model_dir(value: Any) -> str:
    """
    Strip a model picker value down to its path relative to the depot type
    directory, keeping any subdirectories under it.

    Backs the `strip_model_dir` filter, which replaces the old per-preset
    `replace('models/loras/', '')` idiom (see docs/models.md). A value with
    no recognizable `models/<type>/` segment - a bare filename, an already
    backend-native ComfyUI ref, or a value with an unrelated prefix - passes
    through unchanged; `None`/`''` map to `''`.

    Args:
        value: The picker value (a form field's string, or an `item.model`
            entry from a `lora_picker` list).

    Returns:
        The value with its `models/<type>/` prefix removed, or unchanged.
    """
    if not value:
        return ''
    if not isinstance(value, str):
        return value

    matches = list(_MODEL_DIR_PREFIX_RE.finditer(value))
    if not matches:
        return value
    return value[matches[-1].end():]


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
        context: The template context (the internal `_speed_profiles` key
            `PresetProcessor.process` sets, plus `preset.name` for the error
            message - `preset.speed_profiles` itself is not part of the
            documented render context, see docs/presets.md "Speed profiles").
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
    profiles = context.get('_speed_profiles') or {}
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
