"""Making the `remote_executions` table honest again after a restart.

Three of the four things this does are pure repository sweeps that need no
network access at all (`requeue_expired_leases`, `expire_overdue`,
`fail_exhausted` - see `src.features.remote_execution.repository`). The fourth
- resuming the event stream for a row that is still non-terminal - is the one
that can talk to an unreachable host, so it is the one bounded by a timeout:
a worker that doesn't answer must never hold up the sweep, let alone app
startup (see `src.bootstrap.app`'s lifespan).

Deliberately does NOT re-wire an `emit` callback into the resumed rows: the
generation-side WebSocket bridge that owned one lived in the process that
died, and there is no live subscriber to hand a resumed output to. This only
keeps the row and its persisted event history correct; a generation left
non-terminal by the restart is reconciled the same way a local one is
(`GenerationRepository.reconcile_interrupted_generations`, called alongside
this at startup) - not resurrected here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.features.backends.backend_config import NATIVE_REMOTE_DRIVER
from src.features.remote_execution.records import RemoteExecutionState
from src.features.remote_execution.repository import RemoteExecutionRepository
from src.features.remote_execution.policy import RemoteExecutionPolicy
from src.features.remote_execution.transport import WorkerTransport

logger = logging.getLogger(__name__)

#: States a row can be resumed from - a row waiting to be picked up
#: (PENDING/DISPATCHING) has no worker execution to resume events from yet.
_RESUMABLE_STATES = (
    RemoteExecutionState.STAGING,
    RemoteExecutionState.RUNNING,
    RemoteExecutionState.CANCELLING,
)


class RemoteExecutionReconciler:
    def __init__(
        self,
        *,
        repository: Optional[RemoteExecutionRepository] = None,
        policy: Optional[RemoteExecutionPolicy] = None,
        backend_config_store=None,
        event_pull_timeout_seconds: float = 5.0,
    ):
        self._repository = repository or RemoteExecutionRepository()
        self._policy = policy or RemoteExecutionPolicy()
        self._backend_config_store = backend_config_store
        self._event_pull_timeout = event_pull_timeout_seconds

    async def reconcile(self) -> dict:
        """Run every sweep once. Safe to call repeatedly (e.g. on a timer) -
        every step is idempotent."""
        lapsed = self._repository.requeue_expired_leases()
        expired = self._repository.expire_overdue()
        failed = self._repository.fail_exhausted(self._policy.max_dispatch_attempts)
        resumed, unreachable = await self._resume_live_rows()

        if lapsed or expired or failed or resumed or unreachable:
            logger.info(
                "[REMOTE_EXECUTION] Reconciled: %d lease(s) reclaimed, %d expired, "
                "%d exhausted, %d event(s) resumed, %d row(s) unreachable",
                lapsed, expired, failed, resumed, unreachable,
            )
        return {
            "leases_reclaimed": lapsed,
            "expired": expired,
            "exhausted": failed,
            "events_resumed": resumed,
            "unreachable": unreachable,
        }

    async def _resume_live_rows(self) -> tuple[int, int]:
        if self._backend_config_store is None:
            return 0, 0

        resumed = 0
        unreachable = 0
        for state in _RESUMABLE_STATES:
            for row in self._repository.list_by_state(state):
                if not row.backend_id:
                    continue
                config = self._backend_config_store.get_backend(row.backend_id)
                if config is None or config.driver != NATIVE_REMOTE_DRIVER:
                    continue

                try:
                    applied = await asyncio.wait_for(
                        self._resume_row(row, config), timeout=self._event_pull_timeout,
                    )
                    resumed += applied
                except Exception as exc:
                    # A single unreachable worker, a timed-out pull, or a
                    # concurrently-changed row must not abort the sweep for
                    # every other row - see module docstring.
                    unreachable += 1
                    logger.warning(
                        "[REMOTE_EXECUTION] Could not resume events for %r on backend %r "
                        "within %.1fs: %s",
                        row.id, row.backend_id, self._event_pull_timeout, exc,
                    )
        return resumed, unreachable

    async def _resume_row(self, row, config) -> int:
        transport = WorkerTransport(
            config.base_url, config.worker_token,
            connect_timeout=self._event_pull_timeout, request_timeout=self._event_pull_timeout,
        )
        applied = 0
        async for event in transport.stream_events(row.id, after=row.event_cursor):
            if self._repository.apply_job_event(row.id, event):
                applied += 1
            if event.is_terminal:
                break
        return applied
