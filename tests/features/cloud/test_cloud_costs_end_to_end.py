import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from src.features.cloud.clock import MonotonicClock
from src.features.cloud.cost_repository import GenerationCostRepository
from src.features.cloud.testing.fake import FakeBehaviour
from src.features.generation.dto import PromptPair
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.repository import generation_repo
from src.features.generation.routes import GenerationController
from src.features.generation.status_tracker import GenerationState
from src.pipelines.outputs import CostGenerationOutput
from tests.features.cloud.cloud_generation_harness import BACKEND_ID, USER_ID, CloudGeneration

AMOUNT = "0.0731"
SEED = 5


def add_user(db, user_id=USER_ID):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (user_id, user_id, f"{user_id}@example.test"),
        )


@pytest.fixture
async def make(mock_db, tmp_path):
    add_user(mock_db)

    async def build_run(**behaviour):
        return await CloudGeneration(tmp_path, FakeBehaviour(**behaviour)).start()

    return build_run


def costs(generation_id="gen-1"):
    return GenerationCostRepository().for_generation(generation_id)


def without_provider_cost(run):
    original = run.backend.provider._result

    def result(*args, **kwargs):
        return replace(original(*args, **kwargs), cost=None)

    run.backend.provider._result = result


async def test_a_successful_run_writes_one_row_with_the_provider_cost(make):
    run = await make(mode="sync", cost_usd=Decimal(AMOUNT))

    record = await run.run()

    assert record.state == GenerationState.COMPLETED
    (row,) = costs()
    assert (row["amount_usd"], row["source"], row["backend_id"]) == (AMOUNT, "provider", BACKEND_ID)
    assert row["model_id"] is not None
    assert row["detail"]["task"] == "txt2img" and row["detail"]["outputs"] == 1


async def test_a_run_where_the_provider_reports_no_cost_writes_an_estimate_from_the_catalog(make):
    run = await make(mode="sync")
    without_provider_cost(run)

    await run.run()

    (row,) = costs()
    assert (row["amount_usd"], row["source"]) == ("0.04", "estimate")
    assert row["detail"]["lines"][0]["unit"] == "image"


async def test_an_estimate_is_charged_per_image_produced(make):
    run = await make(mode="sync", outputs=3)
    without_provider_cost(run)

    await run.run(quantity=3)

    (row,) = costs()
    assert (row["amount_usd"], row["source"]) == ("0.12", "estimate")


async def test_a_failed_run_writes_no_row(make):
    run = await make(mode="sync", fail_kind="credits", fail_stage="submit")

    record = await run.run()

    assert record.state == GenerationState.FAILED
    assert costs() == []


async def test_a_run_that_fails_while_downloading_writes_no_row(make):
    run = await make(mode="async", duration_s=1, fail_kind="failed", fail_stage="fetch")

    record = await run.run()

    assert record.state == GenerationState.FAILED
    assert costs() == []


async def test_a_cancelled_run_writes_no_row(make):
    run = await make(mode="async", duration_s=100_000, poll_after_s=300)
    run.backend.clock = MonotonicClock()
    await run.submit()
    while "submit" not in run.behaviour.calls:
        await asyncio.sleep(0)

    started = time.monotonic()
    await run.orchestrator.cancel_generation("gen-1")
    await run.finished()

    assert time.monotonic() - started < 1.0
    assert run.orchestrator.status_tracker.get("gen-1").state == GenerationState.CANCELLED
    assert costs() == []


async def test_two_distinct_prompts_write_two_rows(make):
    run = await make(mode="sync", cost_usd=Decimal("0.05"))
    request = run.request(quantity=2, seed=SEED)
    request.prompts = [PromptPair(positive="a {cat|dog|owl|fox|bat|ant|eel|yak}", negative="")]

    with patch("src.features.generation.orchestrator.generate_ulid", return_value="gen-1"):
        await run.orchestrator.start_generation(request, USER_ID, output_callback=run.collected)
    await run.finished()

    rows = costs()
    assert [row["amount_usd"] for row in rows] == ["0.05", "0.05"]
    summary = GenerationCostRepository().summaries(["gen-1"])["gen-1"]
    assert summary == {"amount_usd": "0.10", "source": "provider", "entries": 2, "unpriced": 0}


async def test_the_admin_generation_detail_shows_the_cost(make):
    run = await make(mode="sync", cost_usd=Decimal(AMOUNT))
    await run.run()
    controller = GenerationController(
        Mock(set_queue_listener=Mock()), Mock(), Mock(), Mock(get_report=Mock(return_value=None)), GenerationCostRepository(),
    )

    response = await controller.admin_get_generation("gen-1")

    cost = response.data["cost"]
    assert cost["amount_usd"] == AMOUNT and cost["source"] == "provider" and cost["entries"] == 1
    assert cost["items"][0]["backend_id"] == BACKEND_ID


async def test_the_admin_spend_total_reflects_the_run(make):
    run = await make(mode="sync", cost_usd=Decimal(AMOUNT))
    await run.run()

    spend = GenerationCostRepository().spend()

    assert spend["total_usd"] == AMOUNT
    assert spend["by_backend"][0]["backend_id"] == BACKEND_ID
    assert spend["by_model"][0]["model"] == "Fake Image"


async def test_nothing_a_client_receives_carries_the_amount(make):
    run = await make(mode="sync", cost_usd=Decimal(AMOUNT))
    await run.run()
    serializer = GenerationOutputSerializer("gen-1", "p")

    delivered = [serializer.serialize_output(output) for output in run.collected.outputs]
    record = run.orchestrator.status_tracker.get("gen-1")
    persisted = generation_repo.get_by_id("gen-1", user_id=USER_ID, include_files=True)
    payloads = [delivered, record.__dict__, persisted.to_dict(include_files=True, include_tags=True)]

    assert not run.collected.of(CostGenerationOutput)
    for payload in payloads:
        text = json.dumps(payload, default=str).lower()
        for word in ("cost", "price", "usd", AMOUNT):
            assert word not in text, word
    assert len(costs()) == 1
