import asyncio
from types import SimpleNamespace

import pytest

import organize_actions
from src.plugin_api.organize import (
    OrganizeActionBlocked,
    OrganizeActionRetry,
    OrganizeActionSkipped,
    OrganizeChange,
    OrganizeItem,
)

SHA = "a" * 64


def item(sha=SHA, model_id="m1"):
    return OrganizeItem(subject="model", item_id=model_id, user_id=None, data={"sha256": sha})


@pytest.fixture
def fetched(monkeypatch):
    calls = []
    state = {"summary": {"successful": 1, "failed": 0}, "raises": None}

    async def fake_fetch(provider, model_ids):
        calls.append((provider, list(model_ids)))
        if state["raises"] is not None:
            raise state["raises"]
        return state["summary"]

    monkeypatch.setattr(organize_actions, "run_provider_fetch", fake_fetch)

    def refuse_app_loop(coro):
        coro.close()
        raise RuntimeError("no app loop in tests")

    monkeypatch.setattr(organize_actions, "run_on_app_loop", refuse_app_loop)
    return SimpleNamespace(calls=calls, state=state)


def failing(code, reason="x"):
    return {"successful": 0, "failed": 1, "errors": [{"model_id": "m1", "reason": reason, "code": code}]}


def test_a_successful_fetch_reports_one_change_for_the_model(fetched):
    changes = organize_actions.fetch_info(item(), {}, None)

    assert changes == [OrganizeChange(target_type="plugin", target_id="m1", target_name="CivitAI")]
    assert fetched.calls == [("civitai", ["m1"])]


def test_a_model_without_a_digest_waits_and_nothing_is_fetched(fetched):
    with pytest.raises(OrganizeActionRetry) as waiting:
        organize_actions.fetch_info(item(sha=None), {}, None)

    assert waiting.value.code == "unverified"
    assert fetched.calls == []


def test_a_missing_record_is_a_permanent_skip(fetched):
    fetched.state["summary"] = failing("not_found")

    with pytest.raises(OrganizeActionSkipped) as skipped:
        organize_actions.fetch_info(item(), {}, None)

    assert skipped.value.code == "not_found"
    assert skipped.value.message == "No model with this file hash on CivitAI"


def test_a_false_positive_is_skipped_with_the_caveat_in_its_result(fetched):
    fetched.state["summary"] = failing("hash_mismatch")

    with pytest.raises(OrganizeActionSkipped) as skipped:
        organize_actions.fetch_info(item(), {}, None)

    assert skipped.value.code == "hash_mismatch"
    assert skipped.value.message == "CivitAI answered with a different file, so the answer was ignored"


@pytest.mark.parametrize("code", ["unreachable", "rate_limited"])
def test_outages_and_rate_limits_are_retried_later(fetched, code):
    fetched.state["summary"] = failing(code)

    with pytest.raises(OrganizeActionRetry) as retry:
        organize_actions.fetch_info(item(), {}, None)

    assert retry.value.code == code
    assert retry.value.after_s == organize_actions.RETRY_AFTER_S


def test_an_unhashable_file_and_unknown_errors_are_retried(fetched):
    fetched.state["summary"] = failing("no_hash")
    with pytest.raises(OrganizeActionRetry) as unhashed:
        organize_actions.fetch_info(item(), {}, None)
    fetched.state["summary"] = failing("error", "boom")
    with pytest.raises(OrganizeActionRetry) as unknown:
        organize_actions.fetch_info(item(), {}, None)

    assert unhashed.value.code == "unverified"
    assert (unknown.value.code, unknown.value.message) == ("error", "boom")


def test_a_provider_that_is_not_set_up_blocks_the_rule(fetched):
    from src.plugin_api.models import ProviderFetchException

    fetched.state["raises"] = ProviderFetchException("Provider 'civitai' is not initialized. Configure it.")

    with pytest.raises(OrganizeActionBlocked) as blocked:
        organize_actions.fetch_info(item(), {}, None)

    assert blocked.value.code == "provider_unavailable"


def test_a_plugin_veto_of_the_fetch_is_a_skip_not_a_pause(fetched):
    from src.plugin_api.models import ProviderFetchException

    fetched.state["raises"] = ProviderFetchException("Provider fetch blocked by plugin")

    with pytest.raises(OrganizeActionSkipped) as skipped:
        organize_actions.fetch_info(item(), {}, None)

    assert skipped.value.code == "vetoed"


def test_when_the_app_loop_exists_the_fetch_is_handed_to_it(monkeypatch):
    handed = []

    async def fake_fetch(provider, model_ids):
        return {"successful": 1}

    def on_app_loop(coro):
        handed.append(coro)
        return SimpleNamespace(result=lambda: asyncio.run(coro))

    monkeypatch.setattr(organize_actions, "run_provider_fetch", fake_fetch)
    monkeypatch.setattr(organize_actions, "run_on_app_loop", on_app_loop)

    changes = organize_actions.fetch_info(item(), {}, None)

    assert len(handed) == 1
    assert [c.target_name for c in changes] == ["CivitAI"]
