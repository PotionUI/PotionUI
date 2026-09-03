"""Chat tools for the ``comfyui-import`` chat mode.

Both tools read the import wizard's live state from
``context.session_metadata["comfyui_import"]`` - the same object the
`backend.chat.context.build_import_context` contributor renders into the
system prompt every turn. Neither tool talks to a running ComfyUI server or
touches disk; the wizard already resolved candidates client-side and sends
them fresh on every message. Wire contract (sent by the frontend wizard)::

    {
      "workflow_name": str, "format": "api" | "ui", "node_count": int,
      "candidates": [{"node_id", "class_type", "node_title", "input_name",
                       "current_value", "value_type", "suggested_field_type",
                       "role", "locked"}],
      "form": {"tabs": [{"id", "label", "items": [...schema.Item dicts]}]},
      "mapped": [{"field_name", "node_id", "input_name", "transform"}],
    }

``form`` mirrors `backend.preset_import.schema.ImportForm` exactly (as plain
dicts, not parsed models - the wizard's form is a work in progress and may be
structurally incomplete mid-edit, so this module walks it defensively rather
than requiring it to validate).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from src.plugin_api import BaseTool, ToolContext, ToolResult
from src.plugin_api.chat import ToolApprovalPreview

from ..preset_import.schema import GRAPH_WIRED_FIELD_TYPES, FieldMapping

_MAX_RESULTS = 50


def _wizard_state(context: ToolContext) -> Optional[Dict[str, Any]]:
    wiz = (context.session_metadata or {}).get("comfyui_import")
    return wiz if isinstance(wiz, dict) else None


def _no_wizard_error(tool_name: str) -> ToolResult:
    return ToolResult(
        success=False,
        data="",
        error=(
            f"{tool_name} needs the import wizard's state. No 'comfyui_import' context is "
            "attached to this turn - the user must have a workflow loaded in the ComfyUI "
            "import wizard for this tool to work."
        ),
    )


def _slugify_tab_id(label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").lower()
    return slug or "tab"


def _iter_field_names_and_types(items: List[Dict[str, Any]]):
    for item in items or []:
        kind = item.get("kind")
        if kind == "field":
            yield item.get("field_name"), item.get("field_type")
        elif kind in ("row", "group", "section"):
            yield from _iter_field_names_and_types(item.get("items") or [])


def _validate_ops(
    ops: List[Any], wiz: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate `ops` against the wizard's current state.

    Ops are applied to a local copy of the known tabs/fields/mapped-candidate
    state as they're checked, so a later op in the same batch can reference a
    tab or field an earlier op in the batch adds. Returns
    `(validated_ops, errors)` - `validated_ops` items carry `_clean` (the
    normalized op for the execute_confirmed payload) and `_preview` (one
    human-readable preview line). Non-empty `errors` means reject the whole
    batch - the caller must not apply anything.
    """
    form = wiz.get("form") or {}
    tabs = form.get("tabs") or []
    known_tab_ids = {t.get("id") for t in tabs if t.get("id")}
    known_fields: Dict[str, Optional[str]] = {}
    for tab in tabs:
        for name, ftype in _iter_field_names_and_types(tab.get("items") or []):
            if name:
                known_fields[name] = ftype

    candidates_by_key = {
        (c.get("node_id"), c.get("input_name")): c for c in (wiz.get("candidates") or [])
    }
    owner_of_key: Dict[Tuple[Any, Any], str] = {
        (m.get("node_id"), m.get("input_name")): m.get("field_name")
        for m in (wiz.get("mapped") or [])
    }

    validated: List[Dict[str, Any]] = []
    errors: List[str] = []

    def check_mapping(field_name: str, node_id: Any, input_name: Any, transform: str) -> Optional[str]:
        if not node_id or not input_name:
            return "node_id and input_name are required"
        key = (node_id, input_name)
        candidate = candidates_by_key.get(key)
        if candidate is None:
            return f"no such candidate {node_id}.inputs.{input_name}"
        if candidate.get("locked"):
            return (
                f"{node_id}.inputs.{input_name} is locked (a prompt input) - prompts come "
                "from the Prompts section, never the dynamic form"
            )
        owner = owner_of_key.get(key)
        if owner is not None and owner != field_name:
            return f"{node_id}.inputs.{input_name} is already mapped to field '{owner}'"
        try:
            FieldMapping(node_id=node_id, input_name=input_name, transform=transform)
        except Exception as e:
            return f"invalid transform '{transform}' for {node_id}.inputs.{input_name}: {e}"
        return None

    for i, raw in enumerate(ops):
        if not isinstance(raw, dict):
            errors.append(f"op {i}: not an object")
            continue
        op = raw.get("op")

        if op == "add_tab":
            label = str(raw.get("label") or "").strip()
            if not label:
                errors.append(f"op {i} (add_tab): 'label' is required")
                continue
            base_id = _slugify_tab_id(label)
            tab_id, suffix = base_id, 2
            while tab_id in known_tab_ids:
                tab_id = f"{base_id}_{suffix}"
                suffix += 1
            known_tab_ids.add(tab_id)
            validated.append({
                "_clean": {"op": "add_tab", "label": label, "id": tab_id},
                "_preview": f'+ tab "{label}"',
            })

        elif op == "map":
            field_name = raw.get("field_name")
            node_id = raw.get("node_id")
            input_name = raw.get("input_name")
            transform = raw.get("transform") or "none"
            if not field_name or field_name not in known_fields:
                errors.append(
                    f"op {i} (map): unknown field '{field_name}'. "
                    f"Known fields: {sorted(known_fields) or 'none'}"
                )
                continue
            problem = check_mapping(field_name, node_id, input_name, transform)
            if problem:
                errors.append(f"op {i} (map): {problem}")
                continue
            owner_of_key[(node_id, input_name)] = field_name
            validated.append({
                "_clean": {
                    "op": "map", "field_name": field_name, "node_id": node_id,
                    "input_name": input_name, "transform": transform,
                },
                "_preview": f"{field_name} ← {node_id}.inputs.{input_name}",
            })

        elif op == "add_field":
            tab = raw.get("tab")
            field_type = raw.get("field_type")
            field_name = raw.get("field_name")
            label = raw.get("label")
            mappings = raw.get("mappings") or []
            problems: List[str] = []

            if not tab or tab not in known_tab_ids:
                problems.append(f"unknown tab '{tab}'")
            if not field_type:
                problems.append("field_type is required")
            if not field_name or not str(field_name).isidentifier():
                problems.append(f"field_name '{field_name}' is not a valid identifier")
            elif field_name in known_fields:
                problems.append(f"field_name '{field_name}' already exists")
            if not label:
                problems.append("label is required")
            if not mappings and field_type not in GRAPH_WIRED_FIELD_TYPES:
                problems.append(f"field_type '{field_type}' needs at least one mapping")

            resolved_mappings: List[Dict[str, str]] = []
            for m in mappings:
                node_id = m.get("node_id")
                input_name = m.get("input_name")
                transform = m.get("transform") or "none"
                problem = check_mapping(field_name, node_id, input_name, transform)
                if problem:
                    problems.append(problem)
                    continue
                resolved_mappings.append(
                    {"node_id": node_id, "input_name": input_name, "transform": transform}
                )

            if problems:
                errors.append(f"op {i} (add_field '{field_name}'): " + "; ".join(problems))
                continue

            known_fields[field_name] = field_type
            for m in resolved_mappings:
                owner_of_key[(m["node_id"], m["input_name"])] = field_name

            targets = ", ".join(
                f"{m['node_id']}.inputs.{m['input_name']}" for m in resolved_mappings
            ) or "(none)"
            clean: Dict[str, Any] = {
                "op": "add_field", "tab": tab, "field_type": field_type,
                "field_name": field_name, "label": label, "mappings": resolved_mappings,
            }
            if "default" in raw:
                clean["default"] = raw["default"]
            validated.append({
                "_clean": clean,
                "_preview": f"+ field {field_name} ({field_type}) ← {targets}",
            })

        else:
            errors.append(f"op {i}: unknown op '{op}'. Must be one of add_field, map, add_tab")

    return validated, errors


class GetWorkflowInputsTool(BaseTool):
    """Look up the import wizard's workflow inputs by filter/node."""

    modes = ["comfyui-import"]
    icon = "search"

    @property
    def name(self) -> str:
        return "get_workflow_inputs"

    @property
    def group(self) -> str:
        return "ComfyUI import"

    @property
    def user_description(self) -> str:
        return "Looks up workflow inputs from the ComfyUI import wizard."

    @property
    def hint(self) -> str:
        return (
            "Call this for the full detail (current_value, suggested_field_type, role) of "
            "workflow inputs before proposing a mapping, or when the system context's "
            "unmapped list was truncated."
        )

    @property
    def description(self) -> str:
        return (
            "Return workflow input candidates from the currently loaded import wizard state, "
            "with full detail (current_value, value_type, suggested_field_type, role, locked). "
            "Filter by a case-insensitive substring match against class_type/node_title/"
            "input_name/role, and/or restrict to one node_id."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filter": {
                    "type": ["string", "null"],
                    "description": "Case-insensitive substring matched against class_type, "
                                   "node_title, input_name or role",
                },
                "node_id": {
                    "type": ["string", "null"],
                    "description": "Restrict results to this node id",
                },
                "include_mapped": {
                    "type": "boolean",
                    "description": "Include candidates already mapped to a field (default false)",
                    "default": False,
                },
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        wiz = _wizard_state(context)
        if wiz is None:
            return _no_wizard_error(self.name)

        text_filter = (kwargs.get("filter") or "").strip().lower()
        node_id = kwargs.get("node_id")
        include_mapped = bool(kwargs.get("include_mapped", False))

        mapped_keys = {(m.get("node_id"), m.get("input_name")) for m in wiz.get("mapped") or []}
        candidates = wiz.get("candidates") or []

        def matches(c: Dict[str, Any]) -> bool:
            if node_id and c.get("node_id") != node_id:
                return False
            if not include_mapped and (c.get("node_id"), c.get("input_name")) in mapped_keys:
                return False
            if text_filter:
                haystack = " ".join(
                    str(c.get(k, "")) for k in ("class_type", "node_title", "input_name", "role")
                ).lower()
                if text_filter not in haystack:
                    return False
            return True

        matching = [c for c in candidates if matches(c)]
        total = len(matching)
        results = matching[:_MAX_RESULTS]

        payload: Dict[str, Any] = {"candidates": results, "count": len(results), "total_matching": total}
        if total > len(results):
            payload["note"] = f"{total - len(results)} more not shown - narrow with filter/node_id."

        return ToolResult(success=True, data=json.dumps(payload))


class ProposeFormChangesTool(BaseTool):
    """Propose add_field/map/add_tab operations against the import wizard's form."""

    modes = ["comfyui-import"]
    icon = "wand-sparkles"

    @property
    def name(self) -> str:
        return "propose_form_changes"

    @property
    def group(self) -> str:
        return "ComfyUI import"

    @property
    def user_description(self) -> str:
        return "Proposes form field mappings and tabs for the imported preset."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "Use this to apply mapping/field/tab changes to the form - never describe them in "
            "prose. Never map a prompt input; prompts come from the Prompts section, not here."
        )

    @property
    def description(self) -> str:
        return (
            "Propose changes to the import wizard's form: add_field (a new field with its "
            "mappings), map (map an existing field to a candidate input), add_tab (a new tab). "
            "All-or-nothing: if any op is invalid the whole call is rejected and nothing is "
            "applied. The user must approve before anything is applied."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ops": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": ["add_field", "map", "add_tab"]},
                            "tab": {"type": "string", "description": "add_field: target tab id"},
                            "field_type": {"type": "string", "description": "add_field only"},
                            "field_name": {"type": "string", "description": "add_field, map"},
                            "label": {"type": "string", "description": "add_field, add_tab"},
                            "mappings": {
                                "type": "array",
                                "description": "add_field only",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "node_id": {"type": "string"},
                                        "input_name": {"type": "string"},
                                        "transform": {
                                            "type": "string",
                                            "enum": ["none", "strip_model_prefix", "split_wh_width",
                                                     "split_wh_height", "seed"],
                                        },
                                    },
                                    "required": ["node_id", "input_name"],
                                },
                            },
                            "default": {"description": "add_field only, optional"},
                            "node_id": {"type": "string", "description": "map only"},
                            "input_name": {"type": "string", "description": "map only"},
                            "transform": {
                                "type": "string",
                                "description": "map only",
                                "enum": ["none", "strip_model_prefix", "split_wh_width",
                                         "split_wh_height", "seed"],
                            },
                        },
                        "required": ["op"],
                    },
                }
            },
            "required": ["ops"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        wiz = _wizard_state(context)
        if wiz is None:
            return _no_wizard_error(self.name)

        ops = kwargs.get("ops") or []
        if not ops:
            return ToolResult(success=False, data="", error="No ops provided. Specify at least one op.")

        validated, errors = _validate_ops(ops, wiz)
        if errors:
            return ToolResult(
                success=False, data="",
                error="Invalid ops - nothing was applied: " + "; ".join(errors),
            )

        preview = ToolApprovalPreview(
            action="Apply form changes",
            items=[op["_preview"] for op in validated],
        )
        payload = {"status": "pending_approval", "ops": [op["_clean"] for op in validated]}
        return ToolResult(success=True, data=json.dumps(payload), preview=preview)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        wiz = _wizard_state(context)
        if wiz is None:
            return _no_wizard_error(self.name)

        ops = kwargs.get("ops") or []
        validated, errors = _validate_ops(ops, wiz)
        if errors:
            return ToolResult(
                success=False, data="",
                error="Invalid ops - nothing was applied: " + "; ".join(errors),
            )

        payload = {"action": "apply_import_form_changes", "ops": [op["_clean"] for op in validated]}
        return ToolResult(success=True, data=json.dumps(payload))
