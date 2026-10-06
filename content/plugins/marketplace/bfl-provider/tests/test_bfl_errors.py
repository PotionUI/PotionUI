from decimal import Decimal

import pytest

from src.plugin_api.cloud import CloudError

from .fixtures import KEY
from .support import build, request_for, submits


@pytest.mark.parametrize("mode,kind", [
    ("credits", "credits"), ("forbidden", "refused"), ("rate", "rate_limited"),
    ("invalid", "invalid_request"), ("down", "unavailable"), ("broken", "unavailable"),
])
async def test_submit_failures_map_to_their_kinds(provider, bfl, mode, kind):
    request = await request_for(provider)
    bfl.submit_mode = mode

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == kind and raised.value.user_message
    assert raised.value.request_sent is True and provider.idempotent_submit is False


async def test_a_bad_key_is_an_auth_error_that_never_shows_the_key(bfl):
    made, http = build(bfl, api_key="bfl-wrong-key-SECRETVALUE-987654")
    try:
        with pytest.raises(CloudError) as raised:
            await made.submit(await request_for(made))
    finally:
        await http.close()

    error = raised.value
    assert error.kind == "auth" and "API key" in error.user_message
    assert "SECRETVALUE" not in error.detail and "SECRETVALUE" not in error.user_message
    assert error.detail.startswith("HTTP 401")


async def test_a_rate_limit_keeps_the_retry_delay_bfl_asked_for(provider, bfl):
    request = await request_for(provider)
    bfl.submit_mode = "rate"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == "rate_limited" and raised.value.retry_after_s == 3.0
    assert "active tasks" in raised.value.detail


async def test_a_validation_error_names_the_field_for_the_admin(provider, bfl):
    request = await request_for(provider)
    bfl.submit_mode = "invalid"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert "width: must be a multiple of 16" in raised.value.detail
    assert "width" not in raised.value.user_message


async def test_an_answer_without_a_task_id_fails(provider, bfl):
    request = await request_for(provider)
    bfl.submit_mode = "no_id"

    with pytest.raises(CloudError) as raised:
        await provider.submit(request)

    assert raised.value.kind == "failed"


async def test_the_key_goes_in_the_x_key_header_and_nowhere_else(provider, bfl):
    await provider.submit(await request_for(provider))

    call = submits(bfl)[-1]
    assert call["headers"]["x-key"] == KEY
    assert "Authorization" not in call["headers"]
    assert KEY not in str(call["body"]) and KEY not in str(call["query"])


async def test_the_health_check_reads_the_credit_balance(provider, bfl):
    health = await provider.check()

    assert health.ok is True and health.credits_usd == Decimal("12.345")
    assert bfl.requests[-1]["path"] == "/v1/credits"


async def test_the_health_check_reports_a_bad_key_in_plain_words(provider, bfl):
    bfl.credits_mode = "auth"

    health = await provider.check()

    assert health.ok is False and "API key" in health.message
