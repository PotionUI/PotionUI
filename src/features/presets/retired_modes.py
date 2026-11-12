from typing import Any, Mapping, Optional

KEY = "retired_modes"


def replacement_for(preset_vars: Optional[Mapping[str, Any]], mode: str) -> Optional[str]:
    retired = (preset_vars or {}).get(KEY)
    if not isinstance(retired, Mapping):
        return None
    target = retired.get(mode)
    return target if isinstance(target, str) and target else None


def is_retired(preset_vars: Optional[Mapping[str, Any]], mode: str) -> bool:
    return replacement_for(preset_vars, mode) is not None


def replacement_note(preset_vars: Optional[Mapping[str, Any]], mode: str) -> str:
    target = replacement_for(preset_vars, mode)
    return f" Mode '{mode}' was replaced by '{target}'." if target else ""
