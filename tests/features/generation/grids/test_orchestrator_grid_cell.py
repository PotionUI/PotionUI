from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.generation.dto import GenerationRequest
from src.features.generation.grids.dto import GridCellRef
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.repository import GenerationRepository
from src.features.generation.status_tracker import GenerationStatusTracker


@pytest.fixture(autouse=True)
def bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])

    with patch("src.features.generation.orchestrator.bind_form", side_effect=passthrough):
        yield


class Harness:
    def __init__(self, db):
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash, account_type) VALUES ('u1', 'u1', 'u1@example.test', 'x', 'USER')"
            )
        self.repo = GenerationRepository()
        self.backend = Mock(backend_id="b1", engine="native", name="Local")
        self.backend.start_generation = AsyncMock()
        registry = Mock()
        registry.select_backend_for_generation = Mock(return_value=self.backend)
        registry.get_backend = Mock(return_value=self.backend)
        preset = Mock(engine="native", version="1.0.0")
        builder = Mock()
        builder.build_pipeline = Mock(return_value=Mock(pipes=[{"name": "generator", "config": {}}], preset_template=preset))
        self.tracker = GenerationStatusTracker()
        self.orchestrator = GenerationOrchestrator(
            pipeline_builder=builder,
            backend_registry=registry,
            connection_hub=Mock(),
            settings=Mock(get_setting=Mock(return_value="/outputs")),
            output_processor=Mock(process_output=AsyncMock(return_value={"handler": "H", "processed": True})),
            preset_template_loader=Mock(load_preset_by_id=Mock(return_value=preset)),
            status_tracker=self.tracker,
        )

    def request(self, key=None):
        return GenerationRequest(preset_id="preset-1", prompt="a cat", form_data={"seed": 1}, idempotency_key=key)


@pytest.fixture
def harness(mock_db):
    return Harness(mock_db)


@pytest.mark.asyncio
async def test_a_grid_cell_persists_its_grid_position_and_axis_values(harness):
    ref = GridCellRef("grid-1", 2, 1, {"sampler": "euler", "steps": "20"})

    result = await harness.orchestrator.start_generation(harness.request(), "u1", grid_cell=ref)

    row = harness.repo.get_by_id(result["generation_id"])
    assert (row.grid_id, row.grid_x, row.grid_y) == ("grid-1", 2, 1)
    assert row.axis_values == {"sampler": "euler", "steps": "20"}
    assert harness.tracker.get(result["generation_id"]).grid_id == "grid-1"
    assert result["status"]["grid_id"] == "grid-1"


@pytest.mark.asyncio
async def test_an_ordinary_submit_has_no_grid_fields(harness):
    result = await harness.orchestrator.start_generation(harness.request(), "u1")

    row = harness.repo.get_by_id(result["generation_id"])
    assert (row.grid_id, row.grid_x, row.grid_y, row.axis_values) == (None, None, None, None)
    assert "grid_id" not in result["status"]


@pytest.mark.asyncio
async def test_a_grid_cell_with_an_idempotency_key_keeps_its_grid_fields(harness):
    ref = GridCellRef("grid-1", 0, 0, {"sampler": "a"})

    result = await harness.orchestrator.start_generation(harness.request("k1:grid:0:0"), "u1", grid_cell=ref)

    row = harness.repo.get_by_id(result["generation_id"])
    assert (row.grid_id, row.grid_x, row.grid_y) == ("grid-1", 0, 0)
    assert row.idempotency_key == "k1:grid:0:0"
