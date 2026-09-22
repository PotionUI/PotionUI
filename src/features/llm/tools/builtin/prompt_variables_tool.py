"""Tool for creating, updating, and removing the generate tab's prompt variables."""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.platform.resources.prompt_variables import (
    MAX_OPTIONS,
    MAX_VARIABLES,
    VALID_MODES,
    dependency_prefix,
    format_option,
    name_error,
    normalize_option,
    option_texts,
    valid_options,
    validate_condition,
    variable_dependencies,
)

logger = logging.getLogger(__name__)


def _describe(var: Optional[Dict[str, Any]]) -> str:
    """Short plain-language description of a variable's current value — no
    name prefix, since the caller already shows `${name}` as the field."""
    if not var:
        return "not set"
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


def _existing_by_name(form_state: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    variables = (form_state or {}).get("variables")
    if not isinstance(variables, (list, tuple)):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for var in variables:
        if not isinstance(var, dict):
            continue
        name = var.get("name")
        if isinstance(name, str) and name.strip():
            out[name.strip()] = var
    return out


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
    if len(raw_options) > MAX_OPTIONS:
        errors.append(f"'{name}': too many options ({len(raw_options)} > {MAX_OPTIONS}).")
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


def _validate_operations(
    operations: List[Dict[str, Any]],
    existing_by_name: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Validates operations against the existing variable snapshot.

    Returns (validated_ops, preview_rows, errors). `validated_ops` is the
    normalized wire shape the frontend's `apply_variable_changes` action
    consumes; `preview_rows` is the `field_name`/`old_value`/`new_value` shape
    the approval dock already renders for `update_form_settings` (see
    `buildApprovalDiff`). A count-cap breach discards the whole batch — it is
    a global constraint, not a per-operation one.
    """
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
        if var_type not in ("text", "choice"):
            errors.append(f"'{name}': invalid type '{var_type}'. Use text or choice.")
            continue

        if var_type == "text":
            value = op.get("value")
            if value is None:
                value = ""
            if not isinstance(value, str):
                errors.append(f"'{name}': a text variable's value must be a string.")
                continue
            new_var: Dict[str, Any] = {"type": "text", "value": value}
        else:
            built = _build_choice_var(op, name, working, errors)
            if built is None:
                continue
            new_var = built

        old_desc = _describe(working.get(name))
        working[name] = new_var
        new_desc = _describe(new_var)

        wire_op: Dict[str, Any] = {"op": "set", "name": name, "type": new_var["type"]}
        if new_var["type"] == "text":
            wire_op["value"] = new_var["value"]
        else:
            wire_op["options"] = new_var["options"]
            wire_op["mode"] = new_var["mode"]
            wire_op["pinned_index"] = new_var["pinnedIndex"]
        validated_ops.append(wire_op)

        row = {"field_name": f"${{{name}}}", "old_value": old_desc, "new_value": new_desc}
        if reason:
            row["reason"] = reason
        preview_rows.append(row)

    if len(working) > MAX_VARIABLES:
        errors.append(
            f"Too many prompt variables: {len(working)} (max {MAX_VARIABLES}). "
            "Remove some before adding more."
        )
        return [], [], errors

    return validated_ops, preview_rows, errors


class ManagePromptVariablesTool(BaseTool):
    """Creates, updates, and removes the generate tab's ${name} prompt variables."""

    modes = ["generation"]
    icon = "braces"

    @property
    def name(self) -> str:
        return "manage_prompt_variables"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Creates or changes the ${name} prompt variables on your generation form."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "Prompt variables are named ${name} placeholders the user reuses across the "
            "prompt. An EXISTING variable is used by writing ${name} verbatim in proposed "
            "segment text — no tool call needed for that. To introduce a NEW variable, call "
            "this tool first (the user approves the change), then use ${name} in the "
            "segment text you propose. Never write a ${name} that neither already exists "
            "nor is created by this tool in the same turn — an undefined variable expands "
            "to nothing."
            "{{#if get_form_state}} Call get_form_state first to see which variables "
            "already exist.{{/if}}"
        )

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
            "The user must approve before changes are applied."
            "{{#if get_form_state}} Call get_form_state first to see existing variables and "
            "their current values.{{/if}}"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
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
                                "enum": ["text", "choice"],
                                "description": (
                                    "Required for 'set' on a new variable; defaults to the "
                                    "existing variable's type when updating one."
                                ),
                            },
                            "value": {
                                "type": "string",
                                "description": "The value, for a 'set' with type 'text'",
                            },
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
                                "description": "How a choice variable resolves; defaults to 'shuffle'",
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
        """Preview proposed variable changes - validates and shows old -> new values."""
        operations = kwargs.get("operations", [])
        if not operations:
            return ToolResult(
                success=False,
                data="",
                error="No operations provided. Specify at least one variable change.",
            )

        form_state = context.session_metadata.get("form_state")
        if not form_state:
            return ToolResult(
                success=False,
                data="",
                error="No form state available. The user may not have a form loaded.",
            )

        existing = _existing_by_name(form_state)
        _validated, preview_rows, errors = _validate_operations(operations, existing)

        if errors and not preview_rows:
            return ToolResult(success=False, data="", error="; ".join(errors))

        result = {
            "status": "pending_approval",
            "proposed_changes": preview_rows,
            "change_count": len(preview_rows),
        }
        if errors:
            result["warnings"] = errors

        return ToolResult(success=True, data=json.dumps(result))

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """After user approval, return action payload for frontend to apply."""
        operations = kwargs.get("operations", [])

        # Approval is the USER agreeing to the change, not evidence the form
        # state is unchanged since the preview - and these arguments are
        # replayed from storage, so the preview's checks don't carry over.
        # Re-run them.
        form_state = context.session_metadata.get("form_state")
        existing = _existing_by_name(form_state)
        validated_ops, _preview_rows, errors = _validate_operations(operations, existing)

        if errors and not validated_ops:
            return ToolResult(success=False, data="", error="; ".join(errors))

        payload = {
            "action": "apply_variable_changes",
            "operations": validated_ops,
        }
        if errors:
            payload["rejected_operations"] = errors

        return ToolResult(success=True, data=json.dumps(payload))
