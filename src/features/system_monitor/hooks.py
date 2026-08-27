"""Hook points owned by the system monitor domain."""

from src.platform.plugins.hooks import hooks_registry

SYSTEM_MONITOR_HOOKS = hooks_registry.declare(
    "system_monitor", "backend",
    "before_stats", "after_stats",
    "interval_changed",
    specs={
        "before_stats": {
            "description": "Fired at the start of every stats collection cycle (both polled API reads and the background broadcast loop). Empty payload; can only block collection.",
            "payload": {},
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Temporarily disabling stats collection (e.g. during a heavy GPU operation you don't want to be measured/interrupted by)"],
        },
        "after_stats": {
            "description": "Fired after GPU/RAM/CPU stats have been collected and formatted, before they're returned to the caller/broadcast. Can rewrite `stats` in place.",
            "payload": {
                "stats": {
                    "type": "dict",
                    "description": "{'timestamp': float, 'gpu': {...}, 'ram': {...}, 'cpu': {...}} - gpu/ram/cpu are each {'available': bool, ...usage fields} built by SystemMonitorCoordinator._format_gpu_stats/_format_ram_stats/_format_cpu_stats from src/platform/observability/system_probe.py's snapshot",
                },
            },
            "mutable": ["stats"],
            "use_when": [
                "Injecting additional metrics into the stats payload before it reaches the frontend/API consumer",
                "Redacting or capping reported values",
            ],
        },
        "interval_changed": {
            "description": "Fired when the monitoring poll interval is changed via set_monitoring_interval. Notification-only.",
            "payload": {
                "old_interval": {"type": "float", "description": "Previous interval in seconds"},
                "new_interval": {"type": "float", "description": "New interval in seconds (0.1-60)"},
            },
            "mutable": [],
            "use_when": ["Reacting to a change in monitoring cadence, e.g. adjusting a plugin's own polling to match"],
        },
    },
)
