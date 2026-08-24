"""Dispatch policy: how long a package is good for, how many times it may be
attempted, and whether a worker's failure earns a retry.

Pure and repository-backed pieces only - the dispatch loop that calls these on
a schedule is a later wave. Nothing here reads the settings table yet; the
defaults on :class:`RemoteExecutionPolicy` are the whole story until that
wave wires an admin-configurable source.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.platform.worker_protocol import ExecutionLimitsV1, JobErrorV1


@dataclass(frozen=True)
class RemoteExecutionPolicy:
    """The knobs a dispatcher applies uniformly, absent a per-request override."""

    #: How long an ExecutionPackageV1 remains valid after it's issued. 6
    #: hours: long enough to survive a provider queue delay, short enough
    #: that a worker which comes back to life after an outage still refuses
    #: state core has already given up on.
    package_ttl_seconds: int = 21600
    #: How many times claim_for_dispatch may hand out the same row before
    #: fail_exhausted takes it off the queue.
    max_dispatch_attempts: int = 3
    #: The lease duration claim_for_dispatch and renew_lease use by default.
    lease_seconds: int = 60

    def default_limits(self) -> ExecutionLimitsV1:
        """The ExecutionLimitsV1 a package gets when the caller supplies none.

        Only max_wall_seconds is derived - from the same TTL as expires_at,
        on the reasoning that a worker has no business running longer than
        the package it's executing remains valid. max_staging_seconds and
        max_artifact_bytes stay unset (unbounded): this policy carries no
        opinion about staging time or artifact size yet, and a false bound
        there would fail packages for a reason this dataclass cannot explain.
        """
        return ExecutionLimitsV1(max_wall_seconds=self.package_ttl_seconds)


def should_requeue(error: JobErrorV1, attempt: int, max_attempts: int) -> bool:
    """Whether a worker's failure should send the execution back to PENDING.

    False when the worker marked the failure non-retryable (it knows
    something about the failure a retry can't fix), and false once
    ``attempt`` has already reached the cap - a caller at the cap should be
    calling :meth:`RemoteExecutionRepository.fail_exhausted`, not requeuing
    again.
    """
    if not error.retryable:
        return False
    return attempt < max_attempts
