"""The import wizard's fixed wire contract: a `form` (the preset's tab/field
layout, as the admin arranged it) plus a `history` list (which fields get
recorded to generation history, and how). Both travel in `POST
.../presets/import`'s body, are echoed back by `/presets/import/analyze` as
`default_form`/`default_history`, and are what `import.json` stores for
reload/modify - see `docs/presets.md`'s "ComfyUI presets" chapter for the
full shape.

Two validation tiers, deliberately kept apart:

- Pydantic (`ImportForm`/`HistoryEntry`) catches a malformed shape - an
  unknown `kind`, a missing required key - as an ordinary `ValidationError`.
- `validate_against_workflow` catches what pydantic can't see on its own: a
  field with no mappings, a mapping pointing at a node/input this workflow
  doesn't have, or a history entry naming a field the form never defines.
  Both tiers are surfaced by `backend/api.py` as one 400, per the contract.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

from pydantic import BaseModel, Field, model_validator
from typing_extensions import Annotated

from .parser import Workflow
from .suggest import suggest_fields  # package-internal reuse, not a plugin_api boundary

# Field types whose wiring is structural (a node-graph rewrite keyed off the
# workflow's own detected LoRA chain - see emit.emit_lora_manipulations)
# rather than a literal `form.<field>` value poured into one input, so they
# are exempt from the "a field needs mappings" rule below.
GRAPH_WIRED_FIELD_TYPES = frozenset({"lora_picker"})

# Real form fields the emitter always wires itself (never a `form` Item) but
# that a `history` entry may still legitimately name - see the module
# docstring on foundational fields in emit.py.
FOUNDATIONAL_FIELD_NAMES = frozenset({"seed", "quantity"})

# suggest.InputCandidate roles that mean "this input is a prompt" - whether
# found structurally (a sampler's own conditioning links) or by
# suggest._fallback_prompt_roles's name-based fallback for an all-in-one
# node with no separate sampler. Never a valid `mappings` target: prompts
# come from the Prompts section, never the dynamic form (see emit.py's
# module docstring on foundational fields).
_PROMPT_ROLES = frozenset({"prompt_positive", "prompt_negative"})


class PresetEmitError(ValueError):
    """The `form`/`history` payload - or the emission it drives - can't be
    carried out as asked. Always surfaced as an HTTP 400."""


class FieldMapping(BaseModel):
    node_id: str
    input_name: str
    transform: Literal["none", "strip_model_prefix", "split_wh_width", "split_wh_height", "seed"] = "none"


# Field types whose `default` must be a specific native Python type for
# `src/features/presets/schema.py`'s `_validate_typed_default` (preset lint)
# to accept it - see `_typed_default`. Kept in sync with that module's
# `_BOOL_FIELD_TYPES`/`_NUMERIC_FIELD_TYPES`/`_INTEGER_FIELD_TYPES`, plus
# `stepper`/`seed` pinned to `int` (lint accepts either int or float for
# them, but nothing in this importer ever produces a fractional stepper or
# seed default) and `gate` treated like `checkbox` (lint doesn't check a
# gate's default type at all, but a stray string default is never right).
_INT_FIELD_TYPES = frozenset({"integer", "stepper", "seed"})
_NUMERIC_FIELD_TYPES = frozenset({"number", "slider"})
_BOOL_FIELD_TYPES = frozenset({"checkbox", "boolean", "gate"})
_TRUE_STRINGS = frozenset({"true", "1"})
_FALSE_STRINGS = frozenset({"false", "0"})


def _typed_default(field_type: str, field_name: str, value: Any) -> Any:
    """Coerce `value` to the native Python type `field_type` requires,
    raising `PresetEmitError` (naming the field and the offending value)
    when it can't be. Every `default` reaching a preset.yml or its
    `import.json` sidecar goes through this - the wizard's default-editing
    input (`ImportWorkflowTab.svelte`'s `setDefaultFromText`) hands back
    plain text, so an admin editing e.g. an integer field's default leaves
    it a str unless something coerces it back before it's written."""
    if value is None:
        return None

    if field_type in _BOOL_FIELD_TYPES:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in _TRUE_STRINGS:
                return True
            if lowered in _FALSE_STRINGS:
                return False
        raise PresetEmitError(f"field '{field_name}' ({field_type}): default {value!r} is not a boolean")

    if field_type in _INT_FIELD_TYPES:
        if not isinstance(value, bool):
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, str):
                text = value.strip()
                try:
                    return int(text)
                except ValueError:
                    try:
                        as_float = float(text)
                    except ValueError:
                        as_float = None
                    if as_float is not None and as_float.is_integer():
                        return int(as_float)
        raise PresetEmitError(f"field '{field_name}' ({field_type}): default {value!r} is not a whole number")

    if field_type in _NUMERIC_FIELD_TYPES:
        if not isinstance(value, bool):
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, str):
                text = value.strip()
                try:
                    return int(text)
                except ValueError:
                    try:
                        return float(text)
                    except ValueError:
                        pass
        raise PresetEmitError(f"field '{field_name}' ({field_type}): default {value!r} is not a number")

    return value


class FieldItem(BaseModel):
    kind: Literal["field"] = "field"
    field_name: str
    field_type: str
    label: str
    default: Any = None
    config: Optional[Dict[str, Any]] = None
    mappings: List[FieldMapping] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_matches_field_type(self) -> "FieldItem":
        # Coerced here (not just at emit time) so a re-edited-then-reloaded
        # preset's `import.json` sidecar (`form.model_dump()`, written
        # straight from this model) never carries an untyped default either.
        self.default = _typed_default(self.field_type, self.field_name, self.default)
        return self


class RowItem(BaseModel):
    kind: Literal["row"] = "row"
    columns: Literal[2, 3, 4] = 2
    items: List["Item"] = Field(default_factory=list)


class GroupItem(BaseModel):
    kind: Literal["group"] = "group"
    title: str
    items: List["Item"] = Field(default_factory=list)


class SectionItem(BaseModel):
    kind: Literal["section"] = "section"
    title: str
    collapsed: bool = False
    items: List["Item"] = Field(default_factory=list)


class HeaderItem(BaseModel):
    kind: Literal["header"] = "header"
    text: str


Item = Annotated[
    Union[FieldItem, RowItem, GroupItem, SectionItem, HeaderItem],
    Field(discriminator="kind"),
]

RowItem.model_rebuild()
GroupItem.model_rebuild()
SectionItem.model_rebuild()


class FormTab(BaseModel):
    id: str
    label: str
    icon: Optional[str] = None
    icon_display: Literal["icon_only", "icon_label", "label"] = "icon_only"
    items: List[Item] = Field(default_factory=list)

    @model_validator(mode="after")
    def _icon_display_needs_an_icon(self) -> "FormTab":
        # Core TabsField renders icon_only/icon_label by pulling `configuration.icon`
        # - with no icon there is nothing for either mode to show but the tab's
        # first letter, so a tab with no icon is always forced to plain-label.
        if self.icon is None and self.icon_display != "label":
            self.icon_display = "label"
        return self


class LoraChainSelection(BaseModel):
    """Which of the workflow's detected LoRA chain nodes (see
    `suggest.LoraChainInfo`) the wizard's "Convert to LoRA picker" action
    replaced with the `lora_picker` field's node-graph rewrite versus left
    wired exactly as the source workflow had them - a sibling of `tabs` on
    `ImportForm` rather than a key on the `lora_picker` `FieldItem` itself,
    since it describes a workflow-level decision (which nodes the emitter
    excludes) rather than anything about the field's own shape. Round-trips
    through `import.json` (`emit.emit_preset`'s sidecar) unchanged, exactly
    like `tabs`.

    Every id in `replaced_node_ids`/`kept_node_ids` must be a node the
    current analysis's `lora_chain` actually detected, and together they
    must account for every one of them exactly once - enforced by
    `validate_against_workflow`, not by pydantic (which can't see the
    workflow to check against)."""

    replaced_node_ids: List[str] = Field(default_factory=list)
    kept_node_ids: List[str] = Field(default_factory=list)


class ImportForm(BaseModel):
    tabs: List[FormTab] = Field(default_factory=list)
    lora_chain: Optional[LoraChainSelection] = None


class HistoryEntry(BaseModel):
    field: str
    label: str
    format: Literal["as_is", "number", "wxh", "model_name", "list", "jinja"] = "as_is"
    template: Optional[str] = None

    @model_validator(mode="after")
    def _jinja_needs_a_template(self) -> "HistoryEntry":
        if self.format == "jinja" and not (self.template or "").strip():
            raise ValueError(f"history entry '{self.field}': format 'jinja' requires a non-empty template")
        return self


def iter_field_items(items: List[Any]):
    """Depth-first `FieldItem`s under `items` (rows/groups/sections walked
    through, headers skipped - they carry no field).

    Dispatches on `item.kind` rather than `isinstance` - a plain plugin
    unit test can reload `backend.*` mid-session (see e.g.
    `test_plugin_route_authz.py`), which mints a second, isinstance-
    incompatible copy of every class in this module; `kind` is a plain
    string field, immune to which module generation actually built it."""
    for item in items:
        if item.kind == "field":
            yield item
        elif item.kind in ("row", "group", "section"):
            yield from iter_field_items(item.items)


def all_field_items(form: ImportForm):
    for tab in form.tabs:
        yield from iter_field_items(tab.items)


def _known_input_names(node_id: str, workflow: Workflow, object_info: Optional[Dict[str, Any]]) -> Optional[Set[str]]:
    """The input names this node is known to accept, or `None` when nothing
    can confirm that (an unrecognized class with no live `object_info` to
    ask) - `None` means "accept whatever the workflow itself already has",
    never "accept anything"."""
    node = workflow.node(node_id)
    if node is None:
        return set()
    class_info = (object_info or {}).get(node.class_type)
    if isinstance(class_info, dict):
        input_defs = class_info.get("input")
        if isinstance(input_defs, dict):
            names: Set[str] = set()
            for section in ("required", "optional"):
                section_defs = input_defs.get(section)
                if isinstance(section_defs, dict):
                    names.update(section_defs.keys())
            return names
    return None  # unknown class - fall back to the node's own recorded inputs


def _prompt_role_targets(workflow: Workflow, object_info: Optional[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """`{(node_id, input_name)}` for every input `suggest_fields` identifies
    as a prompt - structurally, or via its name-based fallback for a
    workflow with no separate sampler to follow a conditioning link from
    (see `suggest._fallback_prompt_roles`). Recomputed here rather than
    threaded through as a parameter so this rule can never be skipped by a
    caller that forgot to pass it."""
    analysis = suggest_fields(workflow, object_info=object_info)
    return {(c.node_id, c.input_name) for c in analysis.candidates if c.role in _PROMPT_ROLES}


def _find_sandwiched_kept_nodes(
    chain, replaced_ids: Set[str], kept_ids: Set[str]
) -> List[Tuple[str, str, str]]:
    """`(kept_node_id, nearest_replaced_before, nearest_replaced_after)` for
    every kept chain node that has a replaced node both before AND after it
    in `chain.nodes`' source -> target order - the one shape the emitter's
    graph rewrite can't represent: a flat `lora_picker` loop has no way to
    re-insert a fixed node's own effect partway through a runtime-variable-
    length sequence (see `emit._bypass_replaced_lora_nodes`'s docstring), so
    this is rejected rather than silently dropping the kept node from the
    live compute path."""
    order = [n.node_id for n in chain.nodes]
    sandwiched: List[Tuple[str, str, str]] = []
    for i, node_id in enumerate(order):
        if node_id not in kept_ids:
            continue
        before = next((order[j] for j in range(i - 1, -1, -1) if order[j] in replaced_ids), None)
        after = next((order[j] for j in range(i + 1, len(order)) if order[j] in replaced_ids), None)
        if before is not None and after is not None:
            sandwiched.append((node_id, before, after))
    return sandwiched


def _validate_lora_chain_selection(
    form: ImportForm, workflow: Workflow, object_info: Optional[Dict[str, Any]]
) -> List[str]:
    """`form.lora_chain` is optional - a form with no selection at all (a
    hand-built form, or a preset imported before this selection existed)
    means "replace the whole detected chain", exactly `emit.emit_preset`'s
    pre-existing behavior, so nothing is checked here in that case. Once a
    selection IS given, though, it must be internally consistent:
    `replaced_node_ids`/`kept_node_ids` together must name every node the
    current analysis's `lora_chain` detects, each exactly once - a stale
    selection left over from editing the workflow (a node id the chain no
    longer has, or a chain node neither list mentions) is caught here
    rather than silently mis-splicing the emitted graph.

    A workflow with no detected chain at all can still legitimately carry
    an empty selection (`{replaced_node_ids: [], kept_node_ids: []}`) - the
    `lora_picker` field, in that case, splices onto the sampling cluster's
    own model-chain boundary instead (see `emit._lora_node_manipulations`),
    so there is nothing here to account for. A NON-empty selection with no
    detected chain, though, is still a stale/invalid one."""
    selection = form.lora_chain
    if selection is None:
        return []

    analysis = suggest_fields(workflow, object_info=object_info)
    chain = analysis.lora_chain
    if chain is None:
        if not selection.replaced_node_ids and not selection.kept_node_ids:
            return []
        return ["lora_chain: a selection was given but this workflow has no detected LoRA chain"]

    problems: List[str] = []
    chain_ids = set(chain.lora_node_ids)
    replaced = list(selection.replaced_node_ids)
    kept = list(selection.kept_node_ids)
    overlap = set(replaced) & set(kept)
    if overlap:
        problems.append(f"lora_chain: node(s) {sorted(overlap)} listed as both replaced and kept")

    union = set(replaced) | set(kept)
    missing = chain_ids - union
    if missing:
        problems.append(f"lora_chain: missing chain node(s) from replaced/kept: {sorted(missing)}")
    extra = union - chain_ids
    if extra:
        problems.append(f"lora_chain: replaced/kept name node(s) not in the detected chain: {sorted(extra)}")

    if not problems:
        # Only meaningful once replaced/kept actually partition the chain -
        # a node id already flagged as missing/overlapping/unknown above
        # would just produce a confusing second error about the same thing.
        for node_id, before_id, after_id in _find_sandwiched_kept_nodes(chain, set(replaced), set(kept)):
            problems.append(
                f"lora_chain: kept LoRA node {node_id} sits between replaced nodes {before_id} and "
                f"{after_id}; keep all LoRAs above it fixed too, or replace it"
            )

    return problems


def validate_against_workflow(
    form: ImportForm,
    history: List[HistoryEntry],
    workflow: Workflow,
    object_info: Optional[Dict[str, Any]] = None,
) -> None:
    """Raise `PresetEmitError` (aggregating every problem found, not just the
    first) for a `form`/`history` that pydantic's shape-only checks can't
    catch: a field with no mappings, a mapping targeting a node/input this
    workflow doesn't have, a mapping targeting a prompt input (prompts come
    from the Prompts section, never a form field), or a history entry
    naming a field nothing defines."""
    problems: List[str] = []
    field_names: Set[str] = set(FOUNDATIONAL_FIELD_NAMES)
    prompt_targets = _prompt_role_targets(workflow, object_info)
    problems.extend(_validate_lora_chain_selection(form, workflow, object_info))

    for field in all_field_items(form):
        field_names.add(field.field_name)
        if not field.mappings and field.field_type not in GRAPH_WIRED_FIELD_TYPES:
            problems.append(f"field '{field.field_name}': has no mappings")
            continue
        for mapping in field.mappings:
            if (mapping.node_id, mapping.input_name) in prompt_targets:
                problems.append(
                    f"field '{field.field_name}': maps to {mapping.node_id}.inputs.{mapping.input_name}, "
                    "a prompt input - prompts come from the Prompts section, not a form field"
                )
                continue
            node = workflow.node(mapping.node_id)
            if node is None:
                problems.append(
                    f"field '{field.field_name}': mapping targets unknown node '{mapping.node_id}'"
                )
                continue
            known = _known_input_names(mapping.node_id, workflow, object_info)
            has_input = mapping.input_name in node.inputs
            if known is not None:
                if mapping.input_name not in known and not has_input:
                    problems.append(
                        f"field '{field.field_name}': node '{mapping.node_id}' ({node.class_type}) has no "
                        f"input '{mapping.input_name}'"
                    )
            elif not has_input:
                problems.append(
                    f"field '{field.field_name}': node '{mapping.node_id}' has no input '{mapping.input_name}'"
                )

    for entry in history:
        if entry.field not in field_names:
            problems.append(f"history entry '{entry.field}': no such form field")

    if problems:
        raise PresetEmitError("Invalid form/history payload: " + "; ".join(problems))


def parse_form(raw: Optional[Dict[str, Any]]) -> ImportForm:
    """Parse+shape-validate the incoming `form` dict, wrapping pydantic's
    `ValidationError` as a `PresetEmitError` (a 400, not a 422 - see the
    module docstring) and applying the "no tabs" default from the contract."""
    try:
        form = ImportForm.model_validate(raw or {})
    except Exception as e:  # pydantic.ValidationError
        raise PresetEmitError(f"Invalid form payload: {e}") from e
    if not form.tabs:
        form.tabs = [FormTab(id="generation", label="Generation", items=[])]
    return form


def parse_history(raw: Optional[List[Dict[str, Any]]]) -> List[HistoryEntry]:
    try:
        return [HistoryEntry.model_validate(e) for e in (raw or [])]
    except Exception as e:  # pydantic.ValidationError
        raise PresetEmitError(f"Invalid history payload: {e}") from e
