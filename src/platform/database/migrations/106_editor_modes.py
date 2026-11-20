import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.platform.database.database import db

_NAME = "106_editor_modes"
_PREFIX = "editor:"
_FALLBACKS = {"remove_object": "edit_region"}
_ROOT = Path(__file__).resolve().parents[4]

Modes = Dict[str, Tuple[Optional[str], Optional[str]]]


def _preset_roots() -> List[Path]:
    base = _ROOT / "content" / "presets"
    return [base / "marketplace", base / "local"]


def _read(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _editor_modes() -> Dict[str, Modes]:
    found: Dict[str, Modes] = {}
    for root in _preset_roots():
        if not root.is_dir():
            continue
        for manifest in sorted(root.rglob("preset.yml")):
            document = _read(manifest)
            preset_id = document.get("id") if isinstance(document, dict) else None
            directory = manifest.parent / "editor_modes"
            if not preset_id or not directory.is_dir():
                continue
            modes = found.setdefault(str(preset_id), {})
            for entry in sorted(directory.glob("*/editor_mode.yml")):
                body = _read(entry)
                if isinstance(body, dict):
                    modes.setdefault(entry.parent.name, (body.get("from"), body.get("task")))
    return found


def _target(modes: Modes, mode: str, task: Optional[str]) -> Optional[str]:
    candidates = [name for name, (source, _) in modes.items() if name == mode or source == mode]
    if len(candidates) == 1:
        return candidates[0]
    for wanted in ({task}, {_FALLBACKS.get(task)}):
        narrowed = [name for name in candidates if modes[name][1] in wanted - {None}]
        if len(narrowed) == 1:
            return narrowed[0]
    return None


def _sessions(cursor, available: Dict[str, Modes]) -> Tuple[int, int]:
    cursor.execute("SELECT id, preset_id, data FROM sessions WHERE kind = 'editor'")
    changed = left = 0
    for session_id, preset_id, raw in [tuple(row) for row in cursor.fetchall()]:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        modes = available.get(preset_id, {})
        renamed: Dict[str, Any] = {}
        touched = False
        for key, value in data.items():
            if ":" in key:
                renamed[key] = value
                continue
            name = _target(modes, key, None)
            if name is None:
                renamed[key] = value
                left += 1
            elif f"{_PREFIX}{name}" in data:
                renamed[key] = value
                left += 1
            else:
                renamed[f"{_PREFIX}{name}"] = value
                touched = True
                changed += 1
        if touched:
            cursor.execute("UPDATE sessions SET data = ? WHERE id = ?", (json.dumps(renamed), session_id))
    return changed, left


def _defaults(cursor, available: Dict[str, Modes]) -> Tuple[int, int]:
    cursor.execute("SELECT user_id, task, preset_id, mode FROM editor_task_defaults WHERE instr(mode, ':') = 0")
    changed = left = 0
    for user_id, task, preset_id, mode in [tuple(row) for row in cursor.fetchall()]:
        name = _target(available.get(preset_id, {}), mode, task)
        if name is None:
            left += 1
            continue
        cursor.execute(
            "UPDATE editor_task_defaults SET mode = ?, variant = NULL WHERE user_id = ? AND task = ?",
            (f"{_PREFIX}{name}", user_id, task),
        )
        changed += 1
    return changed, left


def up():
    available = _editor_modes()
    with db.get_cursor() as cursor:
        sessions_changed, sessions_left = _sessions(cursor, available)
        defaults_changed, defaults_left = _defaults(cursor, available)

    print(
        f"Migration {_NAME}: sessions keys prefixed {sessions_changed}, left {sessions_left}; "
        f"defaults prefixed {defaults_changed}, left {defaults_left}"
    )
