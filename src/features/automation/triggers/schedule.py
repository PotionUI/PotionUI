"""`trigger.schedule` - cron (via `croniter`) or fixed-interval firing, one asyncio loop per node."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from croniter import croniter

from src.features.automation.triggers.base import TriggerSource

logger = logging.getLogger(__name__)


class ScheduleTrigger(TriggerSource):
    """
    `trigger.schedule` node.

    Config: `mode` ("cron" | "interval"), `cron` (croniter expression, cron mode),
    `interval_s` (seconds, interval mode).
    """

    def __init__(self, automation_id: str, node_id: str, config: Dict[str, Any], enqueue):
        super().__init__(automation_id, node_id, config, enqueue)
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        mode = self.config.get("mode", "interval")
        try:
            while not self._stopped.is_set():
                sleep_s = self._next_sleep_seconds(mode)
                if sleep_s is None:
                    logger.error(f"[SCHEDULE_TRIGGER] Invalid schedule config for node {self.node_id}: {self.config}")
                    return
                await asyncio.sleep(max(0.1, sleep_s))
                if self._stopped.is_set():
                    return
                self.fire({"fired_at": datetime.now().isoformat(), "mode": mode})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(f"[SCHEDULE_TRIGGER] Schedule loop for node {self.node_id} crashed", exc_info=True)

    def _next_sleep_seconds(self, mode: str) -> Optional[float]:
        if mode == "cron":
            expr = self.config.get("cron")
            if not expr:
                return None
            try:
                itr = croniter(expr, datetime.now())
                next_run = itr.get_next(datetime)
            except (ValueError, KeyError):
                return None
            return (next_run - datetime.now()).total_seconds()

        if mode == "interval":
            try:
                return float(self.config.get("interval_s", 60))
            except (TypeError, ValueError):
                return None

        return None
