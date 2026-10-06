from decimal import Decimal

import pytest

from src.plugin_api.cloud import CloudError, CloudHttp
from src.plugin_api.cloud_testing import (
    SCENARIO_ASYNC,
    SCENARIO_ASYNC_FAILED,
    SCENARIO_CANCEL,
    ContractCase,
    FakeClock,
    run_contract,
)

from backend.mapping import is_bfl_api_url, polling_target

from .fixtures import KEY, PNG
from .support import build, drive, polls, request_for


async def submitted(provider, model_id="flux-2-pro", **fields):
    return await provider.submit(await request_for(provider, model_id, **fields))


async def test_polling_goes_from_pending_to_generating_to_ready_with_growing_waits(provider, bfl):
    job = await submitted(provider, params={"output_format": "png"})

    first = await provider.poll(job)
    second = await provider.poll(job)
    third = await provider.poll(job)

    assert (first.state, first.poll_after_s) == ("queued", 1.5)
    assert (second.state, second.progress, second.poll_after_s) == ("running", 0.5, 2.25)
    assert third.state == "succeeded" and third.progress == 1.0
    (artifact,) = third.result.artifacts
    assert artifact.url == bfl.sample() and artifact.media_type == "image/png"
    assert third.result.seed_used == 1234 and third.result.provider_job_id == "task-1"
    assert [call["query"] for call in polls(bfl)] == [{"id": "task-1"}] * 3


async def test_the_wait_between_polls_is_capped(provider, bfl):
    bfl.poll_mode = "forever"
    job = await submitted(provider)

    waits = [(await provider.poll(job)).poll_after_s for _ in range(10)]

    assert waits == sorted(waits) and waits[-1] == 8.0


async def test_a_ready_picture_is_downloaded_from_the_signed_address_without_the_key(provider, bfl, tmp_path):
    job = await submitted(provider)
    status = await drive(provider, job)

    written = await provider.fetch(status.result.artifacts[0], tmp_path / "out.png")

    assert written.read_bytes() == PNG
    assert len(bfl.cdn_requests) == 1 and "x-key" not in {name.lower() for name in bfl.cdn_requests[0]["headers"]}
    assert bfl.cdn_requests[0]["query"]["sig"] == "SIGNED"
    assert all(call["headers"]["x-key"] == KEY for call in bfl.requests)


async def test_an_expired_signed_address_is_an_expired_result(provider, bfl, tmp_path):
    bfl.poll_mode = "expired_sample"
    job = await submitted(provider)
    status = await drive(provider, job)

    with pytest.raises(CloudError) as raised:
        await provider.fetch(status.result.artifacts[0], tmp_path / "gone.png")

    assert raised.value.kind == "expired"
    assert "expired" in raised.value.user_message and "HTTP 403" in raised.value.detail
    assert not list(tmp_path.glob("gone.png*"))


async def test_the_settled_cost_in_credits_is_recorded_in_dollars(provider, bfl):
    job = await submitted(provider)
    status = await drive(provider, job)

    cost = status.result.cost
    assert cost.amount_usd == Decimal("0.045") and cost.source == "provider"
    assert cost.detail["credits"] == "4.5" and cost.detail["reported_by"] == "settled"


async def test_the_submit_cost_is_recorded_when_no_settled_cost_comes_back(provider, bfl):
    bfl.poll_mode = "no_settled_cost"
    job = await submitted(provider)
    status = await drive(provider, job)

    cost = status.result.cost
    assert cost.amount_usd == Decimal("0.04") and cost.source == "provider"
    assert cost.detail == {"credits": "4.0", "reported_by": "submit", "input_mp": "0.0", "output_mp": "1.05"}


async def test_no_cost_is_invented_when_bfl_reports_none(provider, bfl):
    bfl.submit_mode, bfl.poll_mode = "no_cost", "no_settled_cost"
    job = await submitted(provider)
    status = await drive(provider, job)

    assert status.result.cost is None
    assert status.result.artifacts


@pytest.mark.parametrize("mode,user_message,reason", [
    ("request_moderated", "refused this prompt or picture", "Violence"),
    ("content_moderated", "blocked the finished picture", "Sexual Content"),
])
async def test_both_moderation_states_are_refusals_with_plain_words_for_users(provider, bfl, mode, user_message, reason):
    bfl.poll_mode = mode
    job = await submitted(provider)

    with pytest.raises(CloudError) as raised:
        await drive(provider, job)

    error = raised.value
    assert error.kind == "refused"
    assert user_message in error.user_message and "BFL" in error.user_message
    assert reason not in error.user_message and "Moderated" not in error.user_message
    assert reason in error.detail and ("Content Moderated" if mode == "content_moderated" else "Request Moderated") in error.detail


async def test_content_moderation_tells_the_admin_it_may_be_billed(provider, bfl):
    bfl.poll_mode = "content_moderated"
    job = await submitted(provider)

    with pytest.raises(CloudError) as raised:
        await drive(provider, job)

    assert "billed" in raised.value.detail


async def test_an_error_status_is_a_failed_job_with_the_reason_for_the_admin(provider, bfl):
    bfl.poll_mode = "error"
    job = await submitted(provider)

    status = await drive(provider, job)

    assert status.state == "failed" and status.result is None
    assert "render crashed upstream" in status.message


async def test_a_task_bfl_no_longer_knows_has_expired(provider, bfl):
    bfl.poll_mode = "not_found"
    job = await submitted(provider)

    status = await drive(provider, job)

    assert status.state == "expired" and "task-1" in status.message


async def test_ready_without_a_picture_fails(provider, bfl):
    bfl.poll_mode = "ready_without_sample"
    job = await submitted(provider)

    with pytest.raises(CloudError) as raised:
        await provider.poll(job)

    assert raised.value.kind == "failed"


async def test_pending_after_generating_does_not_move_the_state_backwards(provider, bfl):
    bfl.poll_mode = "pending_after_running"
    job = await submitted(provider)

    states = [(await provider.poll(job)).state for _ in range(3)]

    assert states == ["running", "running", "running"]


async def test_polling_stops_with_a_timeout_once_the_backend_limit_has_passed(bfl):
    clock = FakeClock()
    made, http = build(bfl, clock=clock, timeout_seconds=60)
    bfl.poll_mode = "forever"
    try:
        job = await submitted(made)
        await made.poll(job)
        clock.advance(61)
        before = len(polls(bfl))

        with pytest.raises(CloudError) as raised:
            await made.poll(job)
    finally:
        await http.close()

    assert raised.value.kind == "timeout" and "bill" in raised.value.user_message
    assert len(polls(bfl)) == before


async def test_cancel_cannot_reach_bfl_and_stops_polling_locally(provider, bfl):
    bfl.poll_mode = "forever"
    job = await submitted(provider)
    await provider.poll(job)
    before = len(bfl.requests)

    confirmed = await provider.cancel(job)
    status = await provider.poll(job)

    assert confirmed is False and provider.supports_cancel is False
    assert status.state == "cancelled" and "may still finish this job and bill it" in status.message
    assert len(bfl.requests) == before


async def test_a_polling_address_on_a_foreign_host_is_not_followed(provider, bfl):
    bfl.polling_host = "foreign"
    job = await submitted(provider)

    status = await drive(provider, job)

    assert status.state == "succeeded"
    assert job.handle["target"] == "/get_result" and job.handle["params"] == {"id": "task-1"}
    assert not [call for call in bfl.cdn_requests if call["path"] == "/v1/get_result"]


async def test_a_missing_polling_address_falls_back_to_get_result(provider, bfl):
    bfl.polling_host = "missing"
    job = await submitted(provider)

    assert (await drive(provider, job)).state == "succeeded"
    assert polls(bfl)


@pytest.mark.parametrize("url,trusted", [
    ("https://api.bfl.ai/v1/get_result?id=1", True),
    ("https://api.eu2.bfl.ai/v1/get_result?id=1", True),
    ("https://api.us1.bfl.ai/v1/get_result?id=1", True),
    ("https://api.bfl.ml/v1/get_result?id=1", True),
    ("http://api.eu.bfl.ai/v1/get_result?id=1", False),
    ("https://api.eu.bfl.ai:8443/v1/get_result?id=1", False),
    ("https://delivery-eu1.bfl.ai/results/x.png", False),
    ("https://api.bfl.ai.evil.example/v1/get_result", False),
    ("https://evilbfl.ai/v1/get_result", False),
    ("https://api.x.y.bfl.ai/v1/get_result", False),
])
def test_only_bfl_api_hosts_are_trusted_with_the_key(url, trusted):
    assert is_bfl_api_url(url) is trusted


def test_a_regional_polling_address_is_used_only_when_the_backend_talks_to_bfl_itself():
    http = CloudHttp("https://api.bfl.ai/v1", auth_headers={"x-key": KEY})
    proxied = CloudHttp("https://proxy.example/v1", auth_headers={"x-key": KEY})
    payload = {"polling_url": "https://api.eu4.bfl.ai/v1/get_result?id=t"}

    assert polling_target(http, payload, "t") == {"target": payload["polling_url"], "origin": "https://api.eu4.bfl.ai"}
    assert polling_target(proxied, payload, "t") == {"target": "/get_result", "params": {"id": "t"}, "origin": None}
    assert polling_target(http, {"polling_url": "https://api.bfl.ai/v1/get_result?id=t"}, "t")["origin"] is None


async def test_a_regional_poll_carries_the_key_only_to_that_bfl_host(provider, bfl, monkeypatch):
    import backend.mapping as mapping

    regional_origin = bfl.regional_url
    monkeypatch.setattr(mapping, "is_bfl_api_url", lambda url: url.startswith((regional_origin, bfl.api_url)))
    bfl.polling_host = "regional"
    job = await submitted(provider)

    status = await drive(provider, job)

    assert job.handle["origin"] == regional_origin
    assert status.state == "succeeded"
    assert len(bfl.regional_requests) == 3 and all(call["headers"]["x-key"] == KEY for call in bfl.regional_requests)
    assert polls(bfl) == []


async def test_the_contract_kit_async_scenarios_pass(provider, bfl, tmp_path):
    async def advance(seconds):
        return None

    def make(scenario):
        bfl.polls = 0
        bfl.poll_mode = "error" if scenario == SCENARIO_ASYNC_FAILED else "forever" if scenario == SCENARIO_CANCEL else "ok"
        return provider

    async def probe(mode):
        bfl.submit_mode = mode
        try:
            await submitted(provider)
        finally:
            bfl.submit_mode = "ok"

    case = ContractCase(
        make_provider=make, advance=advance, scenarios=(SCENARIO_ASYNC, SCENARIO_ASYNC_FAILED, SCENARIO_CANCEL),
        error_probes={
            "credits": lambda: probe("credits"),
            "refused": lambda: probe("forbidden"),
            "rate_limited": lambda: probe("rate"),
            "invalid_request": lambda: probe("invalid"),
            "unavailable": lambda: probe("down"),
        },
    )

    ran = await run_contract(case, tmp_path)

    assert {"discover_returns_valid_specs", "async_poll_reaches_success", "async_poll_reaches_failure",
            "fetch_writes_artifacts", "cancel_semantics_match_support", "errors_map_to_kinds"} <= set(ran)
