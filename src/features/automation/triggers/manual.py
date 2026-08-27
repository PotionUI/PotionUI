"""`trigger.manual` - no background loop; fired directly via `AutomationRuntime.run_now()`."""

from typing import Any, Dict

from src.features.automation.triggers.base import TriggerSource


class ManualTrigger(TriggerSource):
    """`trigger.manual` node. `start`/`stop` are no-ops - there is nothing to watch."""

    def __init__(self, automation_id: str, node_id: str, config: Dict[str, Any], enqueue):
        super().__init__(automation_id, node_id, config, enqueue)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None
