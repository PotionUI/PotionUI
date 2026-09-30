import asyncio
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.features.cloud.contracts import CloudError
from src.features.cloud.session import CloudRunSession
from src.features.cloud.testing.fake import FakeBehaviour, FakeClock, FakeCloudConfig, build_fake_provider, fake_specs
from src.pipelines.cloud import CloudRunCancelled, CloudRunError, CloudRunRequest

IMAGE = "fake~image-1"
VIDEO = "fake~video-1"
SECRET = "sk-live-SECRET-0123456789"


class StubCatalog:
    def __init__(self):
        self.specs = {"fake~image-1": fake_specs()[0], "fake~video-1": fake_specs()[1]}

    def get_many(self, backend_id, slugs):
        return [SimpleNamespace(spec=self.specs[slug]) for slug in slugs if slug in self.specs]


class Harness:
    def __init__(self, tmp_path, behaviour=None, supports_cancel=True, timeout_seconds=600, clock=None, idempotent=False):
        self.clock = clock or FakeClock()
        self.provider = build_fake_provider(
            behaviour or FakeBehaviour(), clock=self.clock, supports_cancel=supports_cancel, idempotent=idempotent
        )
        self.provider.config = FakeCloudConfig(id="fake-1", name="Fake", api_key=SECRET)
        self.catalog = StubCatalog()
        self.progress = []
        self.session = CloudRunSession(
            provider=self.provider,
            catalog=self.catalog,
            backend_id="fake-1",
            generation_id="gen-1",
            timeout_seconds=timeout_seconds,
            clock=self.clock,
            scratch_dir=tmp_path / "scratch",
        )

    async def run(self, **fields):
        request = CloudRunRequest(**{"task": "txt2img", "model": IMAGE, "prompt": "a cat", **fields})
        return await self.session.run(request, on_progress=self.progress.append)


@pytest.fixture
def harness(tmp_path):
    def build(**kwargs):
        return Harness(tmp_path, **kwargs)

    return build


async def test_a_sync_job_returns_its_files_and_cost(harness):
    h = harness(behaviour=FakeBehaviour(mode="sync"))

    outcome = await h.run(seed=7)

    (artifact,) = outcome.artifacts
    assert artifact.modality == "image" and artifact.path.read_bytes().startswith(b"\x89PNG")
    assert outcome.cost.amount_usd == Decimal("0.04") and outcome.cost.source == "provider"
    assert outcome.seed_used == 7
    assert h.provider.behaviour.calls == ["submit", "fetch"]


async def test_an_async_job_is_polled_until_it_finishes(harness):
    h = harness(behaviour=FakeBehaviour(mode="async", queue_s=2, duration_s=10, poll_after_s=3))

    outcome = await h.run()

    assert len(outcome.artifacts) == 1
    assert h.provider.behaviour.calls.count("poll") >= 3
    states = [item.state for item in h.progress]
    assert states[0] == "queued" and "running" in states and states[-1] == "fetching"
    assert any(item.fraction is not None for item in h.progress if item.state == "running")


async def test_a_job_splits_into_the_models_largest_batches(harness):
    h = harness(behaviour=FakeBehaviour(mode="sync", outputs=4))

    outcome = await h.run(count=6)

    assert h.provider.behaviour.calls.count("submit") == 2
    assert [artifact.index for artifact in outcome.artifacts] == list(range(len(outcome.artifacts)))


async def test_the_deadline_cancels_the_job_and_reports_a_timeout(harness):
    h = harness(behaviour=FakeBehaviour(mode="async", duration_s=10_000, poll_after_s=5), timeout_seconds=60)

    start = h.clock.now()

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "timeout"
    assert "cancel" in h.provider.behaviour.calls
    assert 60 <= h.clock.now() - start <= 65


async def test_the_model_deadline_wins_when_it_is_shorter(harness):
    h = harness(behaviour=FakeBehaviour(mode="async", duration_s=10_000, poll_after_s=5), timeout_seconds=3600)

    start = h.clock.now()

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "timeout"
    assert 120 <= h.clock.now() - start <= 125


@pytest.mark.parametrize("kind", ["auth", "credits", "refused", "invalid_request", "failed"])
async def test_errors_that_cannot_be_retried_fail_at_once(harness, kind):
    h = harness(behaviour=FakeBehaviour(fail_kind=kind, fail_stage="submit"))

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == kind
    assert h.provider.behaviour.calls == ["submit"]


async def test_a_rate_limited_submit_is_retried_after_the_providers_delay_then_gives_up(harness):
    h = harness(behaviour=FakeBehaviour(fail_kind="rate_limited", fail_stage="submit", retry_after_s=7))

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "rate_limited"
    assert h.provider.behaviour.calls.count("submit") == 3
    assert sum(h.clock.slept) >= 14
    assert any(item.message == "Waiting for provider rate limit" for item in h.progress)


async def test_an_unavailable_submit_after_the_request_was_sent_is_never_retried(harness):
    h = harness(behaviour=FakeBehaviour(fail_kind="unavailable", fail_stage="submit", request_sent=True))

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "unavailable"
    assert h.provider.behaviour.calls == ["submit"]


async def test_a_submit_that_never_left_the_machine_is_retried(harness):
    h = harness(behaviour=FakeBehaviour(mode="sync", fail_kind="unavailable", fail_stage="submit", request_sent=False, fail_times=2))

    outcome = await h.run()

    assert len(outcome.artifacts) == 1
    assert h.provider.behaviour.calls.count("submit") == 3


async def test_a_submit_that_never_left_the_machine_gives_up_after_three_attempts(harness):
    h = harness(behaviour=FakeBehaviour(fail_kind="unavailable", fail_stage="submit", request_sent=False))

    with pytest.raises(CloudRunError):
        await h.run()

    assert h.provider.behaviour.calls.count("submit") == 3


async def test_an_idempotent_provider_retries_unavailable_with_the_same_key(harness):
    h = harness(
        behaviour=FakeBehaviour(mode="sync", fail_kind="unavailable", fail_stage="submit", request_sent=True, fail_times=2),
        idempotent=True,
    )

    outcome = await h.run()

    assert len(outcome.artifacts) == 1
    keys = h.provider.behaviour.keys
    assert len(keys) == 3 and len(set(keys)) == 1 and keys[0]


async def test_idempotency_keys_differ_between_jobs_and_between_runs(harness):
    h = harness(behaviour=FakeBehaviour(mode="sync", outputs=4))

    await h.run(count=6)
    await h.run(count=1)

    assert len(set(h.provider.behaviour.keys)) == 3


async def test_a_timeout_after_submit_is_never_retried(harness):
    h = harness(behaviour=FakeBehaviour(fail_kind="timeout", fail_stage="submit"))

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "timeout"
    assert h.provider.behaviour.calls == ["submit"]


async def test_polling_gives_up_after_five_consecutive_errors(harness):
    h = harness(behaviour=FakeBehaviour(mode="async", fail_kind="unavailable", fail_stage="poll"))

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "unavailable"
    assert h.provider.behaviour.calls.count("poll") == 5


async def test_a_failed_poll_of_a_fatal_kind_stops_at_once(harness):
    h = harness(behaviour=FakeBehaviour(mode="async", fail_kind="auth", fail_stage="poll"))

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "auth"
    assert h.provider.behaviour.calls.count("poll") == 1


@pytest.mark.parametrize("terminal,kind", [("failed", "failed"), ("expired", "expired")])
async def test_terminal_provider_states_map_to_error_kinds(harness, terminal, kind):
    h = harness(behaviour=FakeBehaviour(mode="async", duration_s=3, terminal=terminal))

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == kind


async def test_a_fetch_failure_is_reported_and_leaves_no_files(harness, tmp_path):
    h = harness(behaviour=FakeBehaviour(mode="sync", fail_kind="failed", fail_stage="fetch"))

    with pytest.raises(CloudRunError):
        await h.run()

    assert [path for path in (tmp_path / "scratch").rglob("*") if path.is_file()] == []


async def test_a_file_that_is_not_an_image_fails_the_run(harness):
    h = harness(behaviour=FakeBehaviour(mode="sync"))

    async def broken(artifact, dest, *, max_bytes=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"not an image")
        return dest

    h.provider.fetch = broken

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "failed"


async def test_a_model_the_backend_does_not_offer_is_rejected(harness):
    h = harness()

    with pytest.raises(CloudRunError) as raised:
        await h.run(model="fake~missing")

    assert raised.value.kind == "invalid_request"
    assert h.provider.behaviour.calls == []


async def test_inputs_beyond_the_models_limits_are_rejected(harness, tmp_path):
    h = harness()
    files = []
    for index in range(5):
        path = tmp_path / f"in{index}.png"
        path.write_bytes(b"x")
        files.append(path)

    with pytest.raises(CloudRunError) as raised:
        await h.run(task="img_edit", inputs={"reference": files})

    assert raised.value.kind == "invalid_request"


async def test_secrets_in_provider_errors_are_scrubbed(harness):
    h = harness()

    async def leaky(request):
        raise CloudError("failed", "The provider could not complete the request.", detail=f"echo of Bearer {SECRET} and {SECRET}")

    h.provider.submit = leaky

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert SECRET not in raised.value.detail and SECRET not in str(raised.value)


async def test_secrets_in_unexpected_exceptions_are_scrubbed(harness):
    h = harness()

    async def crash(request):
        raise RuntimeError(f"connection to host with key {SECRET} dropped")

    h.provider.submit = crash

    with pytest.raises(CloudRunError) as raised:
        await h.run()

    assert raised.value.kind == "failed"
    assert SECRET not in raised.value.detail and SECRET not in str(raised.value)


async def test_cancel_takes_effect_within_a_second_and_reaches_the_provider(tmp_path):
    from src.features.cloud.clock import MonotonicClock

    h = Harness(tmp_path, behaviour=FakeBehaviour(mode="async", duration_s=10_000, poll_after_s=300), clock=MonotonicClock())
    task = asyncio.ensure_future(h.run())
    await asyncio.sleep(0.2)

    started = time.monotonic()
    await h.session.cancel()
    with pytest.raises(CloudRunCancelled) as raised:
        await task

    assert time.monotonic() - started < 1.0
    assert raised.value.cancel_confirmed is True
    assert "cancel" in h.provider.behaviour.calls


async def test_a_cancel_from_the_executor_flag_is_seen_quickly(tmp_path):
    from src.features.cloud.clock import MonotonicClock

    h = Harness(tmp_path, behaviour=FakeBehaviour(mode="async", duration_s=10_000, poll_after_s=300), clock=MonotonicClock())
    flag = {"cancelled": False}
    request = CloudRunRequest(task="txt2img", model=IMAGE, prompt="x")
    task = asyncio.ensure_future(h.session.run(request, is_cancelled=lambda: flag["cancelled"]))
    await asyncio.sleep(0.2)

    started = time.monotonic()
    flag["cancelled"] = True
    with pytest.raises(CloudRunCancelled):
        await task

    assert time.monotonic() - started < 1.0


async def test_cancelling_a_provider_without_cancel_says_the_job_may_still_bill(tmp_path):
    from src.features.cloud.clock import MonotonicClock

    h = Harness(
        tmp_path,
        behaviour=FakeBehaviour(mode="async", duration_s=10_000, poll_after_s=300),
        supports_cancel=False,
        clock=MonotonicClock(),
    )
    task = asyncio.ensure_future(h.run())
    await asyncio.sleep(0.2)

    await h.session.cancel()
    with pytest.raises(CloudRunCancelled) as raised:
        await task

    assert raised.value.cancel_confirmed is False
    assert "cancel" not in h.provider.behaviour.calls
    assert any("may still finish this job and bill it" in (item.message or "") for item in h.progress)


async def test_a_run_cancelled_before_it_starts_never_reaches_the_provider(harness):
    h = harness()
    await h.session.cancel()

    with pytest.raises(CloudRunCancelled):
        await h.run()

    assert h.provider.behaviour.calls == []


async def test_one_unavailable_fetch_then_success_leaves_one_file_and_no_partial(harness, tmp_path):
    h = harness(behaviour=FakeBehaviour(mode="sync", fail_kind="unavailable", fail_stage="fetch", fail_times=1))

    outcome = await h.run()

    (artifact,) = outcome.artifacts
    assert h.provider.behaviour.calls.count("fetch") == 2
    assert sorted(path.name for path in (tmp_path / "scratch").rglob("*") if path.is_file()) == [artifact.path.name]
    assert not list((tmp_path / "scratch").rglob("*.part"))


async def test_the_download_cap_depends_on_the_kind_of_media(harness):
    image = harness(behaviour=FakeBehaviour(mode="sync"))
    await image.run()
    video = harness(behaviour=FakeBehaviour(mode="sync"))
    await video.run(task="txt2video", model=VIDEO)

    assert image.provider.behaviour.fetch_limits == [64 * 1024 * 1024]
    assert video.provider.behaviour.fetch_limits == [2048 * 1024 * 1024]


async def test_poll_errors_reset_after_a_success(harness):
    h = harness(behaviour=FakeBehaviour(mode="async", duration_s=60, poll_after_s=1))
    real_poll = h.provider.poll
    script = ["err"] * 4 + ["ok"] + ["err"] * 4
    failures = {"count": 0}

    async def flaky(job):
        step = script.pop(0) if script else "ok"
        if step == "err":
            failures["count"] += 1
            raise CloudError("unavailable", "Fake outage")
        return await real_poll(job)

    h.provider.poll = flaky

    outcome = await h.run()

    assert len(outcome.artifacts) == 1
    assert failures["count"] == 8


async def test_two_runs_on_one_session_keep_their_own_files(harness):
    h = harness(behaviour=FakeBehaviour(mode="async", duration_s=2, poll_after_s=1))

    first = await h.run(task="txt2video", model=VIDEO, prompt="one")
    second = await h.run(task="txt2video", model=VIDEO, prompt="two")

    paths = [first.artifacts[0].path, second.artifacts[0].path]
    assert paths[0] != paths[1]
    assert all(path.is_file() for path in paths)


async def test_a_slow_provider_cancel_does_not_hold_up_the_cancel(tmp_path):
    from src.features.cloud.clock import MonotonicClock

    h = Harness(tmp_path, behaviour=FakeBehaviour(mode="async", duration_s=10_000, poll_after_s=300), clock=MonotonicClock())

    async def slow_cancel(job):
        await asyncio.sleep(10)
        return True

    h.provider.cancel = slow_cancel
    task = asyncio.ensure_future(h.run())
    await asyncio.sleep(0.2)

    started = time.monotonic()
    await h.session.cancel()
    with pytest.raises(CloudRunCancelled) as raised:
        await task

    assert time.monotonic() - started < 1.5
    assert raised.value.cancel_confirmed is None
    assert any("may still finish this job and bill it" in (item.message or "") for item in h.progress)


async def test_a_provider_cancel_that_raises_is_reported_as_unconfirmed(tmp_path):
    from src.features.cloud.clock import MonotonicClock

    h = Harness(tmp_path, behaviour=FakeBehaviour(mode="async", duration_s=10_000, poll_after_s=300), clock=MonotonicClock())

    async def broken_cancel(job):
        raise RuntimeError("provider exploded")

    h.provider.cancel = broken_cancel
    task = asyncio.ensure_future(h.run())
    await asyncio.sleep(0.2)

    await h.session.cancel()
    with pytest.raises(CloudRunCancelled) as raised:
        await task

    assert raised.value.cancel_confirmed is False
    assert any("may still finish this job and bill it" in (item.message or "") for item in h.progress)
