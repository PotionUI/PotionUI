from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.features.generation import GenerationHistoryFacade
from src.features.generation.dto import GenerationRequest
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.generation.routes import GenerationController
from src.features.generation.run_report_recorder import RunReportRecorder
from src.features.presets import PresetProcessor
from src.platform.filesystem import FileStore
from src.platform.security.user import AccountType

LEAKY = "pipe 'secret_pipe' at /srv/presets/Private/model/preset.yml config_path=pipes[2].config.steps"


def _builder():
    loader = Mock()
    loader.load_preset_by_id.return_value = Mock(name="preset", id="p1", version="1.0.0")
    processor = Mock(spec=PresetProcessor)
    processor.process.side_effect = RuntimeError(LEAKY)
    return PipelineBuilder(preset_template_loader=loader, preset_processor=processor)


def _build():
    _builder().build_pipeline(preset_id="p1", form_data={})


@pytest.fixture
def controller():
    orchestrator = Mock(spec=GenerationOrchestrator)
    orchestrator.status_tracker = Mock()
    orchestrator.start_generation = AsyncMock(side_effect=lambda *a, **k: _build())
    orchestrator.preview_memory = AsyncMock(side_effect=lambda *a, **k: _build())
    return GenerationController(
        orchestrator,
        Mock(spec=GenerationHistoryFacade),
        Mock(spec=FileStore),
        Mock(spec=RunReportRecorder),
    )


def _user(account_type):
    user = Mock()
    user.id = "u1"
    user.account_type = account_type
    return user


def _message(response_or_exc):
    if isinstance(response_or_exc, HTTPException):
        return response_or_exc.detail["message"]
    return response_or_exc


async def _start(controller, user):
    request = GenerationRequest(preset_id="p1", prompt="x", form_data={})
    with pytest.raises(HTTPException) as exc:
        await controller.start_generation(request, user)
    return exc.value


async def _preview(controller, user):
    with pytest.raises(HTTPException) as exc:
        await controller.preview_memory(Mock(preset_id="p1"), user)
    return exc.value


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [_start, _preview])
async def test_non_admin_never_sees_preset_processing_detail(controller, action):
    error = await action(controller, _user(AccountType.USER))

    message = _message(error)
    for leaked in ("secret_pipe", "/srv/presets", "config_path", "Traceback"):
        assert leaked not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [_start, _preview])
async def test_admin_still_gets_preset_processing_detail(controller, action):
    error = await action(controller, _user(AccountType.ADMIN))

    assert "secret_pipe" in _message(error)


@pytest.mark.asyncio
async def test_request_validation_errors_keep_their_message(controller):
    controller.generation_orchestrator.start_generation.side_effect = ValueError("'steps' must be positive")

    error = await _start(controller, _user(AccountType.USER))

    assert error.status_code == 400
    assert error.detail["message"] == "'steps' must be positive"
