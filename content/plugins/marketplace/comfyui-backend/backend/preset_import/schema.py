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
from .suggest import _find_input_spec  # noqa: F401  (package-internal reuse, not a plugin_api boundary)

# Field types whose wiring is structural (a node-graph rewrite keyed off the
# workflow's own detected LoRA chain - see emit.emit_lora_manipulations)
# rather than a literal `form.<field>` value poured into one input, so they
# are exempt from the "a field needs mappings" rule below.
GRAPH_WIRED_FIELD_TYPES = frozenset({"lora_picker"})

# Real form fields the emitter always wires itself (never a `form` Item) but
# that a `history` entry may still legitimately name - see the module
# docstring on foundational fields in emit.py.
FOUNDATIONAL_FIELD_NAMES = frozenset({"seed", "quantity"})


class PresetEmitError(ValueError):
    """The `form`/`history` payload - or the emission it drives - can't be
    carried out as asked. Always surfaced as an HTTP 400."""


class FieldMapping(BaseModel):
    node_id: str
    input_name: str
    transform: Literal["none", "strip_model_prefix", "split_wh_width", "split_wh_height", "seed"] = "none"


class FieldItem(BaseModel):
    kind: Literal["field"] = "field"
    field_name: str
    field_type: str
    label: str
    default: Any = None
    config: Optional[Dict[str, Any]] = None
    mappings: List[FieldMapping] = Field(default_factory=list)


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
    items: List[Item] = Field(default_factory=list)


class ImportForm(BaseModel):
    tabs: List[FormTab] = Field(default_factory=list)


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


def validate_against_workflow(
    form: ImportForm,
    history: List[HistoryEntry],
    workflow: Workflow,
    object_info: Optional[Dict[str, Any]] = None,
) -> None:
    """Raise `PresetEmitError` (aggregating every problem found, not just the
    first) for a `form`/`history` that pydantic's shape-only checks can't
    catch: a field with no mappings, a mapping targeting a node/input this
    workflow doesn't have, or a history entry naming a field nothing defines."""
    problems: List[str] = []
    field_names: Set[str] = set(FOUNDATIONAL_FIELD_NAMES)

    for field in all_field_items(form):
        field_names.add(field.field_name)
        if not field.mappings and field.field_type not in GRAPH_WIRED_FIELD_TYPES:
            problems.append(f"field '{field.field_name}': has no mappings")
            continue
        for mapping in field.mappings:
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
