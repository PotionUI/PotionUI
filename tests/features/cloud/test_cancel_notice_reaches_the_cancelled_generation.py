import asyncio
import time

import pytest

import src.features.cloud.session as session
from src.features.backends.base_backend import BaseBackend
from src.features.cloud.clock import MonotonicClock
from src.features.cloud.session import UNCONFIRMED_CANCEL_MESSAGE
from src.features.cloud.testing.fake import FakeBehaviour, FakeCloudProvider
from src.features.generation.status_tracker import GenerationState, GenerationStatusTracker
from tests.features.cloud.cloud_generation_harness import USER_ID, CloudGeneration


@pytest.fixture
async def started(mock_db, tmp_path):
    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (USER_ID, USER_ID, f"{USER_ID}@example.test"),
        )

    async def start_run(supports_cancel=True, submitted=True):
        run = await CloudGeneration(
            tmp_path,
            FakeBehaviour(mode="async", duration_s=100_000, poll_after_s=300),
            supports_cancel=supports_cancel,
        ).start()
        run.backend.clock = MonotonicClock()
        if not submitted:
            return run

        await run.submit()
        while "submit" not in run.behaviour.calls:
            await asyncio.sleep(0)
        return run

    return start_run


@pytest.fixture
async def cancelled(started):
    async def cancel_a_run(supports_cancel):
        run = await started(supports_cancel=supports_cancel)
        assert await run.orchestrator.cancel_generation("gen-1") is True
        await run.finished()
        return run

    return cancel_a_run


def notice_of(run):
    return run.orchestrator.status_tracker.get("gen-1").model_dump()["cancel_notice"]


async def test_a_provider_that_cannot_cancel_leaves_a_notice_on_the_cancelled_payload(cancelled):
    run = await cancelled(supports_cancel=False)

    payload = run.orchestrator.status_tracker.get("gen-1").model_dump()

    assert payload["status"] == "cancelled"
    assert payload["cancel_notice"] == UNCONFIRMED_CANCEL_MESSAGE
    assert payload["message"] != UNCONFIRMED_CANCEL_MESSAGE


async def test_a_provider_that_confirms_the_cancel_leaves_no_notice(cancelled):
    run = await cancelled(supports_cancel=True)

    payload = run.orchestrator.status_tracker.get("gen-1").model_dump()

    assert payload["status"] == "cancelled"
    assert payload["cancel_notice"] is None
    assert "cancel" in run.behaviour.calls


async def test_a_provider_cancel_that_never_answers_leaves_a_notice(cancelled, monkeypatch):
    monkeypatch.setattr(session, "PROVIDER_CANCEL_WAIT_SECONDS", 0.05)

    async def hang(self, job):
        await asyncio.sleep(60)

    monkeypatch.setattr(FakeCloudProvider, "cancel", hang)

    run = await cancelled(supports_cancel=True)

    assert notice_of(run) == UNCONFIRMED_CANCEL_MESSAGE


async def test_a_cancel_during_submit_leaves_a_notice(started):
    run = await started(submitted=False)

    async def stuck_submit(request):
        run.behaviour.calls.append("submit")
        await asyncio.sleep(60)

    run.backend.provider.submit = stuck_submit
    await run.submit()
    while "submit" not in run.behaviour.calls:
        await asyncio.sleep(0)

    assert await run.orchestrator.cancel_generation("gen-1") is True
    await run.finished()

    assert notice_of(run) == UNCONFIRMED_CANCEL_MESSAGE


async def test_a_notice_is_handed_over_once(cancelled):
    run = await cancelled(supports_cancel=False)

    assert run.backend.take_cancel_notice("gen-1") is None


async def test_a_run_that_never_settles_does_not_hold_the_cancel_up(started, monkeypatch):
    release = asyncio.Event()

    async def hang(self, job):
        await release.wait()
        return True

    monkeypatch.setattr(session, "CANCEL_SETTLE_SECONDS", 0.1)
    monkeypatch.setattr(session, "PROVIDER_CANCEL_WAIT_SECONDS", 30.0)
    monkeypatch.setattr(FakeCloudProvider, "cancel", hang)
    run = await started()

    began = time.monotonic()
    cancelled = await run.orchestrator.cancel_generation("gen-1")
    elapsed = time.monotonic() - began
    release.set()
    await run.finished()

    assert cancelled is True
    assert elapsed < 1.0
    assert run.orchestrator.status_tracker.get("gen-1").state == GenerationState.CANCELLED


async def test_cancelling_on_a_non_cloud_backend_leaves_no_notice(started):
    run = await started(submitted=False)
    tracker = run.orchestrator.status_tracker
    tracker.create("native-1", backend_id="native-1")
    await tracker.transition_async("native-1", GenerationState.RUNNING)

    class Native:
        name = "native"
        take_cancel_notice = BaseBackend.take_cancel_notice

        async def cancel_generation(self, generation_id):
            return True

    run.orchestrator._run_backends["native-1"] = Native()

    assert await run.orchestrator.cancel_generation("native-1") is True

    assert tracker.get("native-1").state == GenerationState.CANCELLED
    assert tracker.get("native-1").model_dump()["cancel_notice"] is None


def test_a_generation_that_was_never_cancelled_carries_no_notice():
    tracker = GenerationStatusTracker()
    tracker.create("gen-1")

    assert tracker.get("gen-1").model_dump()["cancel_notice"] is None


def test_backends_without_a_cancel_outcome_report_no_notice():
    assert BaseBackend.take_cancel_notice(object(), "gen-1") is None


async def test_a_run_that_unwinds_after_the_settle_wait_still_leaves_its_notice(started, monkeypatch):
    async def hang(self, job):
        await asyncio.sleep(60)

    monkeypatch.setattr(session, "CANCEL_SETTLE_SECONDS", 0.05)
    monkeypatch.setattr(session, "PROVIDER_CANCEL_WAIT_SECONDS", 0.4)
    monkeypatch.setattr(FakeCloudProvider, "cancel", hang)
    run = await started()

    assert await run.orchestrator.cancel_generation("gen-1") is True
    assert notice_of(run) is None
    await run.finished()

    assert notice_of(run) == UNCONFIRMED_CANCEL_MESSAGE
    assert run.orchestrator.status_tracker.get("gen-1").state == GenerationState.CANCELLED
    assert run.backend.take_cancel_notice("gen-1") is None


async def _complete_with_deposited_notice(run, state, cancel_requested):
    tracker = run.orchestrator.status_tracker
    run.backend._cancel_notices["gen-1"] = UNCONFIRMED_CANCEL_MESSAGE
    if cancel_requested:
        run.orchestrator._cancel_requested.add("gen-1")
    if state is not None:
        await tracker.transition_async("gen-1", state)
    await run.orchestrator._handle_generation_completion("gen-1", None)
    return tracker.get("gen-1")


async def test_a_run_that_failed_after_a_cancel_request_leaves_no_notice_behind(started):
    run = await started()

    record = await _complete_with_deposited_notice(run, GenerationState.FAILED, cancel_requested=False)

    assert record.cancel_notice is None
    assert run.backend.take_cancel_notice("gen-1") is None
    assert "gen-1" not in run.orchestrator._cancel_requested


async def test_the_notice_is_delivered_when_completion_runs_before_the_cancelled_transition(started):
    run = await started()

    record = await _complete_with_deposited_notice(run, None, cancel_requested=True)

    assert record.cancel_notice == UNCONFIRMED_CANCEL_MESSAGE
    assert run.backend.take_cancel_notice("gen-1") is None


async def test_a_notice_without_a_cancel_request_is_not_attached_to_a_finished_run(started):
    run = await started()

    record = await _complete_with_deposited_notice(run, None, cancel_requested=False)

    assert record.cancel_notice is None
    assert run.backend.take_cancel_notice("gen-1") is None
