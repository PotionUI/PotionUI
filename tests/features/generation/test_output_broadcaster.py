from tests.fixtures.kits import inert_kit_expander
import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image

from src.features.generation.output_broadcaster import GenerationOutputBroadcaster
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.output_types import OutputTypeRegistry, OutputTypeSpec
from src.features.generation.routes import GenerationController
from src.features.generation.status_tracker import GenerationStatusTracker
from src.pipelines.outputs import (
    CompareImagesGenerationOutput,
    ComfyUIWorkflowGenerationOutput,
    CostGenerationOutput,
    DiffTextGenerationOutput,
    ErrorGenerationOutput,
    GenerationOutput,
    ImageGenerationOutput,
    ModelFileGenerationOutput,
    ModelGenerationOutput,
    ModelsGenerationOutput,
    ProgressGenerationOutput,
    RenderedPromptGenerationOutput,
    SeedGenerationOutput,
    TextGenerationOutput,
    WarmStartGenerationOutput,
)
from src.platform.websocket.connection_hub import ConnectionHub


class _Socket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        return None

    async def send_text(self, text):
        self.sent.append(json.loads(text))


async def _subscribe(hub, client_id, generation_id, privileged=False):
    socket = _Socket()
    await hub.connect(socket, client_id)
    await hub.subscribe_to_generation(client_id, generation_id, privileged=privileged)
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
            output_broadcaster=broadcaster, kit_expander=inert_kit_expander(),
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


MODEL_PATH = "/srv/potion/models/diffusion_models/secret_dit_fp16.safetensors"


def _report_only_artifact(kind):
    image = Image.new("RGB", (4, 4))
    return {
        "models": lambda: ModelsGenerationOutput(models=[ModelGenerationOutput(name=MODEL_PATH, type="checkpoint")]),
        "diff_text": lambda: DiffTextGenerationOutput(index=0, name="Positive Prompt", diff="a cat in the rain"),
        "seed": lambda: SeedGenerationOutput(index=0, seed=424242),
        "rendered_prompt": lambda: RenderedPromptGenerationOutput(index=0, positive="a cat in the rain"),
        "warm_start": lambda: WarmStartGenerationOutput(index=0, resume_step=3, total_steps=20, steps_skipped=3, similarity=0.9),
        "compare_images": lambda: CompareImagesGenerationOutput(index=0, compare=("Before", image), to=("After", image)),
        "workflow": lambda: ComfyUIWorkflowGenerationOutput(workflow={"1": {"inputs": {"ckpt_name": MODEL_PATH}}}, workflow_file=MODEL_PATH),
        "image": lambda: ImageGenerationOutput(image=image, isArtifact=True, label="Control Image"),
    }[kind]()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["models", "diff_text", "seed", "rendered_prompt", "warm_start", "compare_images", "workflow", "image"],
)
async def test_a_diagnostic_artifact_goes_to_the_run_report_and_never_to_a_subscriber(kind):
    broadcaster, hub, recorder = _broadcaster()
    user = await _subscribe(hub, "user", "gen-1")
    admin = await _subscribe(hub, "admin", "gen-1", privileged=True)

    await broadcaster.handle_output("gen-1", _report_only_artifact(kind))

    assert user.sent == []
    assert admin.sent == []
    recorded = recorder.record_output.call_args[0][1]
    assert recorded["type"] == "pipe_artifact"
    assert recorded["artifact_type"] == kind


@pytest.mark.asyncio
async def test_the_run_report_keeps_the_model_paths_an_admin_reads():
    broadcaster, hub, recorder = _broadcaster()
    await _subscribe(hub, "user", "gen-1")

    await broadcaster.handle_output("gen-1", _report_only_artifact("models"))

    recorded = recorder.record_output.call_args[0][1]
    assert recorded["artifact_data"]["models"][0]["name"] == MODEL_PATH


@pytest.mark.asyncio
async def test_text_and_model_file_artifacts_still_stream_without_server_paths():
    broadcaster, hub, _ = _broadcaster()
    user = await _subscribe(hub, "user", "gen-1")
    admin = await _subscribe(hub, "admin", "gen-1", privileged=True)

    await broadcaster.handle_output("gen-1", TextGenerationOutput(title="Caption", text="a cat"))
    await broadcaster.handle_output(
        "gen-1", ModelFileGenerationOutput(path=MODEL_PATH, size_bytes=1, format="fp8"),
    )

    for socket in (user, admin):
        assert [m["artifact_type"] for m in socket.sent] == ["text", "model_file"]
        assert socket.sent[1]["artifact_data"]["file_name"] == "secret_dit_fp16.safetensors"
        assert "/srv/" not in json.dumps(socket.sent)


@dataclass
class _PluginArtifactOutput(GenerationOutput):
    note: str = ""


@pytest.mark.asyncio
async def test_a_plugin_artifact_type_is_report_only_unless_it_opts_in():
    registry = OutputTypeRegistry()
    registry.register(OutputTypeSpec(
        output_cls=_PluginArtifactOutput,
        key="plugin_artifact",
        message_type="pipe_artifact",
        serializer=lambda output, ctx: {"artifact_type": "plugin_artifact", "artifact_data": {"note": output.note}},
    ))
    broadcaster, hub, recorder = _broadcaster()
    user = await _subscribe(hub, "user", "gen-1")

    with patch("src.features.generation.output_broadcaster.output_type_registry", registry), \
            patch("src.features.generation.output_serializer.output_type_registry", registry):
        await broadcaster.handle_output("gen-1", _PluginArtifactOutput(note=MODEL_PATH))

    assert user.sent == []
    assert recorder.record_output.call_args[0][1]["artifact_type"] == "plugin_artifact"


@pytest.mark.asyncio
async def test_a_progress_line_names_only_the_file_for_a_user_and_the_path_for_an_admin():
    broadcaster, hub, recorder = _broadcaster()
    user = await _subscribe(hub, "user", "gen-1")
    admin = await _subscribe(hub, "admin", "gen-1", privileged=True)
    state = f"Error loading image: cannot identify image file '{MODEL_PATH}'"

    await broadcaster.handle_output("gen-1", ProgressGenerationOutput(state=state, title=f"Loading {MODEL_PATH}"))

    assert user.sent[-1]["current_step"] == "Error loading image: cannot identify image file 'secret_dit_fp16.safetensors'"
    assert user.sent[-1]["message"] == "Loading secret_dit_fp16.safetensors"
    assert admin.sent[-1]["current_step"] == state
    assert recorder.record_output.call_args[0][1]["current_step"] == state


@pytest.mark.asyncio
async def test_the_comfyui_connecting_line_hides_the_backend_address_from_a_user():
    broadcaster, hub, recorder = _broadcaster()
    user = await _subscribe(hub, "user", "gen-1")
    admin = await _subscribe(hub, "admin", "gen-1", privileged=True)
    state = "Connecting to ComfyUI at 10.0.0.5:8188"

    await broadcaster.handle_output("gen-1", ProgressGenerationOutput(state=state))

    assert user.sent[-1]["current_step"] == "Connecting to ComfyUI\u2026"
    assert admin.sent[-1]["current_step"] == state
    assert recorder.record_output.call_args[0][1]["current_step"] == state


@pytest.mark.asyncio
async def test_a_model_file_index_warning_names_only_the_file_for_a_user():
    broadcaster, hub, _ = _broadcaster()
    user = await _subscribe(hub, "user", "gen-1")
    admin = await _subscribe(hub, "admin", "gen-1", privileged=True)
    warning = f"'{MODEL_PATH}' is not under any known model root"

    await broadcaster.handle_output(
        "gen-1", ModelFileGenerationOutput(path=MODEL_PATH, index_warning=warning),
    )

    assert user.sent[-1]["artifact_data"]["index_warning"] == "'secret_dit_fp16.safetensors' is not under any known model root"
    assert admin.sent[-1]["artifact_data"]["index_warning"] == warning


@pytest.mark.asyncio
async def test_a_progress_line_without_a_path_is_sent_once_to_everyone():
    broadcaster, hub, _ = _broadcaster()
    hub.broadcast_to_generation = AsyncMock()
    await _subscribe(hub, "user", "gen-1")

    await broadcaster.handle_output("gen-1", ProgressGenerationOutput(state="Sampling 1/20", title="Generating"))

    message, privileged = hub.broadcast_to_generation.await_args[0][1:]
    assert message["current_step"] == "Sampling 1/20"
    assert privileged is None
