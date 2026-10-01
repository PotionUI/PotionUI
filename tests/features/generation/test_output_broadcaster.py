import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.routes import GenerationController
from src.features.generation.status_tracker import GenerationStatusTracker
from src.pipelines.outputs import (
    CostGenerationOutput,
    ErrorGenerationOutput,
    ProgressGenerationOutput,
)
from src.platform.websocket.connection_hub import ConnectionHub


class _Socket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        return None

    async def send_text(self, text):
        self.sent.append(json.loads(text))


async def _subscribe(hub, client_id, generation_id):
    socket = _Socket()
    await hub.connect(socket, client_id)
    await hub.subscribe_to_generation(client_id, generation_id)
    return socket


def _broadcaster(generation_id="gen-1"):
    hub = ConnectionHub()
    tracker = GenerationStatusTracker()
    recorder = Mock()
    with patch("src.features.generation.status_tracker.generation_repo"):
        tracker.create(generation_id, preset_id="preset-1", user_id="u1")
    return GenerationOutputBroadcaster(hub, tracker, recorder), hub, recorder


@pytest.fixture(autouse=True)
def _bind_form_passthrough():
    from src.features.forms.binding import BoundForm

    def _passthrough(preset_template, mode, form_name, raw_form_data, user_id, storage_dir=None, field_overrides=None):
        return BoundForm(values=dict(raw_form_data or {}), form_name=form_name or "custom", coercions=[], stripped=[])

    with patch("src.features.generation.orchestrator.bind_form", side_effect=_passthrough):
        yield


@pytest.fixture
def started_without_callback(mock_db):
    backend = Mock(backend_id="b1", engine="native", name="Local")
    backend.start_generation = AsyncMock()
    registry = Mock()
    registry.select_backend_for_generation = Mock(return_value=backend)
    registry.get_backend = Mock(return_value=backend)
    preset = Mock(engine="native", version="1.0.0")
    loader = Mock()
    loader.load_preset_by_id = Mock(return_value=preset)
    builder = Mock()
    builder.build_pipeline = Mock(return_value=Mock(pipes=[{"name": "generator", "config": {}}], preset_template=preset))
    request = Mock(
        preset_id="preset-1", form_data={}, prompt="a cat", negative_prompt="", prompts=None,
        prompt_state=None, mode="txt2img", tag_ids=None, source_prompt_id=None,
    )

    async def run(broadcaster, tracker, output_callback=None):
        orchestrator = GenerationOrchestrator(
            pipeline_builder=builder,
            backend_registry=registry,
            connection_hub=broadcaster.connection_hub,
            settings=Mock(get_setting=Mock(return_value="/outputs")),
            output_processor=Mock(process_output=AsyncMock(return_value={"handler": "H", "processed": True})),
            preset_template_loader=loader,
            status_tracker=tracker,
            output_broadcaster=broadcaster,
        )
        with patch("src.features.generation.orchestrator.generation_repo"), \
             patch("src.features.generation.status_tracker.generation_repo"), \
             patch("src.features.generation.orchestrator.generate_ulid", return_value="gen-1"):
            await orchestrator.start_generation(request, "u1", output_callback=output_callback)
        return backend.start_generation.call_args[0][1]

    return run


async def _wait_for(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_generation_started_without_a_callback_streams_progress_and_writes_a_run_report(started_without_callback):
    hub = ConnectionHub()
    tracker = GenerationStatusTracker()
    recorder = Mock()
    broadcaster = GenerationOutputBroadcaster(hub, tracker, recorder)
    socket = await _subscribe(hub, "c1", "gen-1")

    emit = await started_without_callback(broadcaster, tracker)
    emit(ProgressGenerationOutput(state="Working", title="Running"))
    await _wait_for(lambda: any(m.get("type") == "generation_status" for m in socket.sent))

    assert recorder.record_output.call_args[0][0] == "gen-1"
    assert recorder.record_output.call_args[0][1]["type"] == "generation_status"


@pytest.mark.asyncio
async def test_the_run_report_is_recorded_even_when_nobody_is_subscribed():
    broadcaster, hub, recorder = _broadcaster()
    hub.broadcast_to_generation = AsyncMock()

    await broadcaster.handle_output("gen-1", ProgressGenerationOutput(state="Working", title="Running"))

    recorder.record_output.assert_called_once()
    hub.broadcast_to_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_flushes_the_run_report_and_notifies_subscribers():
    broadcaster, hub, recorder = _broadcaster()
    socket = await _subscribe(hub, "c1", "gen-1")

    await broadcaster.handle_output("gen-1", None)

    recorder.flush.assert_called_once()
    assert recorder.flush.call_args[0][0] == "gen-1"
    assert socket.sent[-1]["type"] == "generation_complete"
    assert socket.sent[-1]["data"]["id"] == "gen-1"


@pytest.mark.asyncio
async def test_a_failing_run_report_flush_still_notifies_subscribers():
    broadcaster, hub, recorder = _broadcaster()
    recorder.flush.side_effect = RuntimeError("disk full")
    socket = await _subscribe(hub, "c1", "gen-1")

    await broadcaster.handle_output("gen-1", None)

    assert socket.sent[-1]["type"] == "generation_complete"


@pytest.mark.asyncio
async def test_errors_are_broadcast_to_subscribers():
    broadcaster, hub, _ = _broadcaster()
    socket = await _subscribe(hub, "c1", "gen-1")

    await broadcaster.handle_output("gen-1", ErrorGenerationOutput(error="boom"))

    assert socket.sent[-1]["type"] == "generation_error"


@pytest.mark.asyncio
async def test_an_unknown_generation_is_ignored():
    broadcaster, hub, recorder = _broadcaster()

    await broadcaster.handle_output("other", ProgressGenerationOutput(state="x", title="y"))
    await broadcaster.handle_output("other", None)

    recorder.record_output.assert_not_called()
    recorder.flush.assert_not_called()


@pytest.mark.asyncio
async def test_server_only_outputs_are_never_broadcast_or_reported():
    broadcaster, hub, recorder = _broadcaster()
    socket = await _subscribe(hub, "c1", "gen-1")

    await broadcaster.handle_output("gen-1", CostGenerationOutput(model="x", amount_usd=Decimal("1.5")))

    assert socket.sent == []
    recorder.record_output.assert_not_called()


def test_the_controller_uses_the_hub_of_the_broadcaster_it_is_given():
    broadcaster, hub, _ = _broadcaster()
    orchestrator = Mock(status_tracker=GenerationStatusTracker())

    controller = GenerationController(orchestrator, Mock(), Mock(), Mock(), None, broadcaster)

    assert controller.connection_hub is hub
    assert controller.websocket_handler.connection_hub is hub
    assert controller.output_broadcaster is broadcaster


@pytest.mark.asyncio
async def test_an_explicit_callback_wins_over_the_default_broadcaster(started_without_callback):
    tracker = GenerationStatusTracker()
    broadcaster = GenerationOutputBroadcaster(ConnectionHub(), tracker, Mock())
    broadcaster.handle_output = AsyncMock()
    explicit = AsyncMock()

    emit = await started_without_callback(broadcaster, tracker, explicit)
    emit(ProgressGenerationOutput(state="Working", title="Running"))
    await _wait_for(lambda: explicit.await_count == 1)

    broadcaster.handle_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_is_broadcast_with_the_current_status():
    broadcaster, hub, _ = _broadcaster()
    socket = await _subscribe(hub, "c1", "gen-1")

    await broadcaster.broadcast_cancelled("gen-1")

    assert socket.sent[-1]["type"] == "generation_cancelled"
    assert socket.sent[-1]["data"]["id"] == "gen-1"


@pytest.mark.asyncio
async def test_cancelled_is_not_broadcast_for_an_unknown_generation():
    broadcaster, hub, _ = _broadcaster()
    socket = await _subscribe(hub, "c1", "other")

    await broadcaster.broadcast_cancelled("other")

    assert socket.sent == []
