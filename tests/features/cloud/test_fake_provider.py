from dataclasses import replace
from decimal import Decimal

import pytest

from src.features.cloud.contracts import CloudArtifact, CloudError, CloudHealth, CloudProvider
from src.features.cloud.testing.contract import (
    CONTRACT_CHECKS,
    SCENARIO_ASYNC,
    SCENARIO_ASYNC_FAILED,
    SCENARIO_CANCEL,
    SCENARIO_SYNC,
    ContractCase,
    applicable_checks,
    default_request,
    run_contract,
)
from src.features.cloud.testing.fake import FakeBehaviour, FakeClock, build_fake_provider, fake_specs

FAILURE_KINDS = ["auth", "credits", "rate_limited", "refused", "invalid_request", "unavailable", "timeout", "failed", "expired"]


def make_case(*, supports_cancel: bool = True) -> tuple[ContractCase, FakeClock]:
    clock = FakeClock()

    def make_provider(scenario: str):
        behaviour = {
            SCENARIO_SYNC: FakeBehaviour(mode="sync"),
            SCENARIO_ASYNC: FakeBehaviour(mode="async", queue_s=2, duration_s=10),
            SCENARIO_ASYNC_FAILED: FakeBehaviour(mode="async", duration_s=3, terminal="failed"),
            SCENARIO_CANCEL: FakeBehaviour(mode="async", duration_s=60),
        }.get(scenario, FakeBehaviour())
        return build_fake_provider(behaviour, clock=clock, supports_cancel=supports_cancel)

    async def advance(seconds: float) -> None:
        clock.advance(seconds)

    def probe(kind: str):
        async def run():
            provider = build_fake_provider(FakeBehaviour(fail_kind=kind, fail_stage="submit", retry_after_s=4.0), clock=clock)
            await provider.submit(default_request(fake_specs(), "sync"))

        return run

    return ContractCase(make_provider=make_provider, advance=advance, error_probes={kind: probe(kind) for kind in FAILURE_KINDS}), clock


@pytest.mark.parametrize("supports_cancel", [True, False], ids=["cancel", "no-cancel"])
@pytest.mark.parametrize("name", list(CONTRACT_CHECKS))
async def test_fake_provider_passes_each_contract_check(name, supports_cancel, tmp_path):
    case, _ = make_case(supports_cancel=supports_cancel)
    assert name in applicable_checks(case)
    await CONTRACT_CHECKS[name](case, tmp_path)


async def test_run_contract_reports_every_check_it_ran(tmp_path):
    case, _ = make_case()
    assert await run_contract(case, tmp_path) == list(CONTRACT_CHECKS)


def test_checks_are_skipped_when_a_scenario_is_not_offered():
    case, _ = make_case()
    case.scenarios = (SCENARIO_SYNC,)
    case.error_probes = {}
    assert applicable_checks(case) == ["discover_returns_valid_specs", "sync_submit_returns_result", "fetch_writes_artifacts"]


async def test_kit_catches_a_provider_that_claims_cancel_but_refuses(tmp_path):
    case, _ = make_case()
    original = case.make_provider

    def liar(scenario):
        provider = original(scenario)

        async def refuse(job):
            return False

        provider.cancel = refuse
        return provider

    case.make_provider = liar
    with pytest.raises(AssertionError, match="did not confirm"):
        await CONTRACT_CHECKS["cancel_semantics_match_support"](case, tmp_path)


async def test_kit_catches_a_provider_without_cancel_that_confirms_one(tmp_path):
    case, _ = make_case(supports_cancel=False)
    original = case.make_provider

    def liar(scenario):
        provider = original(scenario)

        async def confirm(job):
            return True

        provider.cancel = confirm
        return provider

    case.make_provider = liar
    with pytest.raises(AssertionError, match="without cancel support"):
        await CONTRACT_CHECKS["cancel_semantics_match_support"](case, tmp_path)


async def test_kit_catches_a_mislabelled_error_kind(tmp_path):
    case, _ = make_case()

    async def wrong():
        raise CloudError("failed", "nope")

    case.error_probes = {"auth": wrong}
    with pytest.raises(AssertionError, match="expected auth"):
        await CONTRACT_CHECKS["errors_map_to_kinds"](case, tmp_path)


async def test_kit_catches_a_probe_that_does_not_raise(tmp_path):
    case, _ = make_case()

    async def quiet():
        return None

    case.error_probes = {"auth": quiet}
    with pytest.raises(AssertionError, match="did not raise"):
        await CONTRACT_CHECKS["errors_map_to_kinds"](case, tmp_path)


async def test_kit_catches_invalid_specs(tmp_path):
    case, _ = make_case()
    original = case.make_provider

    def broken(scenario):
        provider = original(scenario)

        async def discover():
            return [replace(fake_specs()[0], provider_model_id="", tasks=frozenset())]

        provider.discover = discover
        return provider

    case.make_provider = broken
    with pytest.raises(AssertionError, match="provider_model_id"):
        await CONTRACT_CHECKS["discover_returns_valid_specs"](case, tmp_path)


async def test_kit_catches_a_job_that_never_finishes(tmp_path):
    case, _ = make_case()
    original = case.make_provider

    def stuck(scenario):
        provider = original(scenario)
        provider.behaviour.duration_s = 10 ** 9
        return provider

    case.make_provider = stuck
    with pytest.raises(AssertionError, match="terminal state"):
        await CONTRACT_CHECKS["async_poll_reaches_success"](case, tmp_path)


async def test_kit_catches_a_state_that_moves_backwards(tmp_path):
    from src.features.cloud.contracts import CloudStatus

    case, _ = make_case()
    original = case.make_provider

    def regress(scenario):
        provider = original(scenario)
        states = iter([CloudStatus(state="running"), CloudStatus(state="queued"), CloudStatus(state="failed")])

        async def poll(job):
            return next(states)

        provider.poll = poll
        return provider

    case.make_provider = regress
    with pytest.raises(AssertionError, match="backwards"):
        await CONTRACT_CHECKS["async_poll_reaches_failure"](case, tmp_path)


async def test_async_job_walks_queued_running_succeeded_on_the_injected_clock():
    clock = FakeClock()
    provider = build_fake_provider(FakeBehaviour(mode="async", queue_s=2, duration_s=10), clock=clock)
    job = await provider.submit(default_request(fake_specs(), "async"))
    assert job.result is None
    assert (await provider.poll(job)).state == "queued"
    clock.advance(5)
    running = await provider.poll(job)
    assert running.state == "running"
    assert 0 < running.progress < 1
    clock.advance(10)
    done = await provider.poll(job)
    assert done.state == "succeeded"
    assert done.result.cost.amount_usd == Decimal("0.04")


@pytest.mark.parametrize("kind", FAILURE_KINDS)
@pytest.mark.parametrize("stage", ["discover", "submit", "fetch"])
async def test_scripted_failure_raises_the_chosen_kind(kind, stage, tmp_path):
    provider = build_fake_provider(FakeBehaviour(fail_kind=kind, fail_stage=stage, retry_after_s=2.0))
    artifact = CloudArtifact(modality="image", index=0, data=b"abc")
    with pytest.raises(CloudError) as caught:
        if stage == "discover":
            await provider.discover()
        elif stage == "submit":
            await provider.submit(default_request(fake_specs(), "x"))
        else:
            await provider.fetch(artifact, tmp_path / "f.bin")
    assert caught.value.kind == kind
    assert caught.value.retry_after_s == 2.0


async def test_scripted_poll_failure_raises():
    clock = FakeClock()
    provider = build_fake_provider(FakeBehaviour(mode="async", fail_kind="unavailable", fail_stage="poll"), clock=clock)
    job = await provider.submit(default_request(fake_specs(), "x"))
    with pytest.raises(CloudError) as caught:
        await provider.poll(job)
    assert caught.value.kind == "unavailable"


async def test_expired_terminal_state_and_health():
    clock = FakeClock()
    provider = build_fake_provider(FakeBehaviour(mode="async", duration_s=1, terminal="expired"), clock=clock)
    job = await provider.submit(default_request(fake_specs(), "x"))
    clock.advance(5)
    assert (await provider.poll(job)).state == "expired"
    assert await provider.check() == CloudHealth(ok=True, message="fake")


async def test_fetch_respects_max_bytes(tmp_path):
    provider = build_fake_provider(FakeBehaviour())
    job = await provider.submit(default_request(fake_specs(), "x"))
    with pytest.raises(CloudError):
        await provider.fetch(job.result.artifacts[0], tmp_path / "f.bin", max_bytes=3)


async def test_base_fetch_refuses_inline_data_over_max_bytes(tmp_path):
    provider = build_fake_provider(FakeBehaviour())
    with pytest.raises(CloudError):
        await CloudProvider.fetch(provider, CloudArtifact(modality="image", index=0, data=b"abcdef"), tmp_path / "a.bin", max_bytes=3)
    assert not (tmp_path / "a.bin").exists()


async def test_base_fetch_writes_inline_data_and_rejects_empty_artifacts(tmp_path):
    provider = build_fake_provider(FakeBehaviour())
    written = await CloudProvider.fetch(provider, CloudArtifact(modality="image", index=0, data=b"abc"), tmp_path / "sub" / "a.bin")
    assert written.read_bytes() == b"abc"
    with pytest.raises(CloudError):
        await CloudProvider.fetch(provider, CloudArtifact(modality="image", index=0), tmp_path / "b.bin")
