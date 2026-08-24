"""Plain-language rendering of the generate tab's typed prompt variables.

The chat form-state snapshot carries a compact ``variables`` list alongside
``form_data`` (see the frontend ``buildVariablesSnapshot`` helper). A prompt
variable is a named ``${...}`` placeholder the user can reuse across their
prompt; it is either free ``text`` or a ``choice`` of options resolved per a
mode (shuffle once per generation / pin one option / re-roll per image).

Both the ``@form`` resource dump and the ``get_form_state`` tool render this
list into short human sentences the chat model reads — e.g.::

    mood: one of noir, sunlit — shuffles each generation; last roll: sunlit

This module is the single pure renderer shared by both surfaces. It is
defensive about the untrusted client shape and applies the same
count/length caps FormResourceProvider uses so a padded snapshot can never
bloat the prompt.
"""

from typing import Any, List, Optional

# Caps mirror FormResourceProvider's discipline (small-model payload budget).
_MAX_VARIABLES = 24
_MAX_OPTIONS = 12
_MAX_VALUE_CHARS = 80
_MAX_NAME_CHARS = 60


def _clip(text: str, limit: int) -> str:
    return text[:limit] + "…" if len(text) > limit else text


def _valid_options(raw: Any) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    for opt in raw:
        if not isinstance(opt, (str, int, float)):
            continue
        text = str(opt).strip()
        if text:
            out.append(text)
    return out


def _mode_phrase(mode: Any, options: List[str], pinned_index: Any) -> str:
    if mode == "pin":
        if isinstance(pinned_index, int) and 0 <= pinned_index < len(options):
            return f"pinned to {options[pinned_index]}"
        return "pinned"
    if mode == "per-image":
        return "re-rolls independently per image"
    # 'shuffle' is the default (and the value for any unknown/missing mode).
    return "shuffles each generation"


def _render_choice(name: str, var: dict) -> Optional[str]:
    options = _valid_options(var.get("options"))
    if not options:
        return None
    shown = [_clip(o, _MAX_VALUE_CHARS) for o in options[:_MAX_OPTIONS]]
    listing = ", ".join(shown)
    if len(options) > _MAX_OPTIONS:
        listing += ", …"
    line = f"{name}: one of {listing} — {_mode_phrase(var.get('mode'), options, var.get('pinnedIndex'))}"
    roll = var.get("lastRoll")
    if isinstance(roll, (str, int, float)) and str(roll).strip():
        line += f"; last roll: {_clip(str(roll).strip(), _MAX_VALUE_CHARS)}"
    return line


def _render_text(name: str, var: dict) -> str:
    value = var.get("value")
    if not isinstance(value, (str, int, float)) or str(value).strip() == "":
        return f"{name}: free text (empty)"
    return f"{name}: {_clip(str(value).strip(), _MAX_VALUE_CHARS)}"


def render_prompt_variable_lines(variables: Any) -> List[str]:
    """One plain-language sentence per prompt variable, capped and defensive.

    Returns an empty list when ``variables`` is absent, malformed, or contains
    no renderable entries — callers treat that as "no prompt variables".
    """
    if not isinstance(variables, (list, tuple)):
        return []
    lines: List[str] = []
    for var in variables[:_MAX_VARIABLES]:
        if not isinstance(var, dict):
            continue
        raw_name = var.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        name = _clip(raw_name.strip(), _MAX_NAME_CHARS)
        if var.get("type") == "choice":
            line = _render_choice(name, var)
            if line is None:
                continue
        else:
            line = _render_text(name, var)
        lines.append(line)
    return lines
