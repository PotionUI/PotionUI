import logging
import time

import pytest

from src.features.cloud.clock import MonotonicClock
from src.features.cloud.testing.fake import FakeBehaviour
from src.features.generation.content_blocked_output import ContentBlockedGenerationOutput
from src.features.generation.model_repository import generation_model_repo
from src.features.generation.repository import generation_repo
from src.features.generation.status_tracker import GenerationState
from src.pipelines.outputs import GalleryGenerationOutput, ProgressGenerationOutput
from tests.features.cloud.cloud_generation_harness import SECRET, USER_ID, CloudGeneration
from tests.features.content_safety.fakes import build


def add_user(db, user_id=USER_ID):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (user_id, user_id, f"{user_id}@example.test"),
        )


def saved_files(db, generation_id="gen-1"):
    return [file.file_path for file in generation_repo.get_files(generation_id, is_final=True)]


@pytest.fixture
async def make(mock_db, tmp_path):
    add_user(mock_db)

    async def build_run(content_safety=None, timeout_seconds=1800, emit_model=False, **behaviour):
        return await CloudGeneration(
            tmp_path,
            FakeBehaviour(**behaviour),
            content_safety=content_safety,
            timeout_seconds=timeout_seconds,
            emit_model=emit_model,
        ).start()

    return build_run


async def test_a_sync_job_completes_and_its_image_lands_in_the_gallery(make, mock_db):
    run = await make(mode="sync")

    record = await run.run(quantity=1)

    assert record.state == GenerationState.COMPLETED
    (saved,) = saved_files(mock_db)
    assert (run.storage / saved).is_file()
    assert run.collected.of(GalleryGenerationOutput)


async def test_an_async_job_reports_progress_and_completes(make, mock_db):
    run = await make(mode="async", queue_s=2, duration_s=10, poll_after_s=3)

    record = await run.run()

    assert record.state == GenerationState.COMPLETED
    states = [output.state for output in run.collected.of(ProgressGenerationOutput)]
    assert any("Waiting at the provider" in state for state in states)
    assert "Downloading the result" in states
    assert len(saved_files(mock_db)) == 1


async def test_the_model_used_is_recorded_on_the_generation(make):
    run = await make(mode="sync")

    await run.run()

    models = generation_model_repo.get_by_generation("gen-1")
    assert [model.filename for model in models] == [run.slug]


async def test_a_batch_makes_one_gallery_file_per_image(make, mock_db):
    run = await make(mode="sync", outputs=3)

    record = await run.run(quantity=3)

    assert record.state == GenerationState.COMPLETED
    assert len(saved_files(mock_db)) == 3


@pytest.mark.parametrize(
    "kind,code",
    [
        ("credits", "cloud_credits"),
        ("refused", "cloud_refused"),
        ("rate_limited", "cloud_rate_limited"),
        ("auth", "cloud_auth"),
        ("unavailable", "cloud_unavailable"),
    ],
)
async def test_provider_failures_are_reported_with_their_own_error_category(make, kind, code):
    run = await make(mode="sync", fail_kind=kind, fail_stage="submit")

    record = await run.run()

    assert record.state == GenerationState.FAILED
    assert record.error_code == code


async def test_a_job_past_its_deadline_fails_as_a_timeout_and_is_cancelled_at_the_provider(make):
    run = await make(mode="async", duration_s=100_000, poll_after_s=30, timeout_seconds=90)

    record = await run.run()

    assert record.state == GenerationState.FAILED
    assert record.error_code == "cloud_timeout"
    assert "cancel" in run.behaviour.calls


async def test_cancelling_a_running_job_takes_effect_within_a_second(make):
    run = await make(mode="async", duration_s=100_000, poll_after_s=300)
    run.backend.clock = MonotonicClock()
    await run.submit()
    while "submit" not in run.behaviour.calls:
        await run_yield()

    started = time.monotonic()
    cancelled = await run.orchestrator.cancel_generation("gen-1")
    await run.finished()

    assert cancelled is True
    assert time.monotonic() - started < 1.0
    assert run.orchestrator.status_tracker.get("gen-1").state == GenerationState.CANCELLED
    assert "cancel" in run.behaviour.calls
    assert run.collected.of(GalleryGenerationOutput) == []


async def run_yield():
    import asyncio

    await asyncio.sleep(0)


async def test_the_content_safety_gate_applies_to_cloud_images(make, mock_db):
    manager, _, _ = build("blocked", scores=[0.99])
    run = await make(content_safety=manager, mode="sync")

    record = await run.run()

    assert saved_files(mock_db) == []
    assert run.collected.of(ContentBlockedGenerationOutput)
    assert record.state == GenerationState.FAILED
    assert record.error_code == "content_blocked"


async def test_the_api_key_never_reaches_pipe_configs_outputs_history_or_logs(make, mock_db, caplog):
    caplog.set_level(logging.DEBUG)
    run = await make(mode="sync")

    await run.run()

    assert run.prepared
    assert SECRET not in repr(run.prepared)
    assert SECRET not in repr(run.collected.outputs)
    assert SECRET not in caplog.text
    stored = generation_repo.get_by_id("gen-1")
    assert SECRET not in repr(vars(stored))


async def test_the_api_key_never_appears_when_the_provider_error_echoes_it(make, mock_db, caplog):
    caplog.set_level(logging.DEBUG)
    run = await make(mode="sync")

    async def leaky(request):
        raise RuntimeError(f"request failed, Authorization: Bearer {SECRET}")

    run.backend.provider.submit = leaky

    record = await run.run()

    assert record.state == GenerationState.FAILED
    stored = generation_repo.get_by_id("gen-1")
    assert SECRET not in repr(vars(stored))
    assert SECRET not in repr(run.collected.outputs)
    assert SECRET not in caplog.text
    assert SECRET not in repr(vars(record))


async def test_a_preset_that_also_emits_the_model_records_it_once(make):
    run = await make(mode="sync", emit_model=True)

    record = await run.run()

    assert record.state == GenerationState.COMPLETED
    assert any(pipe["name"] == "param_emitter" for pipe in run.prepared[0])
    assert [model.filename for model in generation_model_repo.get_by_generation("gen-1")] == [run.slug]
