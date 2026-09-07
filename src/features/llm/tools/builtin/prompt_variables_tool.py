"""Tool for creating, updating, and removing the generate tab's prompt variables."""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

# Caps mirror src/platform/resources/prompt_variables.py and
# frontend/src/lib/utils/variableSnapshot.ts (small-model payload discipline).
_MAX_VARIABLES = 24
_MAX_OPTIONS = 12
_MAX_NAME_CHARS = 60

# Mirrors frontend/src/lib/utils/promptVariables.ts VARIABLE_NAME_RE — the
# grammar a `${name}` usage in the prompt actually resolves against.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_VALID_MODES = {"shuffle", "pin", "per-image"}


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


def _describe(var: Optional[Dict[str, Any]]) -> str:
    """Short plain-language description of a variable's current value — no
    name prefix, since the caller already shows `${name}` as the field."""
    if not var:
        return "not set"
    if var.get("type") == "choice":
        options = _valid_options(var.get("options"))
        if not options:
            return "choice with no options"
        mode = var.get("mode") or "shuffle"
        if mode == "pin":
            idx = var.get("pinnedIndex")
            if isinstance(idx, int) and 0 <= idx < len(options):
                return f"pinned to {options[idx]}"
            return "pinned (no option selected)"
        if mode == "per-image":
            return f"one of {', '.join(options)} — re-rolls per image"
        return f"one of {', '.join(options)} — shuffles each generation"
    value = var.get("value")
    if not isinstance(value, str) or not value.strip():
        return "empty text"
    return value.strip()


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


def _build_choice_var(op: Dict[str, Any], name: str, errors: List[str]) -> Optional[Dict[str, Any]]:
    raw_options = op.get("options")
    options = _valid_options(raw_options)
    if not options:
        errors.append(f"'{name}': a choice variable needs at least one non-empty option.")
        return None
    if isinstance(raw_options, (list, tuple)) and len(raw_options) > _MAX_OPTIONS:
        errors.append(f"'{name}': too many options ({len(raw_options)} > {_MAX_OPTIONS}).")
        return None
    mode = op.get("mode") or "shuffle"
    if mode not in _VALID_MODES:
        errors.append(f"'{name}': invalid mode '{mode}'. Use shuffle, pin, or per-image.")
        return None
    pinned_index = op.get("pinned_index")
    if pinned_index is not None and (
        not isinstance(pinned_index, int) or not (0 <= pinned_index < len(options))
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
        if not _NAME_RE.match(name) or len(name) > _MAX_NAME_CHARS:
            errors.append(
                f"'{name}' is not a valid variable name. Use letters, digits, and "
                f"underscores, starting with a letter or underscore, up to {_MAX_NAME_CHARS} characters."
            )
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
            built = _build_choice_var(op, name, errors)
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

    if len(working) > _MAX_VARIABLES:
        errors.append(
            f"Too many prompt variables: {len(working)} (max {_MAX_VARIABLES}). "
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
            "independently per image. The user must approve before changes are applied."
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
                                "items": {"type": "string"},
                                "description": "Option texts, for a 'set' with type 'choice'",
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
