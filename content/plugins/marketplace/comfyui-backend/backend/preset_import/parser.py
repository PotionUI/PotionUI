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
    key order (node ids as authored, including subgraph ids like "91:65")."""

    nodes: Dict[str, WorkflowNode]

    def node(self, node_id: str) -> Optional[WorkflowNode]:
        return self.nodes.get(node_id)

    def find_by_class(self, *class_types: str) -> List[WorkflowNode]:
        wanted = set(class_types)
        return [n for n in self.nodes.values() if n.class_type in wanted]

    def find_by_class_prefix(self, prefix: str) -> List[WorkflowNode]:
        return [n for n in self.nodes.values() if n.class_type.startswith(prefix)]

    def resolve(self, connection: Tuple[str, int]) -> Optional[WorkflowNode]:
        return self.nodes.get(connection[0])


def _looks_like_ui_format(data: Dict[str, Any]) -> bool:
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

    if _looks_like_ui_format(data):
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
