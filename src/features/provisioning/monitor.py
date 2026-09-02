"""Heartbeat for provisioned compute: reconcile every row against its
provider on a timer, so a pod paused/deleted in the provider's own console
shows up here within one interval - and stops being selected for new
generations just as fast.

One asyncio loop per process, started/stopped from the app lifespan. Tests
drive `tick()` directly.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from src.features.backends.backend_registry import BackendRegistry
from src.features.provisioning.contracts import (
    STATE_FAILED,
    STATE_MISSING,
    STATE_PROVISIONING,
    STATE_STOPPED,
    STATE_UNKNOWN,
    ComputeProvisionerError,
)
from src.features.provisioning.operations import (
    BRING_UP_STATES,
    ComputeProvisioningJobs,
    broadcast_compute_status,
    disable_backend,
)
from src.features.provisioning.registry import ComputeProvisionerRegistry
from src.features.provisioning.repository import ProvisionedComputeRepository

logger = logging.getLogger(__name__)

INTERVAL_SETTING_KEY = "provisioning.status_interval_seconds"
DEFAULT_INTERVAL_SECONDS = 15
MIN_INTERVAL_SECONDS = 5

#: States in which a linked backend must not stay enabled: the worker is
#: gone or paused, so routing a generation to it can only fail. The same
#: rule `operations.stop_compute` applies when the operator stops it here.
DISABLING_STATES = frozenset({STATE_STOPPED, STATE_MISSING, STATE_FAILED})


def resolve_interval(settings) -> int:
    """`provisioning.status_interval_seconds` from the settings table,
    clamped to the floor; the default when unset or unparseable."""
    if settings is None:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(settings.get_setting(INTERVAL_SETTING_KEY, DEFAULT_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, value)


class ComputeStatusMonitor:
    def __init__(
        self,
        registry: ComputeProvisionerRegistry,
        repository: ProvisionedComputeRepository,
        backend_registry: BackendRegistry,
        hub,
        jobs: Optional[ComputeProvisioningJobs] = None,
        *,
        settings=None,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        call_timeout_seconds: float = 20.0,
    ):
        self._registry = registry
        self._repository = repository
        self._backend_registry = backend_registry
        self._hub = hub
        self._jobs = jobs
        self._settings = settings
        self.interval_seconds = interval_seconds
        self.call_timeout_seconds = call_timeout_seconds
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.interval_seconds = resolve_interval(self._settings) if self._settings is not None else self.interval_seconds
        self._task = asyncio.create_task(self._loop())
        logger.info("Compute status monitor started (every %ss)", self.interval_seconds)

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Compute status tick failed: %s", exc, exc_info=True)
            await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> None:
        for row in self._repository.list_all():
            try:
                await self._reconcile(row)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Reconciling provisioned compute %s failed: %s", row.id, exc, exc_info=True)

    async def _reconcile(self, row) -> None:
        if row.status in BRING_UP_STATES and self._jobs is not None and self._jobs.is_running(row.id):
            return
        if row.status == STATE_PROVISIONING:
            # A `provisioning` row with no job behind it is one the process
            # died on - nothing will ever finish it. A `starting` row in the
            # same spot has a handle, so it is simply asked about below.
            state, detail = STATE_FAILED, "Provisioning was interrupted by a server restart"
        elif not row.handle:
            return
        else:
            state, detail = await self._ask_provider(row)

        previous_state, previous_detail = row.status, row.status_detail
        self._repository.update_status(row.id, state, detail=detail, checked_at=datetime.now(timezone.utc))
        if state == previous_state and detail == previous_detail:
            return

        if state != previous_state and state in DISABLING_STATES:
            if await disable_backend(self._backend_registry, row.backend_id):
                logger.info(
                    "Provisioned compute %s went %s - disabled backend %s", row.id, state, row.backend_id
                )

        fresh = self._repository.get_by_id(row.id)
        if fresh is not None:
            await broadcast_compute_status(self._hub, fresh)

    async def _ask_provider(self, row):
        provisioner = self._registry.get(row.provider_id)
        if provisioner is None:
            return STATE_UNKNOWN, f"No '{row.provider_id}' compute provider is registered"
        try:
            status = await asyncio.wait_for(provisioner.status(row.handle), timeout=self.call_timeout_seconds)
        except asyncio.TimeoutError:
            return STATE_UNKNOWN, f"Status check timed out after {self.call_timeout_seconds:g}s"
        except ComputeProvisionerError as exc:
            return STATE_UNKNOWN, str(exc)
        except Exception as exc:
            logger.warning("Compute provider '%s' status() raised for row %s: %s", row.provider_id, row.id, exc)
            return STATE_UNKNOWN, str(exc) or type(exc).__name__
        return status.state, status.detail
