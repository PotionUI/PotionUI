"""Tool-name-conditional text rendering.

A single primitive shared by the two places that emit tool-referencing copy to
the model: the chat mode system prompt (``ChatModeRegistry.resolve_system_prompt``)
and the tool schema/hint text (``ToolRegistry.get_schemas`` /
``get_tool_hints_text``). Both must be *true for the specific session* — an
instruction to call a tool that is not in the session's allowed set makes small
models flail (confirmed with a 26B local model on ``get_form_state``).

The markup is deliberately tiny — two block forms, nestable:

    {{#if NAME}}...{{/if}}            keep the body iff every NAME is allowed
    {{#ifany NAME1 NAME2}}...{{/ifany}}   keep the body iff any NAME is allowed

Rendering is inside-out, so blocks nest. A reduced-form sentence composes them:

    {{#ifany get_form_state get_active_models}}Call{{#if get_form_state}} get_form_state{{/if}}\
    {{#if get_active_models}}{{#if get_form_state}} and{{/if}} get_active_models{{/if}} first.{{/ifany}}

renders "Call get_form_state and get_active_models first." with both allowed,
"Call get_form_state first." / "Call get_active_models first." with one, and
nothing with neither.

``allowed=None`` means "do not gate" — every block is kept and the markers are
stripped. That keeps plugin prompts and tests that don't thread an allowed set
behaving exactly as before (the markers are simply inert).
"""

import re
from typing import Iterable, Optional

__all__ = ["render_tool_conditionals"]

# Matches the innermost block only: the body may not itself contain a `{{#`
# opener, so a single pass rewrites the deepest blocks and the loop lets the
# next-deepest become innermost in turn. `\1` ties the closer to its opener.
_BLOCK_RE = re.compile(
    r"\{\{#(if|ifany)\s+([a-zA-Z0-9_,\s]+?)\}\}((?:(?!\{\{#).)*?)\{\{/\1\}\}",
    re.DOTALL,
)

_TRAILING_WS_RE = re.compile(r"[ \t]+(\n)")
_EXTRA_BLANKS_RE = re.compile(r"\n{3,}")


def render_tool_conditionals(text: str, allowed: Optional[Iterable[str]]) -> str:
    """Resolve ``{{#if}}`` / ``{{#ifany}}`` blocks against the allowed tool set.

    Args:
        text: Copy that may contain conditional blocks.
        allowed: Tool names available this session. ``None`` keeps every block
            (markers stripped, bodies retained).

    Returns:
        The text with conditionals resolved and marker syntax removed.
    """
    if not text or "{{#" not in text:
        return text

    allowed_set = None if allowed is None else set(allowed)

    def _resolve(match: "re.Match[str]") -> str:
        kind = match.group(1)
        names = [n for n in re.split(r"[,\s]+", match.group(2).strip()) if n]
        body = match.group(3)
        if allowed_set is None:
            keep = True
        elif kind == "if":
            keep = all(n in allowed_set for n in names)
        else:  # ifany
            keep = any(n in allowed_set for n in names)
        return body if keep else ""

    prev = None
    out = text
    while prev != out:
        prev = out
        out = _BLOCK_RE.sub(_resolve, out)

    # Dropped blocks can leave trailing spaces and runs of blank lines behind.
    out = _TRAILING_WS_RE.sub(r"\1", out)
    out = _EXTRA_BLANKS_RE.sub("\n\n", out)
    return out
