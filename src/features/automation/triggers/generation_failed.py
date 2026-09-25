from typing import Any, Awaitable, Dict, Optional

from src.features.automation.triggers.hook_bridge import HookEventBridge, HookEventTrigger
from src.features.generation.hooks import GENERATION_HOOKS

PAYLOAD_FIELDS = (
    "generation_id",
    "user_id",
    "preset_id",
    "error_code",
    "category",
    "message",
    "failed_pipe",
)

FILTER_FIELDS = (
    ("category", "category"),
    ("preset_id", "preset_id"),
    ("user_id", "user_id"),
)


def _config_value(config: Dict[str, Any], key: str) -> str:
    value = config.get(key)
    return str(value).strip() if value is not None else ""


def matches_failure_filters(config: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    for config_key, payload_key in FILTER_FIELDS:
        wanted = _config_value(config, config_key)
        if wanted and wanted != str(payload.get(payload_key) or ""):
            return False
    return True


def failure_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {field: payload.get(field) for field in PAYLOAD_FIELDS}


class GenerationFailedTrigger(HookEventTrigger):

    def __init__(self, automation_id: str, node_id: str, config: Dict[str, Any],
                 enqueue, bridge: HookEventBridge):
        super().__init__(automation_id, node_id, {**config, "hook_name": GENERATION_HOOKS.failed},
                         enqueue, bridge)

    def dispatch(self, payload: Dict[str, Any]) -> Optional[Awaitable]:
        if matches_failure_filters(self.config, payload):
            self.fire(failure_event(payload))
        return None
