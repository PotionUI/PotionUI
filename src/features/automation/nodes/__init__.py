"""
Registers every core (non-plugin) automation node type onto the shared
`node_type_registry`.

Imported once for side-effect registration by `src/bootstrap/container.py`, next
to `register_builtin_fields` - mirrors that module's pattern.
"""

from src.features.automation.nodes import actions, conditions, triggers


def register_builtin_nodes(registry=None) -> None:
    """Register the core trigger/condition/action node types onto `registry` (defaults to the singleton)."""
    from src.platform.plugins.automation_nodes import node_type_registry as _default_registry
    target = registry or _default_registry

    triggers.register(target)
    conditions.register(target)
    actions.register(target)


# Side-effect registration against the module-level singleton, mirroring how
# other registry-driven subsystems (fields, output types) register at import
# time so callers that only need `import src.features.automation.nodes` get the
# builtins for free. The composition root can still call `register_builtin_nodes`
# explicitly against a fresh registry (e.g. in tests).
register_builtin_nodes()
