"""
Automation node-type registry.

Every trigger/condition/action an automation graph can use is declared once as a
`NodeTypeSpec` and registered onto the shared `node_type_registry` singleton.
This is a plugin extension point, like the field-type registry next to it: the
node types the application ships are registered at import time by
`src.features.automation.nodes` (imported for side-effect by the composition
root), and plugins register their own through the manifest `automation_nodes:`
section and `PluginRegistry`, removed again via `unregister_source` on disable.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

NodeKind = str  # "trigger" | "condition" | "action"


class DuplicateNodeTypeError(ValueError):
    """Raised when registering a node type key that is already registered."""


@dataclass(frozen=True)
class NodePort:
    """A single output port on a node (name shown on the canvas edge handle)."""
    key: str
    label: str = ""


@dataclass(frozen=True)
class NodeField:
    """
    One field a node emits, i.e. one key of `NodeResult.output`.

    For trigger node types this instead declares one key of the EVENT payload
    the trigger fires, because the engine seeds `upstream[trigger_node_id] =
    event_payload` (see `AutomationEngine.run`) - so a trigger's declared
    outputs are exactly what downstream nodes read as `event.*`.

    This is the *authored* contract. `tests/core/automation/test_outputs_contract.py`
    executes every condition/action against fakes and asserts the real output
    dict's keys match the declaration, so the two cannot drift apart.
    """
    key: str
    type: str = "any"  # "string"|"number"|"boolean"|"array"|"object"|"any"
    label: str = ""
    description: str = ""
    example: Any = None


@dataclass
class NodeResult:
    """Result of executing a condition/action node."""
    output: Any = None
    # Not just "true"/"false"/"out" - dynamic-port node types (e.g.
    # `condition.switch`) return arbitrary case labels or "default".
    branch: Optional[str] = "out"
    waiting: bool = False
    # Fan-out payload (e.g. `action.scan_files`' one dict per file). `None`
    # (the default) means "ordinary single-payload node" - the engine walks
    # this node's downstream edges exactly as it always has. A non-`None`
    # list - including an EMPTY one - switches the engine into fan-out mode
    # for this node's downstream subtree: it runs once per item (zero times
    # for an empty list), with `upstream[this_node_id]` replaced by that
    # item's dict for the duration of that item's walk. See
    # `AutomationEngine._walk_fanout`.
    items: Optional[List[Dict[str, Any]]] = None


@dataclass(frozen=True)
class NodeTypeSpec:
    """Declaration for a single automation node type."""

    key: str  # e.g. "trigger.filesystem", "condition.compare", "action.add_tag"
    kind: NodeKind  # "trigger" | "condition" | "action"
    title: str
    description: str = ""
    icon: str = ""
    category: str = "general"
    config_schema: List[Dict[str, Any]] = field(default_factory=list)
    output_ports: Tuple[str, ...] = ("out",)
    # The data contract: what this node hands to downstream nodes. Distinct
    # from `output_ports`, which are the *edge handles* (control flow). A node
    # has many output fields but usually one output port.
    outputs: Tuple[NodeField, ...] = ()
    # For fan-out node types (`NodeResult.items` set - see above), the shape
    # of ONE item, as opposed to `outputs`, which is this node's own
    # aggregate result. `upstream.<node_id>.*` resolves against `outputs`
    # outside a fan-out subtree and against `item_outputs` inside one (see
    # `AutomationEngine._walk_fanout`). Empty for every node type that never
    # sets `items`.
    item_outputs: Tuple[NodeField, ...] = ()
    # For payloads that aren't statically knowable: `trigger.manual` fires
    # whatever the caller passed to Run Now, and `trigger.hook_event` fires the
    # hook's own `context.data`, whose shape depends on the selected hook. Such
    # specs leave `outputs` empty and set this - the canvas then shows
    # "runtime-defined" rather than an empty list that reads as "emits nothing".
    dynamic_outputs: bool = False
    execute: Optional[Callable[[Any], Awaitable["NodeResult"]]] = None
    start: Optional[Callable[..., Awaitable[None]]] = None
    stop: Optional[Callable[..., Awaitable[None]]] = None
    source: str = "core"
    # Admin-grade node: a graph that uses it may only be authored, enabled, or
    # manually run by an administrator. There is no per-run user at execution
    # time (triggers fire detached), so the gate is enforced where a graph
    # enters the system or becomes runnable - see AutomationController. Used
    # by actions that perform privileged host operations (e.g.
    # action.backend_action's Clear VRAM / Restart Backend).
    requires_admin: bool = False
    # When set, this node's REAL output ports aren't the static `output_ports`
    # above - they're derived per node-instance from that instance's own
    # config value under this key (see `parse_dynamic_port_labels` /
    # `resolve_dynamic_ports`). `condition.switch` sets this to "cases": a
    # comma-separated string in its own config becomes N case ports plus an
    # implicit trailing "default" port. Serialized into the node-types
    # catalog so the canvas editor can render ports for the current config
    # value instead of the (meaningless) static `output_ports`.
    dynamic_ports_config_key: Optional[str] = None

    def __post_init__(self):
        if self.kind == "condition" and self.output_ports == ("out",):
            object.__setattr__(self, "output_ports", ("true", "false"))


def parse_dynamic_port_labels(raw_value: Any) -> List[str]:
    """Comma-separated string -> trimmed, non-empty, order-preserving labels ('a, b ,,c' -> ['a','b','c'])."""
    if raw_value is None:
        return []
    return [part.strip() for part in str(raw_value).split(",") if part.strip()]


def resolve_dynamic_ports(spec: "NodeTypeSpec", config: Dict[str, Any]) -> Optional[Tuple[str, ...]]:
    """
    For a node type with `dynamic_ports_config_key` set, compute one node
    instance's actual output ports from its own config: the parsed labels
    plus an implicit trailing "default" port. Returns `None` for node types
    without dynamic ports - callers fall back to `spec.output_ports`.
    """
    if not spec.dynamic_ports_config_key:
        return None
    labels = parse_dynamic_port_labels(config.get(spec.dynamic_ports_config_key))
    return tuple(labels) + ("default",)


def resolved_config_schema(spec: "NodeTypeSpec") -> List[Dict[str, Any]]:
    """
    Resolve a node type's `config_schema` for external consumption (the
    `/api/automations/node-types` catalog): any field def carrying an
    `options_provider` (a zero-arg `Callable[[], List[{"value","label"}]]`,
    e.g. enumerating the app's configured directories or the live hooks
    catalog) is called here and its result inlined as a top-level `options`
    key on the field def - NOT nested under `configuration`. The frontend
    field components (`SelectField.svelte` et al.) read `config.options`
    directly; there is no `configuration:`-nesting convention for automation
    node fields the way preset `form.yml` fields have (see the module
    docstring in `nodes/triggers.py`). The callable itself is stripped from
    the returned copy - it must never reach `json.dumps`/the API response.
    """
    resolved = []
    for field_def in spec.config_schema:
        field_copy = dict(field_def)
        provider = field_copy.pop("options_provider", None)
        if provider is not None:
            field_copy["options"] = provider()
        resolved.append(field_copy)
    return resolved


class NodeTypeRegistry:
    """Registry mapping node type key -> `NodeTypeSpec`."""

    def __init__(self):
        self._by_key: Dict[str, NodeTypeSpec] = {}

    def register(self, spec: NodeTypeSpec) -> None:
        """Register a node type. Raises `DuplicateNodeTypeError` on key collision."""
        if spec.key in self._by_key:
            raise DuplicateNodeTypeError(f"Node type already registered: '{spec.key}'")
        self._by_key[spec.key] = spec

    def unregister_source(self, source: str) -> None:
        """Remove every node type registered by `source` (e.g. a plugin id)."""
        for key in [key for key, spec in self._by_key.items() if spec.source == source]:
            del self._by_key[key]

    def get(self, key: str) -> Optional[NodeTypeSpec]:
        """Look up a node type. Returns None for unknown keys (callers must check)."""
        return self._by_key.get(key)

    def all(self) -> List[NodeTypeSpec]:
        return list(self._by_key.values())

    def by_kind(self, kind: NodeKind) -> List[NodeTypeSpec]:
        return [spec for spec in self._by_key.values() if spec.kind == kind]


# Module-level singleton shared by the engine, manager, plugin enable/disable
# path, and the `/api/automations/node-types` catalog endpoint.
node_type_registry = NodeTypeRegistry()
