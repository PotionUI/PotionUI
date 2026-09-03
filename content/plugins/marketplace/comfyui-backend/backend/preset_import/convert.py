"""Converting a ComfyUI **UI-format** workflow (`Workflow -> Export`, the
`{"nodes": [...], "links": [...], "groups": [...]}` shape ComfyUI's own
editor saves) into the same Export (API) shape `parser.parse_api_workflow`
already understands, mirroring what the ComfyUI frontend itself does when it
serializes a graph to send for execution.

This only works with a live server's `GET /object_info` in hand, mainly to
enrich suggestions and to tell a genuinely unrecognized node class from a
known one - widget *names*, when mapping `widgets_values` to input names,
come from the export itself when a recent-enough ComfyUI frontend recorded
them (`_exported_widget_names`), object_info's declared order otherwise. A
node class that's in neither - an uninstalled custom node pack, or a server
that doesn't match what the workflow was built against - is never a reason
to fail the whole import: it converts best-effort (its link inputs resolve
normally; its widget inputs get positional `widget_N` names if nothing else
named them) and is listed in the result's `unknown_nodes`, so the analyze/
import UI can surface it as "this node isn't installed, its fields are
best-effort - re-import once it is" instead of refusing outright. The whole
point of importing is to see what's missing.

Subgraphs (`definitions.subgraphs`, the newest export shape - a node's
`type` names a subgraph's UUID instead of a class_type) are flattened
in-place rather than rejected: every inner node is emitted with a
`<instance_id>:<inner_id>` id (nested instances chain,
`<outer>:<inner_instance>:<node>`), and a link crossing the subgraph's
boundary is resolved straight through to whatever really produces or
consumes the value, exactly as ComfyUI's own frontend flattens a graph
before sending it. See `_Scope`/`_resolve_source` below.

Promoted widgets - a subgraph exposing one of its inner nodes' widgets as
one of the *instance's* own widgets - are read from an assumed
`subgraph.widgets: [{"node_id": <inner_local_id>, "name": <widget_name>},
...]` list, zipped positionally against the instance node's own
`widgets_values`; this shape isn't publicly documented anywhere this
importer could confirm against a real export, so it's a best-effort read
that degrades to "the inner node's own default stays" (per its own
`widgets_values`) if the list isn't there in the exact shape expected,
never a hard failure.

Deferred: bypassed nodes (`mode: 4`) are dropped exactly like muted nodes
(`mode: 2`) rather than having their inputs threaded through to whatever
they fed - a faithful bypass needs to match each output's type against a
downstream input's type, which is a materially bigger piece of graph work
than this first cut takes on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .parser import WorkflowFormatError

# Widget-value scalar types (as opposed to socket types like MODEL, CLIP,
# LATENT, IMAGE, CONDITIONING, ...). A COMBO input is declared as a list of
# option strings instead of one of these names, so it's recognized
# separately (see `_widget_input_order`).
_WIDGET_SCALAR_TYPES = frozenset({"INT", "FLOAT", "STRING", "BOOLEAN"})

# ComfyUI node `mode` values that mean "don't run this node". Bypass (4) is
# collapsed into mute (2) here - see the module docstring's "Deferred" note.
_SKIPPED_MODES = frozenset({2, 4})

_PASSTHROUGH_TYPES = frozenset({"Reroute", "PrimitiveNode"})

# Named widgets ComfyUI pairs with an invisible "randomize"/"fixed" control
# widget, consuming an extra `widgets_values` slot right after the value
# itself. Used only as a last-resort guess for a node class /object_info
# doesn't know at all (see `_resolve_widget_names`) - a known class's own
# `control_after_generate` flag (in its /object_info entry) is always used
# instead when available.
_SEED_LIKE_WIDGET_NAMES = frozenset({"seed", "noise_seed"})

# A subgraph definition's own inner node list may carry literal placeholder
# entries at these ids marking where the boundary sits (ComfyUI's subgraph
# editor draws them as the input/output "pill" nodes) - present or not, the
# boundary itself is what an inner link's origin/target of -10/-20 means,
# so these ids are only ever skipped when iterating real nodes, never
# resolved as ordinary node lookups.
_VIRTUAL_INPUT_ID = -10
_VIRTUAL_OUTPUT_ID = -20


def _widget_input_order(class_info: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """`[(input_name, config), ...]` for every *widget* input `class_info`
    (one `/object_info` entry) declares, in declaration order - required
    inputs first, then optional - skipping socket-only inputs (MODEL, CLIP,
    LATENT, IMAGE, CONDITIONING, ...). This is the order ComfyUI's own editor
    assigns a node's `widgets_values` entries in, so it's the order this
    converter must consume them in too."""
    input_defs = class_info.get("input") if isinstance(class_info, dict) else None
    if not isinstance(input_defs, dict):
        return []
    ordered: List[Tuple[str, Dict[str, Any]]] = []
    for section in ("required", "optional"):
        section_defs = input_defs.get(section)
        if not isinstance(section_defs, dict):
            continue
        for name, spec in section_defs.items():
            if not isinstance(spec, (list, tuple)) or not spec:
                continue
            type_spec = spec[0]
            config = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if isinstance(type_spec, list) or type_spec in _WIDGET_SCALAR_TYPES:
                ordered.append((name, config))
    return ordered


def _converted_widget_names(node: Dict[str, Any]) -> Set[str]:
    """Names of widgets this node converted to a socket input in the UI -
    these no longer consume a `widgets_values` slot; their value comes from
    the connection instead (see `_resolve_source`). A recent ComfyUI export
    lists *every* widget in `inputs[]` with a `widget: {name}` marker, not
    only converted ones - `link` is what tells the two apart: a converted
    widget carries a real link id, a plain (unconverted) one carries `None`
    (see `_exported_widget_names`, which reads exactly those)."""
    names: Set[str] = set()
    for socket in node.get("inputs") or []:
        if not isinstance(socket, dict):
            continue
        widget = socket.get("widget")
        if isinstance(widget, dict) and widget.get("name") and socket.get("link") is not None:
            names.add(widget["name"])
    return names


def _exported_widget_names(node: Dict[str, Any]) -> List[str]:
    """The node's own widget names, in `widgets_values` order, as recorded
    directly in its `inputs[]` list by a recent ComfyUI frontend - every
    plain (unconverted, `link: null`) entry carrying a `widget: {name}`
    marker. Empty for an older export that never recorded this, in which
    case `/object_info` is the only source left (see `_resolve_widget_names`)."""
    names: List[str] = []
    for socket in node.get("inputs") or []:
        if not isinstance(socket, dict):
            continue
        widget = socket.get("widget")
        if isinstance(widget, dict) and widget.get("name") and socket.get("link") is None:
            names.append(widget["name"])
    return names


def _resolve_widget_names(
    node: Dict[str, Any], class_info: Optional[Dict[str, Any]]
) -> Tuple[List[str], Set[str]]:
    """`(names, control_after_generate_names)` for mapping this node's
    `widgets_values` to input names - preferring the names the export
    itself recorded (most reliable: exactly what built this graph) over
    re-deriving them from `/object_info`'s declared order, and falling back
    to positional `widget_N` placeholders when neither source has anything
    (an older export of a class `/object_info` also doesn't know)."""
    names = _exported_widget_names(node)
    if not names and class_info is not None:
        names = [name for name, _config in _widget_input_order(class_info)]

    control_after_generate: Set[str] = set()
    if class_info is not None:
        control_after_generate = {
            name for name, config in _widget_input_order(class_info) if config.get("control_after_generate")
        }
    elif names:
        # No /object_info entry to consult at all - the only signal left for
        # "this widget has a paired randomize/fixed control" is its name.
        control_after_generate = {name for name in names if name in _SEED_LIKE_WIDGET_NAMES}

    if not names:
        widgets_values = node.get("widgets_values")
        if isinstance(widgets_values, list):
            names = [f"widget_{i}" for i in range(len(widgets_values))]
        elif isinstance(widgets_values, dict):
            names = list(widgets_values.keys())

    return names, control_after_generate


def _socket_link(sockets: Any, slot: Optional[int]) -> Optional[Any]:
    """The `link` id at positional `slot` in a node's (or instance's) own
    `inputs`/`outputs`-shaped socket list - ComfyUI numbers sockets by their
    position in that list, both for an ordinary node and for a subgraph
    instance (whose socket list mirrors its subgraph definition's own
    `inputs`/`outputs`, in order)."""
    if not isinstance(sockets, list) or slot is None or not (0 <= slot < len(sockets)):
        return None
    entry = sockets[slot]
    return entry.get("link") if isinstance(entry, dict) else None


def _widget_overrides_for_instance(
    instance_node: Dict[str, Any], sg_def: Dict[str, Any]
) -> Dict[Tuple[Any, str], Any]:
    """`{(inner_local_node_id, widget_name): value}` for every widget the
    subgraph promotes to the instance's own `widgets_values` - see the
    module docstring's note on the assumed `subgraph.widgets` shape this
    reads. Empty (not an error) if that shape isn't present."""
    promoted = sg_def.get("widgets")
    values = instance_node.get("widgets_values")
    if not isinstance(promoted, list) or not isinstance(values, list):
        return {}
    overrides: Dict[Tuple[Any, str], Any] = {}
    for spec, value in zip(promoted, values):
        if isinstance(spec, dict) and "node_id" in spec and "name" in spec:
            overrides[(spec["node_id"], spec["name"])] = value
    return overrides


class _Scope:
    """One graph level: either the outer workflow, or the body of one
    subgraph instance. `full_id` builds the final flat node id a local id in
    this scope becomes - `<parent's prefix>:<local_id>`, chaining through
    nested instances - and `ancestry` (the set of subgraph ids already
    entered to reach this scope) is what catches a self-referencing
    subgraph as a cycle rather than infinite recursion."""

    __slots__ = (
        "nodes_by_id", "links_by_id", "prefix", "parent", "instance_node",
        "widget_overrides", "ancestry",
    )

    def __init__(
        self,
        nodes: Any,
        links: Any,
        prefix: str,
        parent: Optional["_Scope"] = None,
        instance_node: Optional[Dict[str, Any]] = None,
        widget_overrides: Optional[Dict[Tuple[Any, str], Any]] = None,
        ancestry: frozenset = frozenset(),
    ) -> None:
        self.nodes_by_id: Dict[Any, Dict[str, Any]] = {
            n["id"]: n for n in (nodes or []) if isinstance(n, dict) and "id" in n
        }
        self.links_by_id: Dict[Any, Tuple[Any, int, Any, int]] = {}
        for link in links or []:
            if isinstance(link, list) and len(link) >= 5:
                link_id, origin_id, origin_slot, target_id, target_slot = link[:5]
                self.links_by_id[link_id] = (origin_id, origin_slot, target_id, target_slot)
        self.prefix = prefix
        self.parent = parent
        self.instance_node = instance_node
        self.widget_overrides = widget_overrides or {}
        self.ancestry = ancestry

    def full_id(self, local_id: Any) -> str:
        return f"{self.prefix}:{local_id}" if self.prefix else str(local_id)


def _link_to_output_boundary(scope: _Scope, output_slot: int) -> Optional[Any]:
    """The inner link, within a subgraph body's own link table, that feeds
    its `output_slot`-th exposed output (i.e. the link ending at the
    virtual output boundary's that slot)."""
    for link_id, (_origin_id, _origin_slot, target_id, target_slot) in scope.links_by_id.items():
        if target_id == _VIRTUAL_OUTPUT_ID and target_slot == output_slot:
            return link_id
    return None


@dataclass
class ConvertedGraph:
    """The result of converting a UI-format workflow: `prompt` is the
    Export (API)-shaped dict `parser.parse_api_workflow` expects;
    `unknown_nodes` lists every node (post subgraph-flattening, so ids
    already match `prompt`'s keys) whose class wasn't found in the
    `object_info` this conversion was given - see the module docstring."""

    prompt: Dict[str, Any]
    unknown_nodes: List[Dict[str, Any]] = field(default_factory=list)


def convert_graph(ui_workflow: Dict[str, Any], object_info: Dict[str, Any]) -> ConvertedGraph:
    """Convert a UI-format ComfyUI workflow into the Export (API) shape
    `parser.parse_api_workflow` expects, using `object_info` (a live
    server's `GET /object_info`) to know, per node class, which
    `widgets_values` slot is which input. Subgraphs are flattened, not
    rejected - see the module docstring.

    Raises `WorkflowFormatError` only for a subgraph cycle, or a workflow
    with no usable nodes at all - a node class `object_info` doesn't
    recognize converts best-effort instead of failing (see
    `ConvertedGraph.unknown_nodes`)."""
    raw_nodes = ui_workflow.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise WorkflowFormatError("No nodes found in this UI-format ComfyUI workflow.")

    subgraphs_by_id: Dict[Any, Dict[str, Any]] = {
        sg["id"]: sg
        for sg in ((ui_workflow.get("definitions") or {}).get("subgraphs") or [])
        if isinstance(sg, dict) and sg.get("id")
    }

    outer_scope = _Scope(nodes=raw_nodes, links=ui_workflow.get("links"), prefix="")

    child_scopes: Dict[int, _Scope] = {}  # id(instance_node) -> its body scope, built once

    def child_scope(instance_node: Dict[str, Any], parent_scope: _Scope) -> _Scope:
        cache_key = id(instance_node)
        cached = child_scopes.get(cache_key)
        if cached is not None:
            return cached
        sg_def = subgraphs_by_id[instance_node["type"]]
        sg_id = sg_def["id"]
        if sg_id in parent_scope.ancestry:
            raise WorkflowFormatError(
                f"Subgraph '{sg_def.get('name', sg_id)}' contains an instance of itself - "
                "export it with Workflow -> Export (API) instead."
            )
        scope = _Scope(
            nodes=sg_def.get("nodes"),
            links=sg_def.get("links"),
            prefix=parent_scope.full_id(instance_node["id"]),
            parent=parent_scope,
            instance_node=instance_node,
            widget_overrides=_widget_overrides_for_instance(instance_node, sg_def),
            ancestry=parent_scope.ancestry | {sg_id},
        )
        child_scopes[cache_key] = scope
        return scope

    def resolve_source(
        link_id: Optional[Any], scope: _Scope, seen: Optional[Set[Tuple[int, Any]]] = None
    ) -> Optional[Dict[str, Any]]:
        """Follow one input socket's `link` id back to the node/slot that
        really produces the value: transparently walking a chain of
        `Reroute` nodes, resolving a `PrimitiveNode` to its literal value,
        crossing out of a subgraph instance's input boundary to whatever
        feeds it in the parent scope, and crossing into a subgraph
        instance's output boundary to whatever produces it inside - none of
        `Reroute`, `PrimitiveNode`, or a subgraph instance is itself a real,
        executable API node, so none is ever emitted.

        Returns `{"kind": "node", "node_id": str, "slot": int}`,
        `{"kind": "literal", "value": Any}`, or `None` if the link doesn't
        resolve to anything (dangling link, or a cycle)."""
        if link_id is None:
            return None
        active = seen if seen is not None else set()
        seen_key = (id(scope), link_id)
        if seen_key in active or link_id not in scope.links_by_id:
            return None
        active.add(seen_key)

        origin_id, origin_slot, _target_id, _target_slot = scope.links_by_id[link_id]

        if origin_id == _VIRTUAL_INPUT_ID:
            if scope.parent is None or scope.instance_node is None:
                return None
            outer_link_id = _socket_link(scope.instance_node.get("inputs"), origin_slot)
            return resolve_source(outer_link_id, scope.parent, active)

        origin_node = scope.nodes_by_id.get(origin_id)
        if origin_node is None:
            return None

        node_type = origin_node.get("type")
        if node_type == "Reroute":
            upstream_link = _socket_link(origin_node.get("inputs"), 0)
            return resolve_source(upstream_link, scope, active)
        if node_type == "PrimitiveNode":
            values = origin_node.get("widgets_values")
            value = values[0] if isinstance(values, list) and values else None
            return {"kind": "literal", "value": value}
        if node_type in subgraphs_by_id:
            inner_scope = child_scope(origin_node, scope)
            exit_link_id = _link_to_output_boundary(inner_scope, origin_slot)
            return resolve_source(exit_link_id, inner_scope, active)

        return {"kind": "node", "node_id": scope.full_id(origin_id), "slot": origin_slot}

    prompt: Dict[str, Any] = {}
    unknown_nodes: List[Dict[str, Any]] = []

    def emit(scope: _Scope) -> None:
        for local_id, node in scope.nodes_by_id.items():
            if local_id in (_VIRTUAL_INPUT_ID, _VIRTUAL_OUTPUT_ID):
                continue
            if node.get("mode", 0) in _SKIPPED_MODES:
                continue
            node_type = node.get("type")
            if node_type is None:
                continue
            if node_type in subgraphs_by_id:
                emit(child_scope(node, scope))  # the instance itself is never emitted
                continue
            if node_type in _PASSTHROUGH_TYPES:
                continue

            node_id = scope.full_id(local_id)
            class_info = object_info.get(node_type)
            if not isinstance(class_info, dict):
                class_info = None
                unknown_nodes.append(
                    {"node_id": node_id, "class_type": node_type, "title": node.get("title")}
                )

            inputs: Dict[str, Any] = {}
            converted = _converted_widget_names(node)

            for socket in node.get("inputs") or []:
                if not isinstance(socket, dict):
                    continue
                name = socket.get("name")
                if not name:
                    continue
                resolved = resolve_source(socket.get("link"), scope)
                if resolved is None:
                    continue
                if resolved["kind"] == "literal":
                    inputs[name] = resolved["value"]
                else:
                    inputs[name] = [resolved["node_id"], resolved["slot"]]

            names, control_after_generate = _resolve_widget_names(node, class_info)
            widgets_values = node.get("widgets_values")
            if isinstance(widgets_values, dict):
                for name in names:
                    if name in converted or name not in widgets_values:
                        continue
                    inputs[name] = widgets_values[name]
            else:
                values = list(widgets_values) if isinstance(widgets_values, list) else []
                idx = 0
                for name in names:
                    if name in converted:
                        continue
                    if idx >= len(values):
                        break
                    inputs[name] = values[idx]
                    idx += 1
                    if name in control_after_generate:
                        idx += 1  # the paired "randomize"/"fixed" selector - never a real input

            for (override_node_id, override_name), override_value in scope.widget_overrides.items():
                if override_node_id == local_id:
                    inputs[override_name] = override_value

            prompt[node_id] = {
                "class_type": node_type,
                "inputs": inputs,
                "_meta": {"title": node.get("title") or node_type},
            }

    emit(outer_scope)

    if not prompt:
        raise WorkflowFormatError("No usable nodes found after converting this UI-format workflow.")

    return ConvertedGraph(prompt=prompt, unknown_nodes=unknown_nodes)


def graph_to_prompt(ui_workflow: Dict[str, Any], object_info: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience wrapper over `convert_graph` for a caller that only wants
    the converted prompt, not the `unknown_nodes` report."""
    return convert_graph(ui_workflow, object_info).prompt


def extract_node_groups(ui_workflow: Dict[str, Any]) -> Dict[str, str]:
    """`{node_id: group_title}` for every node whose `pos` falls inside one
    of the UI workflow's `groups[].bounding` boxes - used to suggest one
    preset tab per ComfyUI group (see `suggest.suggest_fields`'s
    `node_groups` parameter). Empty if the workflow has no groups. Only the
    outer graph's own nodes are considered - a group drawn around a
    subgraph instance tags that instance's position, not its (flattened)
    inner nodes, which is out of scope here."""
    groups = ui_workflow.get("groups") or []
    boxes: List[Tuple[str, float, float, float, float]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        title = group.get("title")
        bounding = group.get("bounding")
        if not title or not isinstance(bounding, (list, tuple)) or len(bounding) < 4:
            continue
        x, y, w, h = bounding[0], bounding[1], bounding[2], bounding[3]
        boxes.append((title, x, y, x + w, y + h))
    if not boxes:
        return {}

    mapping: Dict[str, str] = {}
    for node in ui_workflow.get("nodes") or []:
        if not isinstance(node, dict) or "id" not in node:
            continue
        pos = node.get("pos")
        if isinstance(pos, dict):
            px, py = pos.get("0"), pos.get("1")
        elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
            px, py = pos[0], pos[1]
        else:
            continue
        if px is None or py is None:
            continue
        for title, x0, y0, x1, y1 in boxes:
            if x0 <= px <= x1 and y0 <= py <= y1:
                mapping[str(node["id"])] = title
                break
    return mapping
