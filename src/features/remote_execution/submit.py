import asyncio
from typing import Awaitable, Callable

from src.features.remote_execution.transport import (
    SubmitResponse,
    WorkerProtocolError,
    WorkerTransport,
    WorkerTransportError,
    WorkerUnreachableError,
)
from src.platform.worker_protocol import ExecutionPackageV1

ACCEPTED_OUTCOMES = frozenset({"accepted", "duplicate"})
SETTLED_OUTCOMES = frozenset({"rejected", "expired"})
KNOWN_OUTCOMES = ACCEPTED_OUTCOMES | SETTLED_OUTCOMES | {"busy", "digest_conflict"}
BACKEND_UNREACHABLE = "backend_unreachable"


class SubmitFailed(WorkerTransportError):
    def __init__(self, message: str, *, error_code: str | None = None):
        self.error_code = error_code
        super().__init__(message)


async def submit_checked(
    transport: WorkerTransport,
    package: ExecutionPackageV1,
    *,
    attempts: int = 3,
    retry_delay: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> SubmitResponse:
    attempts = max(1, attempts)
    last_problem = "no answer"
    last_code = None
    for attempt in range(attempts):
        if attempt:
            await sleep(retry_delay * attempt)
        try:
            response = await transport.submit(package)
        except WorkerUnreachableError as exc:
            last_problem = f"could not reach the worker ({exc})"
            last_code = BACKEND_UNREACHABLE
            continue
        except WorkerProtocolError as exc:
            raise SubmitFailed(f"The worker answered the submit with something unreadable: {exc}") from exc

        retryable = _problem_that_may_be_retried(response)
        if retryable is not None:
            last_problem = retryable
            last_code = None
            continue
        _raise_if_unusable(response, package)
        return response

    raise SubmitFailed(
        f"The worker did not take the job after {attempts} attempt(s): {last_problem}. "
        "The job may or may not have reached it.",
        error_code=last_code,
    )


def _problem_that_may_be_retried(response: SubmitResponse) -> str | None:
    if response.outcome == "busy" or response.status_code == 429:
        return "the worker is busy with another job"
    if response.status_code >= 500:
        return f"the worker answered HTTP {response.status_code}"
    return None


def _raise_if_unusable(response: SubmitResponse, package: ExecutionPackageV1) -> None:
    if response.outcome == "digest_conflict" or response.status_code == 409:
        raise SubmitFailed("The worker already holds a different job under this job's id.")
    if response.status_code not in (200, 202):
        detail = f": {response.detail}" if response.detail else ""
        raise SubmitFailed(f"The worker refused the submit (HTTP {response.status_code}){detail}")
    if response.outcome not in KNOWN_OUTCOMES:
        raise SubmitFailed(
            f"The worker answered HTTP {response.status_code} without a usable outcome"
            + (f" ({response.detail})" if response.detail else "")
        )
    if response.execution_id != package.execution_id:
        raise SubmitFailed(
            f"The worker answered for job {response.execution_id!r}, not the one that was sent."
        )
