"""RemoteExecutionPolicy defaults and the requeue decision it drives."""

import pytest

from src.features.remote_execution.policy import RemoteExecutionPolicy, should_requeue
from src.platform.worker_protocol import ExecutionLimitsV1, JobErrorV1


class TestDefaults:
    def test_the_documented_defaults(self):
        policy = RemoteExecutionPolicy()
        assert policy.package_ttl_seconds == 21600
        assert policy.max_dispatch_attempts == 3
        assert policy.lease_seconds == 60

    def test_is_frozen(self):
        policy = RemoteExecutionPolicy()
        with pytest.raises(Exception):
            policy.package_ttl_seconds = 1

    def test_default_limits_bounds_wall_time_to_the_ttl(self):
        policy = RemoteExecutionPolicy(package_ttl_seconds=1800)
        limits = policy.default_limits()

        assert limits == ExecutionLimitsV1(max_wall_seconds=1800)
        assert limits.max_staging_seconds is None
        assert limits.max_artifact_bytes is None


class TestShouldRequeue:
    def _error(self, retryable: bool) -> JobErrorV1:
        return JobErrorV1(code="cuda_oom", message="out of memory", retryable=retryable)

    @pytest.mark.parametrize(
        "retryable,attempt,max_attempts,expected",
        [
            (True, 0, 3, True),
            (True, 1, 3, True),
            (True, 2, 3, True),
            (True, 3, 3, False),   # at the cap
            (True, 4, 3, False),   # past the cap
            (False, 0, 3, False),  # worker says don't bother, even far from the cap
            (False, 3, 3, False),
        ],
    )
    def test_truth_table(self, retryable, attempt, max_attempts, expected):
        assert should_requeue(self._error(retryable), attempt, max_attempts) is expected
