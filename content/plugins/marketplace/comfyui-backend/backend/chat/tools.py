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
      "lora_chain": {"nodes": [{"node_id", "class_type", "lora_name",
                                 "strength_model"}], "replaced": [node_id, ...],
                      "kept": [node_id, ...]} | None,
    }

``lora_chain`` mirrors the wizard's own `suggest.LoraChainInfo.nodes` plus
its current keep-fixed/replaced split (`schema.LoraChainSelection`) - absent
or `None` when the workflow has no detected LoRA chain.

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


def _find_sandwiched_kept_nodes(
    chain_nodes: List[Dict[str, Any]], replaced_ids: set, kept_ids: set
) -> List[Tuple[str, str, str]]:
    """`(kept_node_id, nearest_replaced_before, nearest_replaced_after)` for
    every kept chain node with a replaced node both before AND after it in
    `chain_nodes`' source -> target order (the same wire order
    `buildImportChatContext` sends) - mirrors
    `backend.preset_import.schema._find_sandwiched_kept_nodes` exactly, kept
    as a separate copy since this module only ever sees the wizard's plain
    wire-shape dicts, never the typed `suggest.LoraChainInfo`."""
    order = [n.get("node_id") for n in chain_nodes]
    sandwiched: List[Tuple[str, str, str]] = []
    for i, node_id in enumerate(order):
        if node_id not in kept_ids:
            continue
        before = next((order[j] for j in range(i - 1, -1, -1) if order[j] in replaced_ids), None)
        after = next((order[j] for j in range(i + 1, len(order)) if order[j] in replaced_ids), None)
        if before is not None and after is not None:
            sandwiched.append((node_id, before, after))
    return sandwiched


def _iter_field_names_and_types(items: List[Dict[str, Any]]):
    for item in items or []:
        kind = item.get("kind")
        if kind == "field":
            yield item.get("field_name"), item.get("field_type")
        elif kind in ("row", "group", "section"):
            yield from _iter_field_names_and_types(item.get("items") or [])


# A model asked to propose form changes routinely names things close to but
# not exactly the tool's own vocabulary (the shape a webui/ComfyUI node
# itself uses, or plain English). Resolved before validation so a near-miss
# is repaired instead of rejected -- the schema still enforces the real
# shape once normalized, teaching the model what changed only when it
# couldn't be resolved.
_TAB_KEY_ALIASES = ("tab_id", "tab_label")
_FIELD_NAME_KEY_ALIASES = ("id", "name")
_DEFAULT_KEY_ALIASES = ("default_value", "value")
_MAPPINGS_KEY_ALIASES = ("mapping", "inputs")

_FIELD_TYPE_ALIASES = {
    "wh": "resolution", "size": "resolution",
    "checkpoint": "model",
    "combo": "select",
    "str": "string",
    "int": "integer",
    "float": "number",
}

# Every core (non-container) field type `register_builtin_fields`
# (`src/features/fields/builtin.py`) registers. Duplicated here rather than
# imported because a marketplace plugin may only import `src.plugin_api`,
# which does not expose the field type registry -- keep in sync by hand.
_KNOWN_FIELD_TYPES = frozenset({
    "string", "textbox", "number", "integer", "boolean", "checkbox", "slider",
    "stepper", "seed", "resolution", "select", "checkbox_group", "model",
    "models", "lora_picker", "image", "video", "audio", "media", "file",
    "carousel", "llm", "alert", "markdown", "header", "section", "gate",
    "prompt_timeline", "camera_shot",
})


def _apply_key_aliases(op: Dict[str, Any], aliases: Tuple[str, ...], canonical: str) -> None:
    if canonical in op:
        for alias in aliases:
            op.pop(alias, None)
        return
    for alias in aliases:
        if alias in op:
            op[canonical] = op.pop(alias)
            return


def _resolve_tab_alias(tab: Any, tabs: List[Dict[str, Any]], known_tab_ids: set) -> Any:
    if not isinstance(tab, str) or tab in known_tab_ids:
        return tab
    lowered = tab.strip().lower()
    if tabs:
        first = tabs[0]
        if lowered in (str(first.get("id") or "").lower(), str(first.get("label") or "").lower()):
            return first.get("id")
    if lowered == "main" and len(tabs) == 1:
        return tabs[0].get("id")
    return tab


def _normalize_op(raw: Any, tabs: List[Dict[str, Any]], known_tab_ids: set) -> Any:
    """Repair the near-miss op shapes a model reaches for instead of this
    tool's own vocabulary. Structural (keys/aliases), not semantic -- a
    genuinely invalid op still fails `_validate_ops` afterward, now against
    its real mistake rather than a synonym of it."""
    if not isinstance(raw, dict):
        return raw
    op = dict(raw)
    nested = op.pop("field", None)
    if isinstance(nested, dict):
        op = {**nested, **op}

    _apply_key_aliases(op, _TAB_KEY_ALIASES, "tab")
    _apply_key_aliases(op, _FIELD_NAME_KEY_ALIASES, "field_name")
    _apply_key_aliases(op, _DEFAULT_KEY_ALIASES, "default")
    _apply_key_aliases(op, _MAPPINGS_KEY_ALIASES, "mappings")
    if "field_type" in op:
        op.pop("type", None)
    elif "type" in op:
        op["field_type"] = op.pop("type")

    if isinstance(op.get("field_type"), str):
        key = op["field_type"].strip().lower()
        op["field_type"] = _FIELD_TYPE_ALIASES.get(key, key)

    if "tab" in op:
        op["tab"] = _resolve_tab_alias(op["tab"], tabs, known_tab_ids)

    mappings = op.get("mappings")
    if isinstance(mappings, list):
        op["mappings"] = [
            {**m, "transform": m.get("transform") or "none"} if isinstance(m, dict) else m
            for m in mappings
        ]
    return op


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

    for i, original in enumerate(ops):
        raw = _normalize_op(original, tabs, known_tab_ids)
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
            elif field_type not in _KNOWN_FIELD_TYPES:
                problems.append(
                    f"unknown field_type '{field_type}'. Must be one of: "
                    f"{', '.join(sorted(_KNOWN_FIELD_TYPES))}"
                )
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

        elif op == "lora_picker":
            chain_nodes = ((wiz.get("lora_chain") or {}).get("nodes")) or []
            chain_node_ids = {n.get("node_id") for n in chain_nodes if n.get("node_id")}
            if not chain_node_ids:
                errors.append(f"op {i} (lora_picker): no LoRA chain detected in this workflow")
                continue
            if any(ftype == "lora_picker" for ftype in known_fields.values()):
                errors.append(f"op {i} (lora_picker): a LoRA picker field already exists")
                continue

            tab = raw.get("tab") or (tabs[0].get("id") if tabs else None)
            if not tab or tab not in known_tab_ids:
                errors.append(f"op {i} (lora_picker): unknown tab '{tab}'")
                continue

            keep_fixed = raw.get("keep_fixed") or []
            if not isinstance(keep_fixed, list):
                errors.append(f"op {i} (lora_picker): 'keep_fixed' must be a list of node ids")
                continue
            keep_fixed_set = set(keep_fixed)
            unknown_ids = keep_fixed_set - chain_node_ids
            if unknown_ids:
                errors.append(
                    f"op {i} (lora_picker): 'keep_fixed' has node(s) not in the detected chain: "
                    f"{sorted(unknown_ids)}"
                )
                continue

            replaced_ids = chain_node_ids - keep_fixed_set
            sandwiched = _find_sandwiched_kept_nodes(chain_nodes, replaced_ids, keep_fixed_set)
            if sandwiched:
                node_id, before_id, after_id = sandwiched[0]
                errors.append(
                    f"op {i} (lora_picker): keep_fixed leaves kept LoRA node {node_id} sandwiched "
                    f"between replaced nodes {before_id} and {after_id} - keep all LoRAs above it "
                    "fixed too, or replace it"
                )
                continue

            field_name = "loras"
            suffix = 2
            while field_name in known_fields:
                field_name = f"loras_{suffix}"
                suffix += 1
            known_fields[field_name] = "lora_picker"

            replaced_count = len(chain_node_ids - keep_fixed_set)
            kept_desc = (
                f", node {sorted(keep_fixed_set)[0]} kept fixed" if len(keep_fixed_set) == 1
                else f", {len(keep_fixed_set)} nodes kept fixed" if keep_fixed_set
                else ""
            )
            validated.append({
                "_clean": {
                    "op": "lora_picker", "tab": tab, "field_name": field_name,
                    "keep_fixed": sorted(keep_fixed_set),
                },
                "_preview": f"+ LoRA picker ({replaced_count} LoRAs seeded{kept_desc})",
            })

        else:
            errors.append(f"op {i}: unknown op '{op}'. Must be one of add_field, map, add_tab, lora_picker")

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
            "prose. Never map a prompt input; prompts come from the Prompts section, not here. "
            "When the workflow has a detected LoRA chain (see the context block), use lora_picker "
            "to convert it to a LoRA picker field rather than mapping its lora_name/strength "
            "inputs one at a time."
        )

    @property
    def description(self) -> str:
        return (
            "Propose changes to the import wizard's form: add_field (a new field with its "
            "mappings), map (map an existing field to a candidate input), add_tab (a new tab), "
            "lora_picker (convert the workflow's detected LoRA chain into a lora_picker field, "
            "optionally keeping some chain nodes fixed via keep_fixed). All-or-nothing: if any op "
            "is invalid the whole call is rejected and nothing is applied. The user must approve "
            "before anything is applied."
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
                            "op": {"type": "string", "enum": ["add_field", "map", "add_tab", "lora_picker"]},
                            "tab": {"type": "string", "description": "add_field, lora_picker: target tab id"},
                            "field_type": {"type": "string", "description": "add_field only"},
                            "field_name": {"type": "string", "description": "add_field, map"},
                            "label": {"type": "string", "description": "add_field, add_tab"},
                            "keep_fixed": {
                                "type": "array",
                                "description": "lora_picker only: chain node ids to leave wired as-is "
                                               "instead of replacing with the picker",
                                "items": {"type": "string"},
                            },
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
