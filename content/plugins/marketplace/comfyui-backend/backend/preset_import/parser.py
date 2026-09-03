"""Parsing a ComfyUI Export (API) workflow JSON into a typed graph.

The API format is a flat mapping of node id -> {class_type, inputs, _meta}.
Node ids are normally digit strings ("3", "42") but a workflow containing a
ComfyUI subgraph uses compound ids like "91:65" - these are opaque strings
throughout this module, never parsed as integers.

Each node's `inputs` mixes two kinds of entries: a literal value (str, int,
float, bool, or a list value the node treats as an option, e.g. an image
batch list) and a connection, which is always a 2-element `[source_node_id,
output_index]` list where the first element is a string and the second an
int. `WorkflowNode.connections()` / `literals()` split the two apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class WorkflowFormatError(ValueError):
    """The given JSON isn't an Export (API) format ComfyUI workflow."""


def _is_connection(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
    )


@dataclass
class WorkflowNode:
    id: str
    class_type: str
    title: Optional[str]
    inputs: Dict[str, Any] = field(default_factory=dict)

    def connections(self) -> Dict[str, Tuple[str, int]]:
        """`{input_name: (source_node_id, output_index)}` for every input that
        is a connection to another node's output."""
        return {
            name: (value[0], value[1])
            for name, value in self.inputs.items()
            if _is_connection(value)
        }

    def literals(self) -> Dict[str, Any]:
        """`{input_name: value}` for every input that is a plain value, not a
        connection to another node."""
        return {name: value for name, value in self.inputs.items() if not _is_connection(value)}

    def connection_source(self, input_name: str) -> Optional[Tuple[str, int]]:
        value = self.inputs.get(input_name)
        return (value[0], value[1]) if _is_connection(value) else None


@dataclass
class Workflow:
    """A parsed Export (API) workflow. `nodes` preserves the source JSON's
    key order (node ids as authored, including subgraph ids like "91:65").

    `unknown_nodes` - only ever non-empty for a workflow that came in as a
    UI-format import (see `convert.convert_graph`) - lists every node whose
    class wasn't found in the resolved backend's `/object_info`: the node
    itself is still fully present in `nodes` (link inputs resolved as
    normal, widget inputs under best-effort names), this is purely a
    surfacing list for the analyze/import responses to warn with."""

    nodes: Dict[str, WorkflowNode]
    unknown_nodes: List[Dict[str, Any]] = field(default_factory=list)

    def node(self, node_id: str) -> Optional[WorkflowNode]:
        return self.nodes.get(node_id)

    def find_by_class(self, *class_types: str) -> List[WorkflowNode]:
        wanted = set(class_types)
        return [n for n in self.nodes.values() if n.class_type in wanted]

    def find_by_class_prefix(self, prefix: str) -> List[WorkflowNode]:
        return [n for n in self.nodes.values() if n.class_type.startswith(prefix)]

    def resolve(self, connection: Tuple[str, int]) -> Optional[WorkflowNode]:
        return self.nodes.get(connection[0])


def is_ui_format(data: Dict[str, Any]) -> bool:
    """The ComfyUI UI export (`Workflow > Export`, not `Export (API)`) is a
    single object with top-level `nodes`/`links` arrays, not a node-id keyed
    mapping. `groups`, `last_node_id`, etc. may also be present but nodes/
    links are the two structural giveaways an API-format workflow never has
    (an API-format node's own value is always an object with `class_type`)."""
    if isinstance(data.get("nodes"), list) or isinstance(data.get("links"), list):
        return True
    return False


def parse_api_workflow(data: Dict[str, Any]) -> Workflow:
    """Parse a ComfyUI Export (API) workflow JSON into a `Workflow`.

    Raises `WorkflowFormatError` if `data` is the UI export format (`Workflow
    > Export`) instead, or isn't a recognizable ComfyUI workflow at all.
    """
    if not isinstance(data, dict) or not data:
        raise WorkflowFormatError("Expected a non-empty ComfyUI workflow JSON object.")

    if is_ui_format(data):
        raise WorkflowFormatError(
            "This is the UI export; use Workflow -> Export (API) in ComfyUI and upload that file instead."
        )

    nodes: Dict[str, WorkflowNode] = {}
    for node_id, node_data in data.items():
        if not isinstance(node_data, dict) or "class_type" not in node_data:
            continue
        title = None
        meta = node_data.get("_meta")
        if isinstance(meta, dict):
            title = meta.get("title")
        inputs = node_data.get("inputs")
        nodes[str(node_id)] = WorkflowNode(
            id=str(node_id),
            class_type=str(node_data["class_type"]),
            title=title,
            inputs=dict(inputs) if isinstance(inputs, dict) else {},
        )

    if not nodes:
        raise WorkflowFormatError(
            "No ComfyUI nodes found. Expected an Export (API) workflow: an object mapping "
            "node ids to {class_type, inputs}."
        )

    return Workflow(nodes=nodes)


def parse_workflow(data: Dict[str, Any], object_info: Optional[Dict[str, Any]] = None) -> Workflow:
    """Parse either workflow shape a ComfyUI export can be in.

    An API-format workflow parses exactly as `parse_api_workflow` always
    has, `object_info` unused either way. A UI-format workflow (`Workflow ->
    Export`) is converted to the API shape first via
    `backend.preset_import.convert.convert_graph`, which uses `object_info`
    (a live server's `GET /object_info`) to map each node's positional
    `widgets_values` back onto its input names - so a UI-format workflow
    with no `object_info` given raises the same rejection message
    `parse_api_workflow` always has, rather than attempting a conversion
    that can't be done correctly without a backend to ask at all. A node
    class `object_info` doesn't recognize (an uninstalled custom node pack)
    is not the same failure - that node still converts, best-effort, and is
    listed in the returned `Workflow.unknown_nodes` instead.
    """
    if not isinstance(data, dict) or not data:
        raise WorkflowFormatError("Expected a non-empty ComfyUI workflow JSON object.")

    if is_ui_format(data):
        if object_info is None:
            raise WorkflowFormatError(
                "This is the UI export; use Workflow -> Export (API) in ComfyUI and upload that "
                "file instead."
            )
        from .convert import convert_graph  # local import: avoids a module-load cycle

        converted = convert_graph(data, object_info)
        workflow = parse_api_workflow(converted.prompt)
        workflow.unknown_nodes = converted.unknown_nodes
        return workflow

    return parse_api_workflow(data)
