import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.features.cloud.cost_repository import GenerationCostRepository
from src.features.generation.records import Generation
from src.features.generation.output_processor import OutputProcessor
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.generation.repository import generation_repo
from src.features.generation.routes import GenerationController
from src.features.generation.status_tracker import GenerationStatusTracker
from src.pipelines.outputs import CostGenerationOutput, ProgressGenerationOutput

AMOUNT = "0.0731"
WORDS = ("cost", "price", "pricing", "amount_usd", "usd", AMOUNT)


def assert_no_cost(payload):
    text = json.dumps(payload, default=str).lower()
    for word in WORDS:
        assert word not in text, word


@pytest.fixture
def generation(mock_db):
    with mock_db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES ('u1', 'u1', 'u1@example.test', 'x', 'USER')"
        )
    created = generation_repo.create(Generation(id="g1", preset_id="p", form_data={"prompt": "a cat"}, user_id="u1", backend_id="b1"))
    GenerationCostRepository().record(
        "g1", backend_id="b1", model_id="m1", user_id="u1", amount_usd=Decimal(AMOUNT), source="provider", detail={"task": "txt2img"},
    )
    return created


def orchestrator():
    tracker = GenerationStatusTracker()
    tracker.create("g1", preset_id="p", backend_id="b1", user_id="u1")
    return GenerationOrchestrator(
        pipeline_builder=Mock(spec=PipelineBuilder), backend_registry=Mock(), connection_hub=Mock(),
        settings=Mock(), output_processor=OutputProcessor(Mock()), preset_template_loader=Mock(),
        status_tracker=tracker,
    )


async def test_a_cost_output_is_recorded_but_never_handed_to_the_client_callback(generation):
    engine = orchestrator()
    callback = AsyncMock()

    await engine._handle_generation_output("g1", CostGenerationOutput(model="fake~x", amount_usd=Decimal("0.5")), "cloud", callback)

    callback.assert_not_awaited()
    amounts = [row["amount_usd"] for row in GenerationCostRepository().for_generation("g1")]
    assert amounts == [AMOUNT, "0.5"]


async def test_the_same_path_still_delivers_ordinary_outputs(generation):
    engine = orchestrator()
    callback = AsyncMock()
    progress = ProgressGenerationOutput(state="Working", title="Running")

    await engine._handle_generation_output("g1", progress, "cloud", callback)

    callback.assert_awaited_once()


async def test_a_failing_cost_recording_does_not_break_the_generation(generation):
    engine = orchestrator()
    engine.output_processor = Mock(process_output=AsyncMock(side_effect=RuntimeError("db gone")))
    callback = AsyncMock()

    await engine._handle_generation_output("g1", CostGenerationOutput(model="x"), "cloud", callback)

    callback.assert_not_awaited()


async def test_the_controller_never_broadcasts_or_reports_a_cost_output(generation):
    controller = GenerationController(Mock(), Mock(), Mock(), Mock())
    controller.connection_hub.generation_connections = {"g1": [object()]}
    controller.connection_hub.broadcast_to_generation = AsyncMock()
    status = SimpleNamespace(preset_id="p")

    await controller.output_broadcaster.broadcast_output("g1", CostGenerationOutput(model="x", amount_usd=Decimal(AMOUNT)), status)

    controller.connection_hub.broadcast_to_generation.assert_not_awaited()
    controller.run_report_recorder.record_output.assert_not_called()


def test_the_registry_knows_which_outputs_are_server_only():
    from src.features.generation.output_types import output_type_registry

    assert output_type_registry.is_server_only(CostGenerationOutput(model="x")) is True
    assert output_type_registry.is_server_only(ProgressGenerationOutput(state="s", title="t")) is False
    assert output_type_registry.is_server_only(object()) is False


def test_every_websocket_message_of_a_run_is_free_of_costs(generation):
    serializer = GenerationOutputSerializer("g1", "p")
    outputs = [ProgressGenerationOutput(state="Generating", title="Running")]

    assert_no_cost([serializer.serialize_output(output) for output in outputs])


def test_a_users_generation_payloads_carry_no_cost(generation):
    owned = generation_repo.get_by_id("g1", user_id="u1", include_files=True)

    assert_no_cost(owned.to_dict(include_files=True, include_tags=True))
    assert_no_cost([g.to_dict() for g in generation_repo.get_all(user_id="u1")])


def test_the_users_status_payload_carries_no_cost(generation):
    engine = orchestrator()

    assert_no_cost(engine.status_tracker.get("g1").__dict__)


def test_no_source_file_outside_the_admin_and_recording_paths_touches_the_cost_table():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "src"
    allowed = {
        "platform/database/migrations/047_generation_costs.py",
        "features/cloud/cost_repository.py",
        "features/generation/handlers/cost_handler.py",
        "features/generation/routes.py",
        "features/stats/routes.py",
        "bootstrap/container.py",
    }
    needles = ("generation_costs", "GenerationCostRepository", "generation_cost_repository")
    users = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if any(needle in path.read_text(encoding="utf-8", errors="ignore") for needle in needles)
    }

    assert users == allowed
