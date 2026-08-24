"""Contributing an automation node.

A plugin declares its node types in `manifest.yml` under `automation_nodes:`,
each pointing a `handler:` at a `module.function` whose signature is
`async def handler(ctx) -> NodeResult`. The handler receives a
`NodeExecutionContext` (config, upstream outputs, the triggering event, and the
injected service bundle) and returns a `NodeResult` carrying the data it hands
to downstream nodes.
"""

from src.features.automation.context import NodeExecutionContext
from src.platform.plugins.automation_nodes import NodeField, NodeResult

__all__ = [
    "NodeExecutionContext",
    "NodeField",
    "NodeResult",
]
