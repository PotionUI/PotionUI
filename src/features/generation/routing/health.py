import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

HEALTHY_STATUSES = frozenset({"healthy", "available"})
PROBE_TTL_SECONDS = 30.0
PROBE_TIMEOUT_SECONDS = 10.0


@dataclass
class _Health:
    healthy: bool
    reason: Optional[str]
    checked_at: float
    consecutive_failures: int


def probes_remotely(backend: Any) -> bool:
    return getattr(backend, "execution_device", "unestablished") != "this_host_gpu"


class BackendHealthTracker:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = PROBE_TTL_SECONDS,
        probe_timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
    ):
        self._clock = clock
        self._ttl = ttl_seconds
        self._probe_timeout = probe_timeout_seconds
        self._state: Dict[str, _Health] = {}
        self._probing: set = set()
        self._listeners: List[Callable[[str, bool], None]] = []

    def add_listener(self, listener: Callable[[str, bool], None]) -> None:
        self._listeners.append(listener)

    def is_healthy(self, backend_id: str) -> bool:
        state = self._state.get(backend_id)
        return state is None or state.healthy

    def reason(self, backend_id: str) -> Optional[str]:
        state = self._state.get(backend_id)
        return None if state is None or state.healthy else state.reason

    def consecutive_failures(self, backend_id: str) -> int:
        state = self._state.get(backend_id)
        return 0 if state is None else state.consecutive_failures

    def needs_probe(self, backend_id: str) -> bool:
        if backend_id in self._probing:
            return False
        state = self._state.get(backend_id)
        return state is None or self._clock() - state.checked_at >= self._ttl

    def forget(self, backend_id: str) -> None:
        self._state.pop(backend_id, None)

    def record(self, backend_id: str, healthy: bool, reason: Optional[str] = None) -> None:
        previous = self._state.get(backend_id)
        failures = 0 if healthy else (previous.consecutive_failures if previous else 0) + 1
        self._state[backend_id] = _Health(healthy, None if healthy else reason, self._clock(), failures)
        was_healthy = previous is None or previous.healthy
        if was_healthy != healthy:
            for listener in list(self._listeners):
                try:
                    listener(backend_id, healthy)
                except Exception:
                    logger.exception("[HEALTH] listener failed for backend %s", backend_id)

    async def probe(self, backend: Any) -> bool:
        backend_id = backend.backend_id
        if backend_id in self._probing:
            return self.is_healthy(backend_id)
        self._probing.add(backend_id)
        try:
            result = await asyncio.wait_for(backend.health_check(), timeout=self._probe_timeout)
            status = result.get("status") if isinstance(result, dict) else getattr(result, "status", None)
            healthy = status in HEALTHY_STATUSES
            reason = None if healthy else _reason_from(result, status)
        except asyncio.TimeoutError:
            healthy, reason = False, "the health check timed out"
        except Exception as exc:
            healthy, reason = False, f"the health check failed: {exc}"
        finally:
            self._probing.discard(backend_id)
        self.record(backend_id, healthy, reason)
        return healthy


def _reason_from(result: Any, status: Optional[str]) -> str:
    if isinstance(result, dict):
        for key in ("reason", "error", "message"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
    return f"the backend reports '{status or 'unknown'}'"
