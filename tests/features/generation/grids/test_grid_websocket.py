import json
from unittest.mock import Mock, patch

import pytest

from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.output_serializer import GenerationOutputSerializer, _reduce_to_allowlist
from src.features.generation.status_tracker import GenerationStatusTracker
from src.pipelines.outputs import ProgressGenerationOutput
from src.platform.websocket.connection_hub import ConnectionHub


class Socket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        return None

    async def send_text(self, text):
        self.sent.append(json.loads(text))


async def setup(grid_id):
    hub = ConnectionHub()
    tracker = GenerationStatusTracker()
    with patch("src.features.generation.status_tracker.generation_repo"):
        tracker.create("gen-1", preset_id="p1", user_id="u1", grid_id=grid_id)
    broadcaster = GenerationOutputBroadcaster(hub, tracker, Mock())
    socket = Socket()
    await hub.connect(socket, "c1")
    await hub.subscribe_to_generation("c1", "gen-1")
    return broadcaster, tracker, socket


def test_the_serializer_adds_grid_id_to_the_generation_status_envelope():
    message = GenerationOutputSerializer("gen-1", "p1", grid_id="g1").serialize_output(
        ProgressGenerationOutput(state="Working", title="Step 3/20")
    )

    assert message["type"] == "generation_status"
    assert message["grid_id"] == "g1"
    assert message["generation_id"] == "gen-1"


def test_the_serializer_leaves_grid_id_out_for_ordinary_generations():
    message = GenerationOutputSerializer("gen-1", "p1").serialize_output(ProgressGenerationOutput(state="Working"))

    assert "grid_id" not in message


def test_grid_id_survives_the_preview_suppression_allowlist():
    reduced = _reduce_to_allowlist({"type": "gallery_update", "generation_id": "g", "grid_id": "g1", "secret": 1})

    assert reduced == {"type": "gallery_update", "generation_id": "g", "grid_id": "g1"}


@pytest.mark.asyncio
async def test_a_grid_cells_progress_message_carries_its_grid_id():
    broadcaster, tracker, socket = await setup("g1")

    await broadcaster.handle_output("gen-1", ProgressGenerationOutput(state="Working", title="Running"))

    status = [m for m in socket.sent if m["type"] == "generation_status"]
    assert status and status[0]["grid_id"] == "g1"


@pytest.mark.asyncio
async def test_a_plain_generations_progress_message_has_no_grid_id():
    broadcaster, tracker, socket = await setup(None)

    await broadcaster.handle_output("gen-1", ProgressGenerationOutput(state="Working", title="Running"))

    status = [m for m in socket.sent if m["type"] == "generation_status"]
    assert status and "grid_id" not in status[0]


@pytest.mark.asyncio
async def test_the_completion_message_status_carries_the_grid_id_for_reconnecting_clients():
    broadcaster, tracker, socket = await setup("g1")

    await broadcaster.handle_output("gen-1", None)

    complete = [m for m in socket.sent if m["type"] == "generation_complete"]
    assert complete and complete[0]["data"]["grid_id"] == "g1"


def test_the_status_record_omits_grid_id_unless_set():
    tracker = GenerationStatusTracker()
    with patch("src.features.generation.status_tracker.generation_repo"):
        grid_record = tracker.create("a", grid_id="g1")
        plain_record = tracker.create("b")

    assert grid_record.model_dump()["grid_id"] == "g1"
    assert "grid_id" not in plain_record.model_dump()
