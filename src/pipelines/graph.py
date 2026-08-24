"""
Pipeline graph projection.

Builds a node/connection graph (used by the preset pipeline preview API) from
an already-processed pipe list - the SAME list produced by
`PipelineBuilder.build_pipeline(...).pipes` for real execution. This module
does no preset processing of its own; it is a pure projection, which is what
guarantees preview == execution by construction.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.pipelines.catalog import PipeCatalog

logger = logging.getLogger(__name__)


@dataclass
class PipeNode:
    """Represents a single node in the pipeline graph."""
    id: str
    name: str
    description: str
    enabled: bool
    position: Dict[str, int]
    inputs: List[Dict[str, Any]]
    outputs: List[Dict[str, Any]]
    configuration: Dict[str, Any]
    status: str  # 'available' or 'not_found'
    pipe_id: Optional[int]  # Index in enabled pipes list
    template_index: int  # Index in template list

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "position": self.position,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "configuration": self.configuration,
            "status": self.status,
            "pipe_id": self.pipe_id,
            "template_index": self.template_index
        }


@dataclass
class PipeConnection:
    """Represents a connection between two pipes."""
    id: str
    source_node: str
    source_output: str
    target_node: str
    target_input: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_node": self.source_node,
            "source_output": self.source_output,
            "target_node": self.target_node,
            "target_input": self.target_input
        }


@dataclass
class PipelineGraph:
    """Result of projecting a processed pipe list into a graph."""
    preset_id: str
    mode: str
    nodes: List[PipeNode] = field(default_factory=list)
    connections: List[PipeConnection] = field(default_factory=list)

    @property
    def debug_info(self) -> Dict[str, int]:
        return {
            'total_pipes': len(self.nodes),
            'available_pipes': len([n for n in self.nodes if n.status == 'available']),
            'missing_pipes': len([n for n in self.nodes if n.status == 'not_found']),
            'enabled_pipes': len([n for n in self.nodes if n.enabled]),
            'disabled_pipes': len([n for n in self.nodes if not n.enabled])
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            'preset_id': self.preset_id,
            'mode': self.mode,
            'nodes': [node.to_dict() for node in self.nodes],
            'connections': [conn.to_dict() for conn in self.connections],
            'debug_info': self.debug_info
        }


def build_graph(
    pipes: List[Dict[str, Any]],
    pipe_catalog: PipeCatalog,
    preset_id: str,
    mode: str
) -> PipelineGraph:
    """Project an already-processed pipe list into a PipelineGraph.

    Args:
        pipes: Canonical processed pipe configs, as produced by
            `PipelineBuilder.build_pipeline(...).pipes`.
        pipe_catalog: Registry for resolving pipe classes (inputs/outputs/description)
        preset_id: The preset id the pipes were built for (for the result header)
        mode: The mode the pipes were built for (for the result header)

    Returns:
        PipelineGraph containing nodes and connections
    """
    result = PipelineGraph(preset_id=preset_id, mode=mode)
    _build_nodes_and_connections(pipes, pipe_catalog, result)
    return result


def _build_nodes_and_connections(
    processed_pipes: List[Dict[str, Any]],
    pipe_catalog: PipeCatalog,
    result: PipelineGraph
) -> None:
    """Build nodes and connections from processed pipes.

    Args:
        processed_pipes: List of processed pipe configurations
        pipe_catalog: Registry for resolving pipe classes
        result: PipelineGraph to populate
    """
    pipe_id_counter = 0  # Only count enabled pipes

    for i, processed_pipe in enumerate(processed_pipes):
        pipe_name = processed_pipe['name']
        is_enabled = processed_pipe['enabled']

        # Get the pipe class from registry
        pipe_class = pipe_catalog.get_pipe(pipe_name)

        # Only enabled pipes get a pipe_id
        current_pipe_id = pipe_id_counter if is_enabled else None
        if is_enabled:
            pipe_id_counter += 1

        if not pipe_class:
            # If pipe class not found, create a basic node
            node = PipeNode(
                id=pipe_name,
                name=pipe_name,
                description=f"Pipe: {pipe_name}",
                enabled=is_enabled,
                position={"x": i * 200, "y": 0},
                inputs=[],
                outputs=[],
                configuration=processed_pipe['config'],
                status="not_found",
                pipe_id=current_pipe_id,
                template_index=i
            )
        else:
            # Get inputs and outputs from the pipe class
            inputs = [
                {
                    "name": spec.name,
                    "type": spec.io_type.value,
                    "required": spec.required,
                    "description": spec.description,
                    "is_array": spec.is_array
                }
                for spec in pipe_class.inputs()
            ]

            outputs = [
                {
                    "name": spec.name,
                    "type": spec.io_type.value,
                    "description": spec.description,
                    "is_array": spec.is_array
                }
                for spec in pipe_class.outputs()
            ]

            node = PipeNode(
                id=pipe_name,
                name=pipe_name,
                description=pipe_class.description,
                enabled=is_enabled,
                position={"x": i * 200, "y": 0},
                inputs=inputs,
                outputs=outputs,
                configuration=processed_pipe['config'],
                status="available",
                pipe_id=current_pipe_id,
                template_index=i
            )

        result.nodes.append(node)

        # Process input connections from the processed pipe
        for processed_input in processed_pipe.get('input', []):
            if processed_input.get('enabled', True):
                connection = PipeConnection(
                    id=f"{processed_input['provider']}_{processed_input['output_var']}_to_{pipe_name}_{processed_input['name']}",
                    source_node=processed_input['provider'],
                    source_output=processed_input['output_var'],
                    target_node=pipe_name,
                    target_input=processed_input['name']
                )
                result.connections.append(connection)
