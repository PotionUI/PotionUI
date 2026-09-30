from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Optional

from src.features.cloud.contracts import (
    MODALITIES,
    TERMINAL_STATES,
    CloudError,
    CloudJob,
    CloudModelSpec,
    CloudProvider,
    CloudRequest,
    CloudResult,
    CloudStatus,
    spec_problems,
)
from src.pipelines.cloud import CLOUD_ERROR_KINDS

MAX_POLLS = 200
_ACTIVE_RANK = {"queued": 0, "running": 1}

SCENARIO_SYNC = "sync"
SCENARIO_ASYNC = "async"
SCENARIO_ASYNC_FAILED = "async_failed"
SCENARIO_CANCEL = "cancel"

ProviderFactory = Callable[[str], CloudProvider]
RequestBuilder = Callable[[list[CloudModelSpec], str], CloudRequest]
ErrorProbe = Callable[[], Awaitable[object]]


def default_request(specs: list[CloudModelSpec], scenario: str) -> CloudRequest:
    spec = specs[0]
    task = sorted(spec.tasks)[0]
    return CloudRequest(model=spec, task=task, prompt="a lighthouse at dusk", client_reference=f"contract-{scenario}")


@dataclass
class ContractCase:
    make_provider: ProviderFactory
    advance: Callable[[float], Awaitable[None]]
    scenarios: tuple[str, ...] = (SCENARIO_SYNC, SCENARIO_ASYNC, SCENARIO_ASYNC_FAILED, SCENARIO_CANCEL)
    make_request: RequestBuilder = default_request
    error_probes: Mapping[str, ErrorProbe] = field(default_factory=dict)


async def _discover(case: ContractCase, scenario: str) -> tuple[CloudProvider, list[CloudModelSpec]]:
    provider = case.make_provider(scenario)
    specs = await provider.discover()
    return provider, specs


async def _drive(provider: CloudProvider, job: CloudJob, advance: Callable[[float], Awaitable[None]]) -> CloudStatus:
    seen_rank = -1
    for _ in range(MAX_POLLS):
        status = await provider.poll(job)
        assert status.state in _ACTIVE_RANK or status.state in TERMINAL_STATES, f"unknown state {status.state!r}"
        if status.progress is not None:
            assert 0.0 <= status.progress <= 1.0, f"progress {status.progress} outside 0..1"
        if status.state in TERMINAL_STATES:
            return status
        rank = _ACTIVE_RANK[status.state]
        assert rank >= seen_rank, "state moved backwards from running to queued"
        seen_rank = rank
        await advance(status.poll_after_s or job.poll_after_s or 1.0)
    raise AssertionError(f"job did not reach a terminal state in {MAX_POLLS} polls")


def _assert_result(result: Optional[CloudResult]) -> CloudResult:
    assert result is not None, "success carried no result"
    assert result.artifacts, "result has no artifacts"
    indices = [artifact.index for artifact in result.artifacts]
    assert len(set(indices)) == len(indices), "artifact indices repeat"
    for artifact in result.artifacts:
        assert artifact.data is not None or artifact.url, "artifact has neither data nor url"
        assert artifact.modality in MODALITIES
    return result


async def discover_returns_valid_specs(case: ContractCase, workdir: Path) -> None:
    _, specs = await _discover(case, case.scenarios[0])
    assert specs, "discover returned no models"
    ids = [spec.provider_model_id for spec in specs]
    assert len(set(ids)) == len(ids), "provider_model_id repeats"
    for spec in specs:
        problems = spec_problems(spec)
        assert not problems, f"{spec.provider_model_id}: {problems}"


async def sync_submit_returns_result(case: ContractCase, workdir: Path) -> None:
    provider, specs = await _discover(case, SCENARIO_SYNC)
    job = await provider.submit(case.make_request(specs, SCENARIO_SYNC))
    assert job.job_id
    _assert_result(job.result)


async def async_poll_reaches_success(case: ContractCase, workdir: Path) -> None:
    provider, specs = await _discover(case, SCENARIO_ASYNC)
    job = await provider.submit(case.make_request(specs, SCENARIO_ASYNC))
    assert job.result is None, "async scenario returned a result at submit"
    status = await _drive(provider, job, case.advance)
    assert status.state == "succeeded", f"ended in {status.state}: {status.message}"
    _assert_result(status.result)


async def async_poll_reaches_failure(case: ContractCase, workdir: Path) -> None:
    provider, specs = await _discover(case, SCENARIO_ASYNC_FAILED)
    job = await provider.submit(case.make_request(specs, SCENARIO_ASYNC_FAILED))
    status = await _drive(provider, job, case.advance)
    assert status.state in ("failed", "expired"), f"ended in {status.state}"
    assert status.result is None, "failed job carried a result"


async def fetch_writes_artifacts(case: ContractCase, workdir: Path) -> None:
    scenario = SCENARIO_SYNC if SCENARIO_SYNC in case.scenarios else SCENARIO_ASYNC
    provider, specs = await _discover(case, scenario)
    job = await provider.submit(case.make_request(specs, scenario))
    result = job.result
    if result is None:
        status = await _drive(provider, job, case.advance)
        assert status.state == "succeeded"
        result = status.result
    result = _assert_result(result)
    for artifact in result.artifacts:
        dest = workdir / f"artifact-{artifact.index}.bin"
        written = await provider.fetch(artifact, dest)
        assert Path(written).exists(), "fetch returned a path that does not exist"
        assert Path(written).stat().st_size > 0, "fetched file is empty"


async def cancel_semantics_match_support(case: ContractCase, workdir: Path) -> None:
    provider, specs = await _discover(case, SCENARIO_CANCEL)
    job = await provider.submit(case.make_request(specs, SCENARIO_CANCEL))
    assert job.result is None, "cancel scenario needs an async job"
    confirmed = await provider.cancel(job)
    if provider.supports_cancel:
        assert confirmed is True, "provider claims cancel support but did not confirm"
        status = await _drive(provider, job, case.advance)
        assert status.state == "cancelled", f"cancelled job ended in {status.state}"
    else:
        assert confirmed is False, "provider without cancel support confirmed a cancel"


async def errors_map_to_kinds(case: ContractCase, workdir: Path) -> None:
    assert case.error_probes, "no error probes supplied"
    for kind, probe in case.error_probes.items():
        assert kind in CLOUD_ERROR_KINDS, f"probe names unknown kind {kind!r}"
        try:
            await probe()
        except CloudError as error:
            assert error.kind == kind, f"expected {kind}, got {error.kind}"
            assert error.user_message, f"{kind}: empty user_message"
            assert isinstance(error.detail, str)
            if kind == "rate_limited":
                assert error.retry_after_s is None or error.retry_after_s >= 0
        else:
            raise AssertionError(f"probe for {kind} did not raise CloudError")


CONTRACT_CHECKS: Mapping[str, Callable[[ContractCase, Path], Awaitable[None]]] = {
    "discover_returns_valid_specs": discover_returns_valid_specs,
    "sync_submit_returns_result": sync_submit_returns_result,
    "async_poll_reaches_success": async_poll_reaches_success,
    "async_poll_reaches_failure": async_poll_reaches_failure,
    "fetch_writes_artifacts": fetch_writes_artifacts,
    "cancel_semantics_match_support": cancel_semantics_match_support,
    "errors_map_to_kinds": errors_map_to_kinds,
}

_REQUIRED_SCENARIOS = {
    "sync_submit_returns_result": (SCENARIO_SYNC,),
    "async_poll_reaches_success": (SCENARIO_ASYNC,),
    "async_poll_reaches_failure": (SCENARIO_ASYNC_FAILED,),
    "cancel_semantics_match_support": (SCENARIO_CANCEL,),
}


def applicable_checks(case: ContractCase) -> list[str]:
    names = []
    for name in CONTRACT_CHECKS:
        if name == "errors_map_to_kinds" and not case.error_probes:
            continue
        if name == "fetch_writes_artifacts" and not {SCENARIO_SYNC, SCENARIO_ASYNC} & set(case.scenarios):
            continue
        if any(scenario not in case.scenarios for scenario in _REQUIRED_SCENARIOS.get(name, ())):
            continue
        names.append(name)
    return names


async def run_contract(case: ContractCase, workdir: Path) -> list[str]:
    ran = applicable_checks(case)
    for name in ran:
        await CONTRACT_CHECKS[name](case, workdir)
    return ran
