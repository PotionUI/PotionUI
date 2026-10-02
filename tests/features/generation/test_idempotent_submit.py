import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.features.generation.dto import GenerationRequest
from src.features.generation.exceptions import DuplicateIdempotencyKey, IdempotencyKeyConflict
from src.features.generation.orchestrator import GenerationOrchestrator, _submission_fingerprint
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository
from src.features.generation.status_tracker import GenerationStatusTracker


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])

    with patch("src.features.generation.orchestrator.bind_form", side_effect=_passthrough):
        yield


class Harness:
    def __init__(self, db):
        for user_id in ("u1", "u2"):
            with db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
                    (user_id, user_id, f"{user_id}@example.test"),
                )
        self.repo = GenerationRepository()
        self.backend = Mock(backend_id="b1", engine="native", name="Local")
        self.backend.start_generation = AsyncMock()
        registry = Mock()
        registry.select_backend_for_generation = Mock(return_value=self.backend)
        registry.get_backend = Mock(return_value=self.backend)
        preset = Mock(engine="native", version="1.0.0")
        loader = Mock(load_preset_by_id=Mock(return_value=preset))
        builder = Mock()
        builder.build_pipeline = Mock(return_value=Mock(pipes=[{"name": "generator", "config": {}}], preset_template=preset))
        self.tracker = GenerationStatusTracker()
        self.orchestrator = GenerationOrchestrator(
            pipeline_builder=builder,
            backend_registry=registry,
            connection_hub=Mock(),
            settings=Mock(get_setting=Mock(return_value="/outputs")),
            output_processor=Mock(process_output=AsyncMock(return_value={"handler": "H", "processed": True})),
            preset_template_loader=loader,
            status_tracker=self.tracker,
        )

    def request(self, key=None, prompt="a cat", form_data=None):
        return GenerationRequest(
            preset_id="preset-1", prompt=prompt, form_data=form_data or {}, idempotency_key=key
        )

    async def submit(self, user_id="u1", key=None, **request_fields):
        return await self.orchestrator.start_generation(self.request(key, **request_fields), user_id)

    def rows(self, user_id=None):
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if user_id:
                cursor.execute("SELECT id, status, idempotency_key FROM generations WHERE user_id = ?", (user_id,))
            else:
                cursor.execute("SELECT id, status, idempotency_key FROM generations")
            return [dict(row) for row in cursor.fetchall()]


@pytest.fixture
def harness(mock_db):
    return Harness(mock_db)


@pytest.mark.asyncio
async def test_a_second_submit_with_the_same_key_returns_the_first_and_starts_nothing(harness):
    first = await harness.submit(key="k1")
    second = await harness.submit(key="k1")

    assert second["generation_id"] == first["generation_id"]
    assert second["status"]["id"] == first["generation_id"]
    assert second["queue_position"] is None
    assert harness.backend.start_generation.await_count == 1
    assert harness.orchestrator.backend_registry.select_backend_for_generation.call_count == 1
    assert len(harness.rows()) == 1


@pytest.mark.asyncio
async def test_concurrent_submits_with_one_key_make_exactly_one_generation(harness):
    routed = []

    async def slow_route(routing_request):
        routed.append(routing_request)
        await asyncio.sleep(0.01)
        return Mock(
            chosen=harness.backend,
            summary=lambda: {"reason": "r"},
            to_trace_dict=lambda: {"chosen": {}},
        )

    harness.orchestrator.router = Mock(route=slow_route)

    results = await asyncio.gather(*[harness.submit(key="k1") for _ in range(5)])

    assert len({r["generation_id"] for r in results}) == 1
    assert len(routed) == 1
    assert harness.backend.start_generation.await_count == 1
    assert len(harness.rows()) == 1


@pytest.mark.asyncio
async def test_the_lock_table_does_not_grow(harness):
    await asyncio.gather(*[harness.submit(key=f"k{i % 2}") for i in range(6)])

    assert harness.orchestrator._idempotency_locks == {}


@pytest.mark.asyncio
async def test_a_row_recorded_but_never_queued_is_reported_failed_and_not_requeued(harness):
    harness.repo.create(Generation(
        id="orphan", preset_id="preset-1", form_data={}, user_id="u1", status="pending", idempotency_key="k1",
    ))

    result = await harness.submit(key="k1")

    assert result["generation_id"] == "orphan"
    assert result["status"]["status"] == "failed"
    assert result["queue_position"] is None
    harness.backend.start_generation.assert_not_awaited()
    assert harness.rows() == [{"id": "orphan", "status": "failed", "idempotency_key": "k1"}]
    again = await harness.submit(key="k1")
    assert again["status"]["status"] == "failed"
    assert again["generation_id"] == "orphan"


@pytest.mark.asyncio
async def test_a_failure_after_the_row_is_recorded_leaves_it_failed_not_pending(harness):
    harness.orchestrator._queue_dispatcher.enqueue = AsyncMock(side_effect=RuntimeError("queue down"))

    with pytest.raises(RuntimeError):
        await harness.submit(key="k1")

    rows = harness.rows()
    assert [(r["status"], r["idempotency_key"]) for r in rows] == [("failed", "k1")]
    harness.orchestrator._queue_dispatcher.enqueue = AsyncMock()
    retry = await harness.submit(key="k1")
    assert retry["generation_id"] == rows[0]["id"]
    assert retry["status"]["status"] == "failed"
    harness.orchestrator._queue_dispatcher.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_conflict_at_insert_resolves_to_the_winners_row(harness):
    winner = harness.repo.create(Generation(
        id="winner", preset_id="preset-1", form_data={}, user_id="u1", status="pending", idempotency_key="k1",
    ))
    harness.tracker.create(id="winner", preset_id="preset-1", user_id="u1")
    from src.features.generation import orchestrator as module

    real = module.generation_repo.get_by_idempotency_key
    calls = []

    def blind_first(user_id, key):
        calls.append(key)
        return None if len(calls) == 1 else real(user_id, key)

    with patch.object(module.generation_repo, "get_by_idempotency_key", side_effect=blind_first):
        result = await harness.submit(key="k1")

    assert result["generation_id"] == winner.id
    harness.backend.start_generation.assert_not_awaited()
    assert len(harness.rows()) == 1


@pytest.mark.asyncio
async def test_another_user_with_the_same_key_gets_their_own_generation(harness):
    first = await harness.submit(user_id="u1", key="k1")
    second = await harness.submit(user_id="u2", key="k1")

    assert first["generation_id"] != second["generation_id"]
    assert len(harness.rows("u1")) == 1 and len(harness.rows("u2")) == 1


@pytest.mark.asyncio
async def test_without_a_key_every_submit_starts_a_generation(harness):
    first = await harness.submit()
    second = await harness.submit()

    assert first["generation_id"] != second["generation_id"]
    assert [r["idempotency_key"] for r in harness.rows()] == [None, None]
    assert harness.orchestrator._idempotency_locks == {}


def test_the_repository_refuses_a_second_row_with_the_same_key(harness):
    harness.repo.create(Generation(id="g1", preset_id="p", form_data={}, user_id="u1", idempotency_key="k1"))

    with pytest.raises(DuplicateIdempotencyKey):
        harness.repo.create(Generation(id="g2", preset_id="p", form_data={}, user_id="u1", idempotency_key="k1"))

    assert harness.repo.get_by_idempotency_key("u1", "k1").id == "g1"
    assert harness.repo.get_by_idempotency_key("u2", "k1") is None


@pytest.mark.parametrize("key", ["", "x" * 201])
def test_an_empty_or_oversized_key_is_rejected(key):
    with pytest.raises(ValidationError):
        GenerationRequest(preset_id="p", idempotency_key=key)


@pytest.mark.asyncio
async def test_a_retry_of_a_submit_still_waiting_in_the_queue_is_not_failed(harness):
    await harness.submit(user_id="u1", key="busy")
    queued = await harness.submit(user_id="u1", key="waiting")
    assert queued["queue_position"] == 0

    retry = await harness.submit(user_id="u1", key="waiting")

    assert retry["generation_id"] == queued["generation_id"]
    assert retry["status"]["status"] == "pending"
    assert retry["queue_position"] == 0
    assert {r["status"] for r in harness.rows()} == {"pending", "running"}


@pytest.mark.asyncio
async def test_a_retry_of_a_finished_generation_leaves_it_as_it_ended(harness):
    harness.repo.create(Generation(
        id="done", preset_id="preset-1", form_data={}, user_id="u1", status="pending", idempotency_key="k1",
    ))
    harness.repo.update_status("done", "completed")

    result = await harness.submit(key="k1")

    assert result["generation_id"] == "done"
    assert result["status"]["status"] == "completed"
    assert harness.rows() == [{"id": "done", "status": "completed", "idempotency_key": "k1"}]


@pytest.mark.asyncio
async def test_a_retry_of_a_stored_generation_reports_utc_timestamps(harness):
    stored = Generation(
        id="done", preset_id="preset-1", form_data={}, user_id="u1", status="completed", idempotency_key="k1",
        created_at=datetime(2026, 10, 2, 8, 30), completed_at=datetime(2026, 10, 2, 8, 31),
    )

    with patch("src.features.generation.orchestrator.generation_repo.get_by_id", return_value=stored):
        status = harness.orchestrator._existing_submission(stored, None)["status"]

    assert datetime.fromisoformat(status["created_at"]) == datetime(2026, 10, 2, 8, 30, tzinfo=timezone.utc)
    assert datetime.fromisoformat(status["completed_at"]).utcoffset() == timedelta(0)


def test_the_fingerprint_hashes_media_bytes_instead_of_their_repr():
    def fingerprint(media):
        return _submission_fingerprint(Mock(
            preset_id="p", mode="m", form_name="f", form_data={"image": media},
            prompt="x", negative_prompt="", prompts=None,
        ))

    expected = hashlib.sha256(json.dumps({
        "preset_id": "p", "mode": "m", "form_name": "f",
        "form_data": {"image": "sha256:" + hashlib.sha256(b"abc").hexdigest()},
        "prompt": "x", "negative_prompt": "", "prompts": None,
    }, sort_keys=True).encode()).hexdigest()

    assert fingerprint(b"abc") == expected
    assert fingerprint(b"abd") != expected


@pytest.mark.asyncio
async def test_the_same_key_with_a_different_request_is_a_conflict(harness):
    first = await harness.submit(key="k1", prompt="a cat", form_data={"steps": 20})

    for changed in ({"prompt": "a dog"}, {"form_data": {"steps": 30}}):
        with pytest.raises(IdempotencyKeyConflict):
            await harness.submit(key="k1", **{"prompt": "a cat", "form_data": {"steps": 20}, **changed})

    assert [r["id"] for r in harness.rows()] == [first["generation_id"]]
    assert harness.backend.start_generation.await_count == 1


@pytest.mark.asyncio
async def test_the_same_key_with_the_same_request_still_returns_the_first(harness):
    first = await harness.submit(key="k1", prompt="a cat", form_data={"b": 1, "a": 2})
    again = await harness.submit(key="k1", prompt="a cat", form_data={"a": 2, "b": 1})

    assert again["generation_id"] == first["generation_id"]


@pytest.mark.asyncio
async def test_the_fingerprint_is_stored_with_the_key(harness):
    first = await harness.submit(key="k1")

    stored = harness.repo.get_by_id(first["generation_id"])
    assert stored.idempotency_fingerprint and len(stored.idempotency_fingerprint) == 64
    assert harness.repo.get_by_id(
        (await harness.submit(key="k2", prompt="other"))["generation_id"]
    ).idempotency_fingerprint != stored.idempotency_fingerprint


@pytest.mark.asyncio
async def test_a_conflict_found_at_insert_is_still_a_conflict(harness):
    harness.repo.create(Generation(
        id="winner", preset_id="preset-1", form_data={}, user_id="u1", status="pending",
        idempotency_key="k1", idempotency_fingerprint="someone-elses-request",
    ))
    harness.tracker.create(id="winner", preset_id="preset-1", user_id="u1")
    from src.features.generation import orchestrator as module

    real = module.generation_repo.get_by_idempotency_key
    calls = []

    def blind_first(user_id, key):
        calls.append(key)
        return None if len(calls) == 1 else real(user_id, key)

    with patch.object(module.generation_repo, "get_by_idempotency_key", side_effect=blind_first):
        with pytest.raises(IdempotencyKeyConflict):
            await harness.submit(key="k1")


@pytest.mark.asyncio
async def test_the_route_answers_409_for_a_key_conflict(harness):
    from src.features.generation.routes import GenerationController

    controller = GenerationController(harness.orchestrator, Mock(), Mock(), Mock())
    await harness.submit(key="k1", prompt="a cat")

    with pytest.raises(HTTPException) as refused:
        await controller.start_generation(harness.request("k1", prompt="a dog"), Mock(id="u1"))

    assert refused.value.status_code == 409
    assert refused.value.detail["error"] == "idempotency_key_conflict"


@pytest.mark.asyncio
async def test_a_cancelled_submit_is_settled_as_failed_and_a_retry_returns_that_row(harness):
    entered = asyncio.Event()

    async def hang(item):
        entered.set()
        await asyncio.sleep(3600)

    harness.orchestrator._queue_dispatcher.enqueue = hang
    task = asyncio.create_task(harness.submit(key="k1"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    rows = harness.rows()
    assert [(r["status"], r["idempotency_key"]) for r in rows] == [("failed", "k1")]
    harness.orchestrator._queue_dispatcher.enqueue = AsyncMock()
    retry = await harness.submit(key="k1")
    assert retry["generation_id"] == rows[0]["id"]
    assert retry["status"]["status"] == "failed"
    harness.orchestrator._queue_dispatcher.enqueue.assert_not_awaited()
