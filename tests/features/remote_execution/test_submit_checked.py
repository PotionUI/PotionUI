import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from src.features.backends.native_remote_backend import RemoteNativeBackend, WorkerSilent
from src.features.remote_execution.policy import RemoteExecutionPolicy
from src.features.remote_execution.submit import SubmitFailed, submit_checked
from src.features.remote_execution.transport import WorkerTransport, WorkerTransportError
from src.pipelines.outputs import ErrorGenerationOutput

PACKAGE = SimpleNamespace(execution_id="job-1")


@pytest.fixture(autouse=True)
def _envelope():
    with patch("src.features.remote_execution.transport.envelope", return_value={"kind": "execution_package"}):
        yield


class Script:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        return step


def _json(status, body):
    return httpx.Response(status, json=body)


def _accepted():
    return _json(202, {"execution_id": "job-1", "outcome": "accepted"})


async def _submit(script, attempts=3):
    transport = WorkerTransport("http://worker", "t", transport=httpx.MockTransport(script))
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    response = await submit_checked(transport, PACKAGE, attempts=attempts, retry_delay=0.5, sleep=sleep)
    return response, sleeps


@pytest.mark.asyncio
async def test_an_accepted_answer_is_returned():
    response, sleeps = await _submit(Script(_accepted()))

    assert (response.status_code, response.outcome, response.execution_id) == (202, "accepted", "job-1")
    assert sleeps == []


@pytest.mark.asyncio
async def test_a_duplicate_answer_counts_as_taken():
    response, _ = await _submit(Script(_json(200, {"execution_id": "job-1", "outcome": "duplicate"})))

    assert response.outcome == "duplicate"


@pytest.mark.asyncio
async def test_a_rejected_answer_is_returned_so_the_structured_reason_can_be_read_from_the_events():
    response, _ = await _submit(Script(_json(200, {"execution_id": "job-1", "outcome": "rejected", "detail": "build"})))

    assert response.outcome == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"error": "model exploded"},
    {"execution_id": "job-1"},
    {"execution_id": "job-1", "outcome": "surprise"},
    {"execution_id": "job-1", "outcome": 7},
    {},
])
async def test_an_error_body_behind_http_200_is_a_failure_not_a_success(body):
    script = Script(_json(200, body))

    with pytest.raises(SubmitFailed) as caught:
        await _submit(script)

    assert "usable outcome" in str(caught.value)
    assert len(script.requests) == 1


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_fails_without_a_retry():
    script = Script(httpx.Response(200, text="<html>welcome</html>"))

    with pytest.raises(SubmitFailed) as caught:
        await _submit(script)

    assert "unreadable" in str(caught.value)
    assert len(script.requests) == 1


@pytest.mark.asyncio
async def test_a_json_body_that_is_not_an_object_fails():
    with pytest.raises(SubmitFailed) as caught:
        await _submit(Script(_json(200, ["accepted"])))

    assert "not an object" in str(caught.value)


@pytest.mark.asyncio
async def test_an_answer_for_a_different_job_id_fails():
    with pytest.raises(SubmitFailed) as caught:
        await _submit(Script(_json(202, {"execution_id": "someone-else", "outcome": "accepted"})))

    assert "not the one that was sent" in str(caught.value)


@pytest.mark.asyncio
async def test_an_answer_without_a_job_id_fails():
    with pytest.raises(SubmitFailed):
        await _submit(Script(_json(202, {"outcome": "accepted"})))


@pytest.mark.asyncio
async def test_a_digest_conflict_fails_without_a_retry():
    script = Script(_json(409, {"execution_id": "job-1", "outcome": "digest_conflict"}))

    with pytest.raises(SubmitFailed) as caught:
        await _submit(script)

    assert "different job" in str(caught.value)
    assert len(script.requests) == 1


@pytest.mark.asyncio
async def test_a_client_error_fails_naming_the_status_without_a_retry():
    script = Script(_json(422, {"detail": {"code": "wrong_kind"}}))

    with pytest.raises(SubmitFailed) as caught:
        await _submit(script)

    assert "refused the submit (HTTP 422)" in str(caught.value)
    assert len(script.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 404])
async def test_a_failing_status_is_a_failure_even_when_the_body_looks_accepted(status):
    with pytest.raises(SubmitFailed) as caught:
        await _submit(Script(_json(status, {"execution_id": "job-1", "outcome": "accepted"})))

    assert f"HTTP {status}" in str(caught.value)


@pytest.mark.asyncio
async def test_a_server_error_is_retried_with_the_same_job_key_and_then_succeeds():
    script = Script(_json(500, {"detail": "boom"}), _accepted())

    response, sleeps = await _submit(script)

    assert response.outcome == "accepted"
    assert len(script.requests) == 2
    assert script.requests[0] == script.requests[1]
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_a_server_error_that_never_clears_fails_after_the_attempts():
    script = Script(_json(503, {"detail": "boom"}))

    with pytest.raises(SubmitFailed) as caught:
        await _submit(script, attempts=3)

    assert len(script.requests) == 3
    assert "3 attempt(s)" in str(caught.value)
    assert "may or may not" in str(caught.value)


@pytest.mark.asyncio
async def test_a_gateway_error_page_is_retried_as_an_unreachable_worker():
    script = Script(httpx.Response(502, text="Bad Gateway"), _accepted())

    response, _ = await _submit(script)

    assert response.outcome == "accepted"
    assert len(script.requests) == 2


@pytest.mark.asyncio
async def test_a_timeout_is_retried_because_the_job_key_makes_a_second_submit_harmless():
    script = Script(httpx.ReadTimeout("slow"), _accepted())

    response, sleeps = await _submit(script)

    assert response.outcome == "accepted"
    assert len(script.requests) == 2
    assert script.requests[0] == script.requests[1]


@pytest.mark.asyncio
async def test_a_worker_that_never_answers_fails_as_unreachable_and_says_the_job_may_have_arrived():
    script = Script(httpx.ConnectTimeout("no route"))

    with pytest.raises(SubmitFailed) as caught:
        await _submit(script, attempts=2)

    assert len(script.requests) == 2
    assert caught.value.error_code == "backend_unreachable"
    assert "may or may not" in str(caught.value)


@pytest.mark.asyncio
async def test_a_busy_worker_is_retried_and_a_busy_one_that_stays_busy_fails_plainly():
    clears = Script(_json(429, {"execution_id": "job-1", "outcome": "busy"}), _accepted())
    response, _ = await _submit(clears)
    assert response.outcome == "accepted"

    stuck = Script(_json(429, {"execution_id": "job-1", "outcome": "busy"}))
    with pytest.raises(SubmitFailed) as caught:
        await _submit(stuck)
    assert "busy" in str(caught.value)
    assert caught.value.error_code is None


@pytest.mark.asyncio
async def test_a_single_attempt_policy_does_not_retry():
    script = Script(_json(500, {}), _accepted())

    with pytest.raises(SubmitFailed):
        await _submit(script, attempts=1)

    assert len(script.requests) == 1


def _backend(policy):
    backend = object.__new__(RemoteNativeBackend)
    backend._policy = policy
    backend._repository = Mock()
    backend._active = set()
    return backend


class HangingTransport:
    def __init__(self, events=()):
        self.events = list(events)
        self.cancelled = []

    async def stream_events(self, execution_id, after=0):
        for event in self.events:
            yield event
        await asyncio.sleep(3600)

    async def cancel(self, execution_id):
        self.cancelled.append(execution_id)
        return "accepted"


@pytest.mark.asyncio
async def test_a_job_that_never_reports_back_fails_after_the_first_event_timeout():
    backend = _backend(RemoteExecutionPolicy(first_event_timeout_seconds=0.05, event_idle_timeout_seconds=30))
    package = SimpleNamespace(processed_pipes=SimpleNamespace(pipes=[]))

    with pytest.raises(WorkerSilent) as caught:
        await backend._consume_events("job-1", package, HangingTransport(), Path("/nonexistent"), Mock())

    assert "nothing for 0 seconds" in str(caught.value)
    assert caught.value.error_code == "backend_unreachable"


@pytest.mark.asyncio
async def test_a_job_that_goes_quiet_after_starting_fails_after_the_idle_timeout():
    backend = _backend(RemoteExecutionPolicy(first_event_timeout_seconds=30, event_idle_timeout_seconds=0.05))
    package = SimpleNamespace(processed_pipes=SimpleNamespace(pipes=[]))
    event = SimpleNamespace(cursor=1, is_terminal=False, payload=None, kind="running", pipe_id=None, error=None, artifacts=[])

    async with asyncio.timeout(3):
        with pytest.raises(WorkerSilent):
            await backend._consume_events("job-1", package, HangingTransport([event]), Path("/nonexistent"), Mock())

    backend._repository.apply_job_event.assert_called_once()


@pytest.mark.asyncio
async def test_a_silent_job_is_failed_cancelled_on_the_worker_and_frees_the_slot():
    backend = _backend(RemoteExecutionPolicy())
    outputs = []

    async def silent(*args, **kwargs):
        raise WorkerSilent("The worker took the job but sent nothing for 120 seconds, so it was stopped.")

    backend._dispatch = silent
    backend._active.add("job-1")

    await backend._run("job-1", None, [], outputs.append)

    assert backend._active == set()
    assert outputs[-1] is None
    errors = [o for o in outputs if isinstance(o, ErrorGenerationOutput)]
    assert len(errors) == 1
    assert errors[0].error_code == "backend_unreachable"
    assert "sent nothing" in errors[0].error


@pytest.mark.asyncio
async def test_failing_a_silent_dispatch_marks_the_row_failed_and_tells_the_worker_to_stop():
    backend = _backend(RemoteExecutionPolicy())
    backend._repository.get_by_id.return_value = SimpleNamespace(is_terminal=False)
    transport = HangingTransport()

    await backend._fail_dispatch("job-1", WorkerSilent("quiet"), transport)

    assert backend._repository.apply_state.call_args.kwargs["error_code"] == "dispatch_error"
    assert transport.cancelled == ["job-1"]


@pytest.mark.asyncio
async def test_failing_any_other_dispatch_does_not_send_a_cancel():
    backend = _backend(RemoteExecutionPolicy())
    backend._repository.get_by_id.return_value = SimpleNamespace(is_terminal=False)
    transport = HangingTransport()

    await backend._fail_dispatch("job-1", SubmitFailed("refused"), transport)

    assert transport.cancelled == []


@pytest.mark.asyncio
async def test_a_submit_failure_reaches_the_user_as_a_failed_generation_and_frees_the_slot():
    backend = _backend(RemoteExecutionPolicy())
    outputs = []

    async def refused(*args, **kwargs):
        raise SubmitFailed("The worker already holds a different job under this job's id.")

    backend._dispatch = refused
    backend._active.add("job-2")

    await backend._run("job-2", None, [], outputs.append)

    assert backend._active == set()
    assert outputs[-1] is None
    assert [o.error for o in outputs if isinstance(o, ErrorGenerationOutput)] == [
        "The worker already holds a different job under this job's id."
    ]
