"""Tool for creating, updating, and removing the generate tab's prompt variables."""

import difflib
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.tab_drafts.errors import TabDraftError
from src.features.llm.tools.builtin.utils import parse_page
from src.features.llm.tools.operation_args import collapse_repeats, operations_from
from src.platform.resources.prompt_variables import (
    VALID_MODES,
    dependency_prefix,
    format_option,
    name_error,
    normalize_option,
    number_grid,
    number_grid_error,
    number_pinned_text,
    option_texts,
    render_prompt_variable_lines,
    valid_options,
    validate_condition,
    variable_dependencies,
)


def _describe(var: Optional[Dict[str, Any]]) -> str:
    """Short plain-language description of a variable's current value — no
    name prefix, since the caller already shows `${name}` as the field."""
    if not var:
        return "not set"
    if var.get("type") == "number":
        lines = render_prompt_variable_lines([{**var, "name": "n", "lastRoll": None}])
        return lines[0].split(": ", 1)[1] if lines else "number with an invalid range"
    if var.get("type") == "choice":
        options = valid_options(var.get("options"))
        if not options:
            return "choice with no options"
        mode = var.get("mode") or "shuffle"
        texts = option_texts(options)
        if mode == "pin":
            idx = var.get("pinnedIndex")
            if isinstance(idx, int) and 0 <= idx < len(texts):
                return f"pinned to {texts[idx]}"
            return "pinned (no option selected)"
        prefix = dependency_prefix(variable_dependencies(var))
        listing = ", ".join(format_option(o) for o in options)
        if mode == "per-image":
            return f"{prefix}one of {listing} — re-rolls per image"
        return f"{prefix}one of {listing} — shuffles each generation"
    value = var.get("value")
    if not isinstance(value, str) or not value.strip():
        return "empty text"
    return value.strip()


def _validate_condition(
    name: str,
    when: Dict[str, Any],
    working: Dict[str, Dict[str, Any]],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    normalized, error = validate_condition(name, when, working)
    if error:
        errors.append(error)
        return None
    return normalized


NO_TAB_ERROR = (
    "No Generate tab is selected. Pass tab_id; list_tab_drafts shows the user's tabs."
)
NO_TAB_OPEN_MESSAGE = "No Generate tab is open — open the Generate page to read or change prompt variables."
NO_DRAFT_STORE_ERROR = "Tab drafts are not available."
TAB_ID_PARAMETER = {"type": "string", "description": "MCP only; ignored in chat."}
NO_OPERATIONS_ERROR = "No operations provided. Specify at least one variable change."
CALL_EXAMPLE = (
    '<tool_call>{"name": "manage_prompt_variables", "arguments": {"operations": '
    '[{"op": "set", "name": "mood", "type": "choice", "options": ["noir", "sunlit"], "mode": "shuffle"}, '
    '{"op": "set", "name": "palette", "type": "text", "value": "teal and orange"}, '
    '{"op": "remove", "name": "old_style"}]}}</tool_call>'
)


def _tab_id(context: ToolContext, kwargs: Dict[str, Any]) -> Optional[str]:
    if context.chat_session:
        form_state = context.session_metadata.get("form_state")
        candidate = form_state.get("tab_id") if isinstance(form_state, dict) else None
    else:
        candidate = kwargs.get("tab_id")
    return candidate.strip() if isinstance(candidate, str) and candidate.strip() else None


def _draft_error_text(exc: TabDraftError) -> str:
    details = getattr(exc, "operations", None) or []
    reasons = [reason for item in details for reason in item.get("errors", [])]
    return "; ".join(reasons) if reasons else str(exc)


def active_draft_variables(context: ToolContext) -> Dict[str, Dict[str, Any]]:
    _tab, variables, _error = _load_variables(context, {})
    return variables


def tab_variables(
    context: ToolContext, kwargs: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Dict[str, Any]]], Optional[str]]:
    _tab, variables, error = _load_variables(context, kwargs)
    return (None, error) if error else (variables, None)


def _load_variables(context: ToolContext, kwargs: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Dict[str, Any]], Optional[str]]:
    if context.tab_drafts is None:
        return None, {}, NO_DRAFT_STORE_ERROR
    tab_id = _tab_id(context, kwargs)
    if tab_id is None:
        return None, {}, NO_TAB_OPEN_MESSAGE if context.chat_session else NO_TAB_ERROR
    try:
        return tab_id, context.tab_drafts.variables(context.user_id, tab_id), None
    except TabDraftError as exc:
        return tab_id, {}, str(exc)


def _build_choice_var(
    op: Dict[str, Any],
    name: str,
    working: Dict[str, Dict[str, Any]],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    raw_options = op.get("options")
    if not isinstance(raw_options, (list, tuple)):
        errors.append(f"'{name}': a choice variable needs at least one non-empty option.")
        return None

    options: List[Any] = []
    texts: List[str] = []
    for raw_opt in raw_options:
        normalized = normalize_option(raw_opt)
        if normalized is None:
            continue
        when = normalized["when"]
        if when is not None:
            when = _validate_condition(name, when, working, errors)
            if when is None:
                return None
        texts.append(normalized["text"])
        options.append({"text": normalized["text"], "when": when} if when else normalized["text"])

    if not options:
        errors.append(f"'{name}': a choice variable needs at least one non-empty option.")
        return None

    mode = op.get("mode") or "shuffle"
    if mode not in VALID_MODES:
        errors.append(f"'{name}': invalid mode '{mode}'. Use shuffle, pin, or per-image.")
        return None
    pinned_index = op.get("pinned_index")
    if pinned_index is not None and (
        not isinstance(pinned_index, int) or not (0 <= pinned_index < len(texts))
    ):
        errors.append(f"'{name}': pinned_index must be a valid index into options.")
        return None
    return {"type": "choice", "options": options, "mode": mode, "pinnedIndex": pinned_index}


def _wire_number(value: Decimal) -> Any:
    return int(value) if value == value.to_integral_value() else float(value)


def _build_number_var(
    op: Dict[str, Any],
    name: str,
    existing: Optional[Dict[str, Any]],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    inherited = existing if isinstance(existing, dict) and existing.get("type") == "number" else {}
    definition = {
        field: op[field] if field in op else inherited.get(field)
        for field in ("min", "max", "step", "value")
    }
    mode = op.get("mode") or inherited.get("mode") or "shuffle"
    if mode not in VALID_MODES:
        errors.append(f"'{name}': invalid mode '{mode}'. Use shuffle, pin, or per-image.")
        return None
    error = number_grid_error(name, definition)
    if error:
        errors.append(error)
        return None
    grid = number_grid(definition)
    return {
        "type": "number",
        "mode": mode,
        "min": _wire_number(grid.minimum),
        "max": _wire_number(grid.maximum),
        "step": _wire_number(grid.step),
        "value": _wire_number(Decimal(number_pinned_text(definition, grid))),
    }


def _validate_operations(
    operations: List[Dict[str, Any]],
    existing_by_name: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    working = dict(existing_by_name)
    validated_ops: List[Dict[str, Any]] = []
    preview_rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    for op in operations:
        if not isinstance(op, dict):
            errors.append("Operation must be an object.")
            continue

        op_type = op.get("op")
        raw_name = op.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        reason = op.get("reason") or ""

        if not name:
            errors.append("Empty variable name in operation.")
            continue
        name_err = name_error(name)
        if name_err:
            errors.append(name_err)
            continue

        if op_type == "remove":
            if name not in working:
                errors.append(f"Unknown variable '{name}' — cannot remove.")
                continue
            old_desc = _describe(working[name])
            del working[name]
            validated_ops.append({"op": "remove", "name": name})
            row = {"field_name": f"${{{name}}}", "old_value": old_desc, "new_value": "(removed)"}
            if reason:
                row["reason"] = reason
            preview_rows.append(row)
            continue

        if op_type != "set":
            errors.append(f"Unknown operation '{op_type}' for '{name}'. Use set or remove.")
            continue

        var_type = op.get("type") or (working.get(name) or {}).get("type") or "text"
        if var_type not in ("text", "choice", "number"):
            errors.append(f"'{name}': invalid type '{var_type}'. Use text, choice or number.")
            continue

        if var_type == "text":
            value = op.get("value")
            if value is None:
                value = ""
            if not isinstance(value, str):
                errors.append(f"'{name}': a text variable's value must be a string.")
                continue
            new_var: Dict[str, Any] = {"type": "text", "value": value}
        elif var_type == "number":
            built = _build_number_var(op, name, working.get(name), errors)
            if built is None:
                continue
            new_var = built
        else:
            built = _build_choice_var(op, name, working, errors)
            if built is None:
                continue
            new_var = built

        previous = working.get(name)
        old_desc = _describe(previous)
        working[name] = new_var
        new_desc = _describe(new_var)

        wire_op: Dict[str, Any] = {"op": "set", "name": name, "type": new_var["type"]}
        if new_var["type"] == "text":
            wire_op["value"] = new_var["value"]
        elif new_var["type"] == "number":
            wire_op.update({key: new_var[key] for key in ("mode", "min", "max", "step", "value")})
        else:
            wire_op["options"] = new_var["options"]
            wire_op["mode"] = new_var["mode"]
            wire_op["pinned_index"] = new_var["pinnedIndex"]
        if isinstance(previous, dict) and previous.get("linked") is True and previous.get("type") == new_var["type"]:
            wire_op["linked"] = True
        validated_ops.append(wire_op)

        row = {"field_name": f"${{{name}}}", "old_value": old_desc, "new_value": new_desc}
        if reason:
            row["reason"] = reason
        preview_rows.append(row)

    return validated_ops, preview_rows, collapse_repeats(errors)


def _applied_summary(rows: List[Dict[str, Any]]) -> str:
    lines = [f"{row['field_name']}: {row['old_value']} -> {row['new_value']}" for row in rows]
    noun = "change" if len(rows) == 1 else "changes"
    return f"Applied {len(rows)} variable {noun}. " + "; ".join(lines)


class ManagePromptVariablesTool(BaseTool):
    """Creates, updates, and removes the generate tab's ${name} prompt variables."""

    modes = ["generation"]
    icon = "braces"

    name = "manage_prompt_variables"
    mcp = True
    group = "Form & segments"
    user_description = "Creates or changes the ${name} prompt variables on your generation form."
    requires_approval = True

    @property
    def description(self) -> str:
        return (
            "Create, update, or remove ${name} prompt variables on the user's generation "
            "form. Each operation is 'set' (create a new variable, or replace an existing "
            "one's value/options) or 'remove'. A 'set' with type 'text' takes a plain "
            "value; type 'choice' takes an options list and a mode — 'shuffle' rolls one "
            "option per generation, 'pin' always uses pinned_index, 'per-image' re-rolls "
            "independently per image. A choice option can be conditioned on another choice "
            "variable by replacing its string with {\"text\": ..., \"when\": {\"var\": "
            "\"other_var\", \"values\": [...]}}; it is then eligible only when other_var last "
            "resolved to one of those values, and an option without 'when' is always "
            "eligible. 'when.var' must already be a choice variable on this tab (or set "
            "earlier in this same operations list), cannot be the variable being defined, and "
            "cannot depend on it in turn (no cycles); 'when.values' must match that "
            "variable's option texts exactly. Example: {\"op\": \"set\", \"name\": \"dance\", "
            "\"type\": \"choice\", \"options\": [{\"text\": \"breaking\", \"when\": "
            "{\"var\": \"music\", \"values\": [\"hip hop\"]}}, \"salsa\"]}. "
            "type 'number' takes min, max, step (default 1, up to 200 values) and the same "
            "modes ('pin' uses value); omitted fields keep their current value. "
            "The user must approve before changes are applied."
            "{{#if get_prompt_variables}} Call get_prompt_variables first to read a "
            "variable's exact options and values.{{/if}}"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tab_id": TAB_ID_PARAMETER,
                "operations": {
                    "type": "array",
                    "description": "List of variable operations to propose",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": ["set", "remove"],
                                "description": "'set' creates or replaces the variable, 'remove' deletes it",
                            },
                            "name": {
                                "type": "string",
                                "description": "The variable name, without ${} or $",
                            },
                            "type": {
                                "type": "string",
                                "enum": ["text", "choice", "number"],
                                "description": (
                                    "Required for 'set' on a new variable; defaults to the "
                                    "existing variable's type when updating one."
                                ),
                            },
                            "value": {
                                "oneOf": [{"type": "string"}, {"type": "number"}],
                                "description": "Text value, or the pinned number for type 'number'",
                            },
                            "min": {"type": "number"},
                            "max": {"type": "number"},
                            "step": {"type": "number"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {
                                            "type": "object",
                                            "properties": {
                                                "text": {"type": "string"},
                                                "when": {
                                                    "type": "object",
                                                    "properties": {
                                                        "var": {
                                                            "type": "string",
                                                            "description": "Name of another choice variable this option depends on",
                                                        },
                                                        "values": {
                                                            "type": "array",
                                                            "items": {"type": "string"},
                                                            "description": "Exact option texts of that variable this option is eligible for",
                                                        },
                                                    },
                                                    "required": ["var", "values"],
                                                },
                                            },
                                            "required": ["text"],
                                        },
                                    ]
                                },
                                "description": (
                                    "Option texts, for a 'set' with type 'choice'. Each entry is "
                                    "a plain string, or {text, when: {var, values}} to make it "
                                    "conditionally eligible."
                                ),
                            },
                            "mode": {
                                "type": "string",
                                "enum": ["shuffle", "pin", "per-image"],
                                "description": "Choice or number resolution; defaults to 'shuffle'",
                            },
                            "pinned_index": {
                                "type": "integer",
                                "description": "Index into options to pin, when mode is 'pin'",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Optional explanation for why this change is proposed",
                            },
                        },
                        "required": ["op", "name"],
                    },
                }
            },
            "required": ["operations"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        operations, problem = operations_from(kwargs.get("operations"), CALL_EXAMPLE, NO_OPERATIONS_ERROR)
        if problem:
            return ToolResult(success=False, data="", error=problem)

        _tab, existing, load_error = _load_variables(context, kwargs)
        if load_error:
            return ToolResult(success=False, data="", error=load_error)

        _validated, preview_rows, errors = _validate_operations(operations, existing)

        if errors and not preview_rows:
            return ToolResult(success=False, data="", error="; ".join(errors))

        result = {
            "status": "pending_approval",
            "tab_id": _tab,
            "proposed_changes": preview_rows,
            "change_count": len(preview_rows),
        }
        if errors:
            result["warnings"] = errors

        return ToolResult(success=True, data=json.dumps(result))

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        operations, problem = operations_from(kwargs.get("operations"), CALL_EXAMPLE, NO_OPERATIONS_ERROR)
        if problem:
            return ToolResult(success=False, data="", error=problem)

        tab_id, existing, load_error = _load_variables(context, kwargs)
        if load_error:
            return ToolResult(success=False, data="", error=load_error)
        validated_ops, applied_rows, errors = _validate_operations(operations, existing)

        if errors and not validated_ops:
            return ToolResult(success=False, data="", error="; ".join(errors))

        try:
            draft = context.tab_drafts.apply_operations(context.user_id, tab_id, validated_ops)
        except TabDraftError as exc:
            return ToolResult(success=False, data="", error=_draft_error_text(exc))
        context.tab_drafts.announce(draft)

        payload = {
            "status": "applied",
            "tab_id": tab_id,
            "rev": draft.rev,
            "applied_count": len(validated_ops),
            "applied_changes": applied_rows,
            "summary": _applied_summary(applied_rows),
        }
        if errors:
            payload["rejected_operations"] = errors

        return ToolResult(success=True, data=json.dumps(payload))


DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100
DEFAULT_OPTION_LIMIT = 100
MAX_OPTION_LIMIT = 200
MAX_SUGGESTIONS = 5


def _bare_name(raw: Any) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    if text.startswith("${") and text.endswith("}"):
        return text[2:-1].strip()
    return text.lstrip("$").strip()


def _requested_names(kwargs: Dict[str, Any]) -> List[str]:
    raw = kwargs.get("names")
    if raw is None:
        raw = kwargs.get("name")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    names: List[str] = []
    for item in raw:
        name = _bare_name(item)
        if name and name not in names:
            names.append(name)
    return names


def _matches(var: Dict[str, Any], needle: str) -> bool:
    if needle in str(var.get("name", "")).lower():
        return True
    if var.get("type") == "choice":
        return any(needle in text.lower() for text in option_texts(valid_options(var.get("options"))))
    if var.get("type") == "text":
        return needle in str(var.get("value") or "").lower()
    return False


def _summary_row(var: Dict[str, Any]) -> Dict[str, Any]:
    kind = var.get("type") if var.get("type") in ("choice", "number") else "text"
    row: Dict[str, Any] = {"name": var["name"], "type": kind}
    if kind == "text":
        row["length"] = len(str(var.get("value") or "").strip())
        return row
    row["mode"] = var.get("mode") or "shuffle"
    if kind == "choice":
        options = valid_options(var.get("options"))
        row["option_count"] = len(options)
        idx = var.get("pinnedIndex")
        if row["mode"] == "pin" and isinstance(idx, int) and 0 <= idx < len(options):
            row["pinned_value"] = options[idx]["text"]
        return row
    grid = number_grid(var)
    if grid is None:
        row["invalid"] = True
        return row
    row.update(min=grid.format_index(0), max=grid.format_index(grid.count - 1), step=format(grid.step, "f"), count=grid.count)
    if row["mode"] == "pin":
        row["pinned_value"] = number_pinned_text(var, grid)
    return row


def _readers(name: str, by_name: Dict[str, Dict[str, Any]]) -> List[str]:
    return [other for other, var in by_name.items() if other != name and name in variable_dependencies(var)]


def _detail(var: Dict[str, Any], by_name: Dict[str, Dict[str, Any]], search: str, offset: int, limit: int) -> Dict[str, Any]:
    row = _summary_row(var)
    name = var["name"]
    if row["type"] == "text":
        row["value"] = str(var.get("value") or "")
        return row
    if row["type"] == "number":
        grid = number_grid(var)
        if grid is not None:
            row["decimals"] = grid.places
        return row
    options = valid_options(var.get("options"))
    indexed = [(i, o) for i, o in enumerate(options) if not search or search in o["text"].lower()]
    page = indexed[offset:offset + limit]
    row["pinned_index"] = var.get("pinnedIndex") if row["mode"] == "pin" else None
    row["options"] = [
        {"index": i, "text": o["text"], **({"when": o["when"]} if o["when"] else {})} for i, o in page
    ]
    row["options_total"] = len(indexed)
    row["offset"] = offset
    row["limit"] = limit
    row["has_more"] = offset + len(page) < len(indexed)
    deps = variable_dependencies(var)
    if deps:
        row["resolves_after"] = deps
    readers = _readers(name, by_name)
    if readers:
        row["read_by_conditions_of"] = readers
    return row


def _suggestions(name: str, names: List[str]) -> List[str]:
    lowered = name.lower()
    close = difflib.get_close_matches(name, names, n=MAX_SUGGESTIONS, cutoff=0.5)
    contained = [n for n in names if lowered in n.lower() or n.lower() in lowered]
    return list(dict.fromkeys([*contained, *close]))[:MAX_SUGGESTIONS]


class GetPromptVariablesTool(BaseTool):
    modes = ["generation"]
    icon = "braces"

    name = "get_prompt_variables"
    mcp = True
    read_only = True
    group = "Form & segments"
    user_description = "Reads the ${name} prompt variables on your generation form, with every option."

    @property
    def description(self) -> str:
        return (
            "Read the ${name} prompt variables on the user's form. Without 'names': one summary "
            "row per variable. With 'names': full detail — every choice option with its 0-based "
            "index (what pinned_index takes) and condition, a number's range, step and count, or "
            "the full text. The form state is only a summary, so call this before picking, "
            "pinning or conditioning on an option. Page with offset/limit and check has_more."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tab_id": TAB_ID_PARAMETER,
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Variable names without ${}. Omit to list all.",
                },
                "search": {
                    "type": "string",
                    "description": "Case-insensitive filter on names and option texts (list), or on options (detail).",
                },
                "offset": {"type": "integer", "description": "Rows or options to skip. Defaults to 0."},
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Rows (default {DEFAULT_LIST_LIMIT}, max {MAX_LIST_LIMIT}) or options "
                        f"(default {DEFAULT_OPTION_LIMIT}, max {MAX_OPTION_LIMIT})."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        _tab, variables, load_error = _load_variables(context, kwargs)
        if load_error == NO_TAB_OPEN_MESSAGE:
            return ToolResult(success=True, data=json.dumps({"variables": [], "count": 0, "total": 0, "note": load_error}))
        if load_error:
            return ToolResult(success=False, data="", error=load_error)
        by_name = {name: {**var, "name": name} for name, var in variables.items()}
        search = (kwargs.get("search") or "").strip().lower()
        names = _requested_names(kwargs)
        try:
            if names:
                offset, limit = parse_page(kwargs, DEFAULT_OPTION_LIMIT, MAX_OPTION_LIMIT)
            else:
                offset, limit = parse_page(kwargs, DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT)
        except ValueError as exc:
            return ToolResult(success=False, data="", error=str(exc))

        if not names:
            rows = [v for v in by_name.values() if not search or _matches(v, search)]
            page = rows[offset:offset + limit]
            payload = {
                "variables": [_summary_row(v) for v in page],
                "count": len(page),
                "total": len(rows),
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(page) < len(rows),
            }
            if not by_name:
                payload["note"] = "This tab has no prompt variables yet; manage_prompt_variables can add some."
            return ToolResult(success=True, data=json.dumps(payload))

        found = [by_name[n] for n in names if n in by_name]
        missing = [
            {"name": n, "suggestions": _suggestions(n, list(by_name))} for n in names if n not in by_name
        ]
        if not found:
            hint = "; ".join(
                f"'{m['name']}' not found" + (f" (did you mean: {', '.join(m['suggestions'])}?)" if m["suggestions"] else "")
                for m in missing
            )
            available = ", ".join(by_name) or "none"
            return ToolResult(success=False, data="", error=f"{hint}. Variables on this form: {available}.")
        details = [_detail(v, by_name, search, offset, limit) for v in found]
        payload = {
            "variables": details,
            "count": len(details),
            "total": len(details),
            "offset": offset,
            "limit": limit,
            "has_more": any(d.get("has_more") for d in details),
        }
        if missing:
            payload["not_found"] = missing
        return ToolResult(success=True, data=json.dumps(payload))


class ListTabDraftsTool(BaseTool):
    modes = ["generation"]
    icon = "braces"

    name = "list_tab_drafts"
    mcp = True
    read_only = True
    group = "Form & segments"
    user_description = "Lists the user's Generate tabs that hold prompt variables, so another tab's variables can be read."

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        return form_state is None

    @property
    def description(self) -> str:
        return (
            "List the user's Generate tabs that have prompt variables saved on the server: tab_id, "
            "variable count, revision and last update. Pass a tab_id to get_prompt_variables or "
            "manage_prompt_variables to work on that tab."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if context.tab_drafts is None:
            return ToolResult(success=False, data="", error=NO_DRAFT_STORE_ERROR)
        tabs = [summary.to_dict() for summary in context.tab_drafts.list(context.user_id)]
        payload: Dict[str, Any] = {"tabs": tabs, "count": len(tabs)}
        if not tabs:
            payload["note"] = "No Generate tab has prompt variables saved yet."
        return ToolResult(success=True, data=json.dumps(payload))
