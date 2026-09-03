"""Converting a ComfyUI **UI-format** workflow (`Workflow -> Export`, the
`{"nodes": [...], "links": [...], "groups": [...]}` shape ComfyUI's own
editor saves) into the same Export (API) shape `parser.parse_api_workflow`
already understands, mirroring what the ComfyUI frontend itself does when it
serializes a graph to send for execution.

This only works with a live server's `GET /object_info` in hand: a UI-format
node's `widgets_values` is a bare positional array with no input names
attached, so the only way to know which value belongs to which of a node
class's inputs is to ask the server what that class's inputs are declared
as, in order.

Deferred: bypassed nodes (`mode: 4`) are dropped exactly like muted nodes
(`mode: 2`) rather than having their inputs threaded through to whatever
they fed - a faithful bypass needs to match each output's type against a
downstream input's type, which is a materially bigger piece of graph work
than this first cut takes on.
"""

from __future__ import annotations

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


def _reject_subgraphs(ui_workflow: Dict[str, Any]) -> None:
    """Subgraphs are the newest UI-export shape (`definitions.subgraphs`,
    with a node's `type` naming a subgraph's UUID instead of a class_type) -
    converting one correctly means inlining the subgraph's own internal
    graph at each call site, which this first cut doesn't attempt."""
    definitions = ui_workflow.get("definitions")
    if isinstance(definitions, dict) and definitions.get("subgraphs"):
        raise WorkflowFormatError(
            "This workflow uses subgraphs; export it with Workflow -> Export (API) instead."
        )


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
    the connection instead (see `_resolve_source`)."""
    names: Set[str] = set()
    for socket in node.get("inputs") or []:
        if not isinstance(socket, dict):
            continue
        widget = socket.get("widget")
        if isinstance(widget, dict) and widget.get("name"):
            names.add(widget["name"])
    return names


def _resolve_source(
    link_id: Optional[int],
    links_by_id: Dict[Any, Tuple[Any, int, Any, int, Any]],
    nodes_by_id: Dict[Any, Dict[str, Any]],
    _seen: Optional[Set[Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Follow one input socket's `link` id back to the node/slot that really
    produces the value, transparently walking through any chain of `Reroute`
    nodes and resolving a `PrimitiveNode` to the literal value it carries -
    neither kind is itself a real, executable API node, so neither is ever
    emitted; the API graph wires straight past them.

    Returns `{"kind": "node", "node_id": str, "slot": int}`,
    `{"kind": "literal", "value": Any}`, or `None` if the link doesn't
    resolve to anything (dangling link, or a cycle of reroutes)."""
    if link_id is None:
        return None
    seen = _seen if _seen is not None else set()
    if link_id in seen or link_id not in links_by_id:
        return None
    seen.add(link_id)

    origin_id, origin_slot, _target_id, _target_slot, _type = links_by_id[link_id]
    origin_node = nodes_by_id.get(origin_id)
    if origin_node is None:
        return None

    node_type = origin_node.get("type")
    if node_type == "Reroute":
        upstream = origin_node.get("inputs") or []
        upstream_link = upstream[0].get("link") if upstream and isinstance(upstream[0], dict) else None
        return _resolve_source(upstream_link, links_by_id, nodes_by_id, seen)
    if node_type == "PrimitiveNode":
        values = origin_node.get("widgets_values")
        value = values[0] if isinstance(values, list) and values else None
        return {"kind": "literal", "value": value}

    return {"kind": "node", "node_id": str(origin_id), "slot": origin_slot}


def _node_class_type(node: Dict[str, Any]) -> Optional[str]:
    node_type = node.get("type")
    return node_type if isinstance(node_type, str) else None


def graph_to_prompt(ui_workflow: Dict[str, Any], object_info: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a UI-format ComfyUI workflow into the Export (API) shape
    `parser.parse_api_workflow` expects, using `object_info` (a live
    server's `GET /object_info`) to know, per node class, which
    `widgets_values` slot is which input.

    Raises `WorkflowFormatError` for a subgraph workflow, or a node class
    this `object_info` doesn't know about (an uninstalled custom node, or a
    server that doesn't match what the workflow was built against).
    """
    _reject_subgraphs(ui_workflow)

    raw_nodes = ui_workflow.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise WorkflowFormatError("No nodes found in this UI-format ComfyUI workflow.")

    nodes_by_id: Dict[Any, Dict[str, Any]] = {
        n["id"]: n for n in raw_nodes if isinstance(n, dict) and "id" in n
    }

    links_by_id: Dict[Any, Tuple[Any, int, Any, int, Any]] = {}
    for link in ui_workflow.get("links") or []:
        if isinstance(link, list) and len(link) >= 5:
            link_id, origin_id, origin_slot, target_id, target_slot = link[:5]
            link_type = link[5] if len(link) > 5 else None
            links_by_id[link_id] = (origin_id, origin_slot, target_id, target_slot, link_type)

    prompt: Dict[str, Any] = {}

    for node in raw_nodes:
        if not isinstance(node, dict) or "id" not in node:
            continue
        if node.get("mode", 0) in _SKIPPED_MODES:
            continue
        class_type = _node_class_type(node)
        if class_type is None or class_type in _PASSTHROUGH_TYPES:
            continue

        class_info = object_info.get(class_type)
        if not isinstance(class_info, dict):
            raise WorkflowFormatError(
                f"Node class '{class_type}' was not found on this ComfyUI server's /object_info; "
                "install the node pack that provides it, or export with Export (API) instead."
            )

        node_id = str(node["id"])
        inputs: Dict[str, Any] = {}
        converted = _converted_widget_names(node)

        for socket in node.get("inputs") or []:
            if not isinstance(socket, dict):
                continue
            name = socket.get("name")
            if not name:
                continue
            resolved = _resolve_source(socket.get("link"), links_by_id, nodes_by_id)
            if resolved is None:
                continue
            if resolved["kind"] == "literal":
                inputs[name] = resolved["value"]
            else:
                inputs[name] = [resolved["node_id"], resolved["slot"]]

        widgets_values = node.get("widgets_values")
        if isinstance(widgets_values, dict):
            for name, _config in _widget_input_order(class_info):
                if name in converted or name not in widgets_values:
                    continue
                inputs[name] = widgets_values[name]
        else:
            values = list(widgets_values) if isinstance(widgets_values, list) else []
            idx = 0
            for name, config in _widget_input_order(class_info):
                if name in converted:
                    continue
                if idx >= len(values):
                    break
                inputs[name] = values[idx]
                idx += 1
                if config.get("control_after_generate"):
                    idx += 1  # the paired "randomize"/"fixed" selector - never a real input

        entry: Dict[str, Any] = {"class_type": class_type, "inputs": inputs}
        entry["_meta"] = {"title": node.get("title") or class_type}
        prompt[node_id] = entry

    if not prompt:
        raise WorkflowFormatError("No usable nodes found after converting this UI-format workflow.")

    return prompt


def extract_node_groups(ui_workflow: Dict[str, Any]) -> Dict[str, str]:
    """`{node_id: group_title}` for every node whose `pos` falls inside one
    of the UI workflow's `groups[].bounding` boxes - used to suggest one
    preset tab per ComfyUI group (see `suggest.suggest_fields`'s
    `node_groups` parameter). Empty if the workflow has no groups."""
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
