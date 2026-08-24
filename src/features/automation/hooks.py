"""Hook points owned by the automation domain."""

from src.platform.plugins.hooks import hooks_registry

AUTOMATION_HOOKS = hooks_registry.declare(
    "automation", "backend",
    "before_run", "after_run",
    "node.before_execute", "node.after_execute",
    specs={
        "before_run": {
            "description": "Fired just before an automation run starts walking its graph, after the run row is persisted.",
            "payload": {
                "automation_id": {"type": "str", "description": "Automation being run"},
                "run_id": {"type": "str", "description": "ID of the newly created automation_runs row"},
                "trigger_node_id": {"type": "Optional[str]", "description": "Node ID of the trigger that fired, if known"},
                "trigger_type": {"type": "Optional[str]", "description": "Node type key of the trigger (e.g. 'trigger.filesystem')"},
                "event_payload": {"type": "dict", "description": "Payload produced by the trigger, becomes the trigger node's output"},
            },
            "use_when": [
                "Audit/log automation runs before they execute",
                "Veto is not supported here - use node.before_execute on individual action nodes instead",
            ],
        },
        "after_run": {
            "description": "Fired when an automation run reaches a terminal state (success/failed/cancelled).",
            "payload": {
                "automation_id": {"type": "str", "description": "Automation that ran"},
                "run_id": {"type": "str", "description": "ID of the automation_runs row"},
                "status": {"type": "str", "description": "Terminal run status"},
                "duration_ms": {"type": "Optional[int]", "description": "Wall-clock run duration in milliseconds"},
                "error": {"type": "Optional[str]", "description": "Error message if the run failed"},
            },
            "use_when": [
                "Send a notification or metrics event when a run finishes",
            ],
        },
        "node.before_execute": {
            "description": "Fired immediately before a condition/action node executes.",
            "payload": {
                "automation_id": {"type": "str", "description": "Owning automation"},
                "run_id": {"type": "str", "description": "Owning run"},
                "node_id": {"type": "str", "description": "Graph node id"},
                "node_type": {"type": "str", "description": "Registered node type key"},
                "config": {"type": "dict", "description": "Node's resolved config"},
            },
            "mutable": ["config"],
            "use_when": [
                "Inject/override config for a specific node type before it executes",
            ],
        },
        "node.after_execute": {
            "description": "Fired immediately after a condition/action node finishes executing.",
            "payload": {
                "automation_id": {"type": "str", "description": "Owning automation"},
                "run_id": {"type": "str", "description": "Owning run"},
                "node_id": {"type": "str", "description": "Graph node id"},
                "node_type": {"type": "str", "description": "Registered node type key"},
                "output": {"type": "Any", "description": "The node's NodeResult.output value"},
                "duration_ms": {"type": "int", "description": "Milliseconds spent executing the node"},
            },
            "use_when": [
                "Post-process or record per-node outputs/timing",
            ],
        },
    },
)
