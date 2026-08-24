"""
`trigger.gpu_threshold` - polls `GpuManager.get_free_vram()` and fires once
when free VRAM crosses a threshold, with hysteresis so it doesn't refire on
every poll while sitting right at the boundary.

Re-arms only after free VRAM crosses back past `threshold_pct +/- margin_pct`
(~5 percentage points by default) - see plan A4 / `docs` for rationale.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from src.features.automation.triggers.base import TriggerSource

logger = logging.getLogger(__name__)

DEFAULT_MARGIN_PCT = 5.0
DEFAULT_POLL_INTERVAL_S = 10.0
DEFAULT_HOLD_S = 0.0

# "is_*" directions describe a state rather than a crossing: they fire on every
# poll the condition holds, so an automation can act whenever there is room
# rather than once at the moment the boundary was crossed. Hysteresis does not
# apply - there is no edge to debounce.
LEVEL_DIRECTIONS = frozenset({"is_below", "is_above"})


def direction_met(free_pct: float, threshold_pct: float, direction: str) -> bool:
    if direction in ("below", "is_below"):
        return free_pct < threshold_pct
    if direction in ("above", "is_above"):
        return free_pct > threshold_pct
    return False


def evaluate_hold(condition_met: bool, held_since: Optional[float], now: float,
                   hold_s: float) -> "tuple[bool, Optional[float]]":
    """
    Pure dwell step for `hold_s`. Returns (should_fire, new_held_since).

    `held_since` is the monotonic time the condition most recently started
    being continuously true, or None if it isn't currently true.
    """
    if not condition_met:
        return False, None
    since = held_since if held_since is not None else now
    return (now - since) >= hold_s, since


def evaluate_hysteresis(free_pct: float, threshold_pct: float, direction: str,
                         armed: bool, margin_pct: float = DEFAULT_MARGIN_PCT) -> "tuple[bool, bool]":
    """
    Pure hysteresis step. Returns (should_fire, new_armed_state).

    `direction`: "below" fires when free_pct drops under threshold_pct;
    "above" fires when free_pct rises over threshold_pct. Re-arms once
    free_pct has recovered past the threshold by `margin_pct`.
    """
    if direction == "below":
        if armed and free_pct < threshold_pct:
            return True, False
        if not armed and free_pct >= threshold_pct + margin_pct:
            return False, True
        return False, armed

    if direction == "above":
        if armed and free_pct > threshold_pct:
            return True, False
        if not armed and free_pct <= threshold_pct - margin_pct:
            return False, True
        return False, armed

    return False, armed


class ResourceTrigger(TriggerSource):
    """
    `trigger.gpu_threshold` node.

    Config: `threshold_pct` (float, % of total VRAM free), `direction`
    ("below" | "above"), `margin_pct` (default 5), `poll_interval_s` (default 10),
    `hold_s` (default 0 - condition must persist this long before firing),
    `require_generation_idle` (default false).
    """

    def __init__(self, automation_id: str, node_id: str, config: Dict[str, Any], enqueue,
                 gpu_manager: Any, generation_status_tracker: Optional[Any] = None):
        super().__init__(automation_id, node_id, config, enqueue)
        self._gpu_manager = gpu_manager
        self._generation_status_tracker = generation_status_tracker
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()
        self._armed = True
        self._held_since: Optional[float] = None

    async def start(self) -> None:
        self._stopped.clear()
        self._armed = True
        self._held_since = None
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

    def _generation_active(self) -> bool:
        tracker = self._generation_status_tracker
        if tracker is None:
            return False
        return len(tracker.list_active()) > 0

    async def _loop(self) -> None:
        poll_interval_s = float(self.config.get("poll_interval_s", DEFAULT_POLL_INTERVAL_S))
        threshold_pct = float(self.config.get("threshold_pct", 20.0))
        direction = self.config.get("direction", "below")
        margin_pct = float(self.config.get("margin_pct", DEFAULT_MARGIN_PCT))
        hold_s = float(self.config.get("hold_s", DEFAULT_HOLD_S))
        require_generation_idle = bool(self.config.get("require_generation_idle", False))

        try:
            while not self._stopped.is_set():
                try:
                    free_mb = self._gpu_manager.get_free_vram()
                    total_mb = self._gpu_manager.get_total_vram()
                    free_pct = (free_mb / total_mb) * 100.0 if total_mb else 0.0

                    condition_met = direction_met(free_pct, threshold_pct, direction)
                    if require_generation_idle and condition_met and self._generation_active():
                        condition_met = False

                    if direction in LEVEL_DIRECTIONS:
                        should_fire, self._held_since = evaluate_hold(
                            condition_met, self._held_since, time.monotonic(), hold_s
                        )
                        if should_fire:
                            self.fire({
                                "free_vram_mb": free_mb, "total_vram_mb": total_mb,
                                "free_vram_pct": free_pct, "threshold_pct": threshold_pct,
                                "direction": direction,
                            })
                    elif self._armed:
                        should_fire, self._held_since = evaluate_hold(
                            condition_met, self._held_since, time.monotonic(), hold_s
                        )
                        if should_fire:
                            self.fire({
                                "free_vram_mb": free_mb, "total_vram_mb": total_mb,
                                "free_vram_pct": free_pct, "threshold_pct": threshold_pct,
                                "direction": direction,
                            })
                            self._armed = False
                            self._held_since = None
                    else:
                        _, self._armed = evaluate_hysteresis(
                            free_pct, threshold_pct, direction, self._armed, margin_pct
                        )
                        self._held_since = None
                except Exception:
                    logger.error(f"[RESOURCE_TRIGGER] Error polling GPU for node {self.node_id}", exc_info=True)

                await asyncio.sleep(max(0.5, poll_interval_s))
        except asyncio.CancelledError:
            raise
