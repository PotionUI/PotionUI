"""Base ABC for automation trigger sources."""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict


class TriggerSource(ABC):
    """
    A running trigger for one (automation_id, node_id) pair.

    Implementations own whatever background resource fires the trigger
    (a watchdog observer, a schedule loop, a hook dispatcher subscription,
    a polling task) and must call `enqueue` with the event payload when it
    fires - never execute the automation inline (see plan risk #3).
    """

    def __init__(self, automation_id: str, node_id: str, config: Dict[str, Any],
                 enqueue: Callable[[str, str, Dict[str, Any]], None]):
        self.automation_id = automation_id
        self.node_id = node_id
        self.config = config
        self._enqueue = enqueue

    def fire(self, payload: Dict[str, Any]) -> None:
        """Call from subclasses when the trigger condition is met."""
        self._enqueue(self.automation_id, self.node_id, payload)

    @abstractmethod
    async def start(self) -> None:
        """Begin watching/polling/subscribing. Must not block."""
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        """Stop watching/polling/subscribing and release any resources."""
        raise NotImplementedError
