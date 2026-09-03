"""Chat context contributor for the ``comfyui-import`` mode.

Renders one compact system block from the import wizard's live state, which
travels every turn under ``context_metadata["comfyui_import"]`` (see the wire
contract documented on `backend.chat.tools`). Registered in `manifest.yml` as
the mode's `context_contributor` and invoked by
`src.features.chat.context_builder.ChatContextBuilder.inject_contributor_block`.

The block has three parts: the workflow summary, the form as arranged so far
(one line per field, showing its mappings), and the still-unmapped workflow
inputs grouped by node - capped so a workflow with hundreds of inputs can't
flood the context; `get_workflow_inputs` is the escape hatch for the rest.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Keeps a wide workflow's block well inside a reasonable context budget - see
# the module docstring; the line cap alone would usually be enough, but a
# pathological single value (a giant embedded string) could still blow the
# budget on its own, hence the two independent caps.
_MAX_UNMAPPED_LINES = 60
_MAX_BLOCK_CHARS = 4000
_MAX_VALUE_CHARS = 60


def _cap(text: Any, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _iter_field_items(items: List[Dict[str, Any]]) -> Any:
    for item in items or []:
        kind = item.get("kind")
        if kind == "field":
            yield item
        elif kind in ("row", "group", "section"):
            yield from _iter_field_items(item.get("items") or [])


def _render_field_line(field: Dict[str, Any]) -> str:
    name = field.get("field_name", "?")
    ftype = field.get("field_type", "?")
    mappings = field.get("mappings") or []
    if not mappings:
        return f"  {name} ({ftype}): unmapped"
    targets = ", ".join(
        f"{m.get('node_id')}.inputs.{m.get('input_name')}"
        + (f" [{m['transform']}]" if m.get("transform") and m["transform"] != "none" else "")
        for m in mappings
    )
    return f"  {name} ({ftype}) ← {targets}"


def _render_tabs(tabs: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for tab in tabs or []:
        fields = list(_iter_field_items(tab.get("items") or []))
        lines.append(f"Tab \"{tab.get('label', tab.get('id', '?'))}\" ({len(fields)} field(s)):")
        if not fields:
            lines.append("  (empty)")
        lines.extend(_render_field_line(f) for f in fields)
    return lines


def _mapped_keys(mapped: List[Dict[str, Any]]) -> set:
    return {(m.get("node_id"), m.get("input_name")) for m in mapped or []}


def _render_unmapped(candidates: List[Dict[str, Any]], mapped_keys: set) -> List[str]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for c in candidates or []:
        if c.get("locked"):
            continue
        if (c.get("node_id"), c.get("input_name")) in mapped_keys:
            continue
        node_id = c.get("node_id", "?")
        if node_id not in groups:
            groups[node_id] = []
            order.append(node_id)
        groups[node_id].append(c)

    total_candidates = sum(len(v) for v in groups.values())
    lines: List[str] = []
    shown_candidates = 0
    for node_id in order:
        if len(lines) >= _MAX_UNMAPPED_LINES:
            break
        entries = groups[node_id]
        first = entries[0]
        parts = ", ".join(
            f"{e.get('input_name')}={_cap(e.get('current_value'), _MAX_VALUE_CHARS)} ({e.get('value_type')})"
            for e in entries
        )
        lines.append(f"  {node_id} {first.get('class_type', '?')} \"{first.get('node_title', '')}\": {parts}")
        shown_candidates += len(entries)

    remaining = total_candidates - shown_candidates
    if remaining > 0:
        lines.append(f"  … {remaining} more, use get_workflow_inputs")
    return lines


def build_import_context(
    context_metadata: Dict[str, Any],
    session: Any,
    user_id: str,
) -> Optional[str]:
    """Render the comfyui-import mode's per-turn system context block.

    Returns None when the wizard hasn't attached ``comfyui_import`` state -
    the caller skips injecting anything for that turn.
    """
    wiz = (context_metadata or {}).get("comfyui_import")
    if not isinstance(wiz, dict):
        return None

    form = wiz.get("form") or {}
    tabs = form.get("tabs") or []
    mapped = wiz.get("mapped") or []
    candidates = wiz.get("candidates") or []

    lines: List[str] = [
        f"COMFYUI IMPORT WIZARD — workflow \"{wiz.get('workflow_name', '?')}\" "
        f"({wiz.get('format', '?')} format, {wiz.get('node_count', 0)} nodes)",
        "",
        "Form so far:",
    ]
    lines.extend(_render_tabs(tabs))
    lines.append("")
    lines.append("Unmapped workflow inputs (excluding locked prompt inputs):")
    lines.extend(_render_unmapped(candidates, _mapped_keys(mapped)) or ["  (none)"])

    return _cap("\n".join(lines), _MAX_BLOCK_CHARS)
