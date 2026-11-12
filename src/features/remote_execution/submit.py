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


class SubmitAmbiguous(SubmitFailed):
    pass


async def submit_checked(
    transport: WorkerTransport,
    package: ExecutionPackageV1,
    *,
    attempts: int = 3,
    retry_delay: float = 0.5,
    busy_wait_seconds: float = 3600.0,
    busy_backoff_cap: float = 30.0,
    on_busy: Callable[[float], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> SubmitResponse:
    attempts = max(1, attempts)
    last_problem = "no answer"
    last_code = None
    failures = 0
    busy_waited = 0.0
    busy_turns = 0
    while failures < attempts:
        if failures:
            await sleep(retry_delay * failures)
        try:
            response = await transport.submit(package)
        except WorkerUnreachableError as exc:
            last_problem = f"could not reach the worker ({exc})"
            last_code = BACKEND_UNREACHABLE
            failures += 1
            continue
        except WorkerProtocolError as exc:
            raise SubmitFailed(f"The worker answered the submit with something unreadable: {exc}") from exc

        if response.outcome == "busy" or response.status_code == 429:
            if busy_waited >= busy_wait_seconds:
                raise SubmitFailed(
                    f"The worker stayed busy with another job for {int(busy_wait_seconds // 60)} minutes."
                )
            delay = min(busy_backoff_cap, float(2 ** busy_turns))
            busy_turns += 1
            busy_waited += delay
            if on_busy is not None:
                await on_busy(delay)
            await sleep(delay)
            continue
        if response.status_code >= 500:
            last_problem = f"the worker answered HTTP {response.status_code}"
            last_code = None
            failures += 1
            continue
        _raise_if_unusable(response, package)
        return response

    raise SubmitAmbiguous(
        f"The worker did not take the job after {attempts} attempt(s): {last_problem}. "
        "The job may or may not have reached it.",
        error_code=last_code,
    )


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
