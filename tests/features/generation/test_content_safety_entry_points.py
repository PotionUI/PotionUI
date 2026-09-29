import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from src.features.generation.dto import GenerationRequest, PromptPair
from src.features.generation.routes import GenerationController
from src.features.generation.status_tracker import GenerationState
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.run_generation_tool import RunGenerationTool
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.pipelines.outputs import ImageGenerationOutput
from tests.features.content_safety.fakes import build
from tests.features.generation.test_orchestrator_content_safety import (
    backend,
    bind_form_passthrough,
    make_orchestrator,
    repo,
)


def request():
    return GenerationRequest(
        preset_id="p", mode="txt2img", form_data={"seed": 5},
        prompts=[PromptPair(positive="a quiet lake", negative="")],
    )


async def via_chat_start_tool(orchestrator):
    context = ToolContext(user_id="u1", generation_orchestrator=orchestrator)
    with patch("src.features.llm.tools.builtin.start_generation_tool.preset_form_media_errors", return_value=[]), \
            patch("src.features.llm.tools.builtin.start_generation_tool.preset_form_model_errors", return_value=[]):
        result = await StartGenerationTool().execute_confirmed(context, preset_id="p", prompt="a quiet lake")
    assert result.success, result.error


async def via_chat_run_tool(orchestrator):
    context = ToolContext(
        user_id="u1",
        generation_orchestrator=orchestrator,
        session_metadata={"form_state": {"preset": "p", "mode": "txt2img", "form_data": {"seed": 5}}},
    )
    with patch.object(RunGenerationTool, "_validate_media_overrides", return_value=[]):
        result = await RunGenerationTool().execute_confirmed(context)
    assert result.success, result.error


async def via_plugin_api(orchestrator):
    container = SimpleNamespace(generation_orchestrator=orchestrator)
    with patch("src.plugin_api.presets.get_container", return_value=container):
        from src.plugin_api.presets import get_container

        await get_container().generation_orchestrator.start_generation(request(), "u1")


async def via_route(orchestrator):
    controller = GenerationController(orchestrator, MagicMock(), MagicMock(), MagicMock())
    await controller.start_generation(request(), SimpleNamespace(id="u1"))


@pytest.mark.parametrize("entry", [via_chat_start_tool, via_chat_run_tool, via_plugin_api, via_route])
@pytest.mark.asyncio
async def test_every_generation_entry_point_hits_the_content_gate(entry, repo, backend):
    manager, tagger, _ = build("blocked", [0.99])
    orchestrator = make_orchestrator(backend, manager)

    with patch("src.features.generation.orchestrator.generate_ulid", return_value="gE"):
        await entry(orchestrator)

    emit = backend.start_generation.await_args.args[1]
    task = orchestrator._bridge_tasks["gE"]
    emit(ImageGenerationOutput(image=Image.new("RGB", (2, 2)), temporary=False))
    emit(None)
    await task

    assert tagger.calls == [1]
    orchestrator.output_processor.process_output.assert_not_awaited()
    record = orchestrator.status_tracker.get("gE")
    assert record.state == GenerationState.FAILED
    assert record.error_code == "content_blocked"
