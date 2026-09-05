"""`GenerationController.preview_memory`'s error mapping - specifically that a
routing failure (no backend eligible/available for the preset's engine)
reports as its own honest outcome, `no_eligible_backend`, rather than folding
into the generic `memory_preview_failed` bucket a genuine estimator bug would
get. See routes.py's `preview_memory` for the rationale.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.features.backends.backend_registry import NoBackendForEngineError
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.routes import GenerationController
from src.features.generation.routing.contracts import NoEligibleBackendError, RoutingDecision
from src.platform.filesystem import FileStore
from src.features.generation import GenerationHistoryFacade
from src.features.generation.run_report_recorder import RunReportRecorder


@pytest.fixture
def controller():
    orchestrator = Mock(spec=GenerationOrchestrator)
    orchestrator.status_tracker = Mock()
    orchestrator.preview_memory = AsyncMock()
    return GenerationController(
        orchestrator,
        Mock(spec=GenerationHistoryFacade),
        Mock(spec=FileStore),
        Mock(spec=RunReportRecorder),
    )


@pytest.fixture
def current_user():
    user = Mock()
    user.id = "user_1"
    return user


def _request():
    request = Mock()
    request.preset_id = "preset_1"
    return request


class TestPreviewMemoryRoutingOutcome:
    @pytest.mark.asyncio
    async def test_no_backend_for_engine_reports_as_routing_outcome(self, controller, current_user):
        controller.generation_orchestrator.preview_memory.side_effect = NoBackendForEngineError(
            "No enabled backend provides engine 'comfyui'"
        )

        with pytest.raises(HTTPException) as exc_info:
            await controller.preview_memory(_request(), current_user)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["error"] == "no_eligible_backend"
        assert "comfyui" in exc_info.value.detail["message"]

    @pytest.mark.asyncio
    async def test_no_eligible_backend_reports_as_routing_outcome(self, controller, current_user):
        decision = RoutingDecision(chosen=None, candidates=[], rule_trace=[])
        controller.generation_orchestrator.preview_memory.side_effect = NoEligibleBackendError("native", decision)

        with pytest.raises(HTTPException) as exc_info:
            await controller.preview_memory(_request(), current_user)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["error"] == "no_eligible_backend"

    @pytest.mark.asyncio
    async def test_a_genuine_estimator_bug_stays_a_distinct_outcome(self, controller, current_user):
        """A routing error and any other unexpected failure must map to
        DIFFERENT error codes - collapsing them would make a caller unable to
        tell "no backend is available right now" apart from "the estimator
        itself broke"."""
        controller.generation_orchestrator.preview_memory.side_effect = RuntimeError("boom")

        with pytest.raises(HTTPException) as exc_info:
            await controller.preview_memory(_request(), current_user)

        assert exc_info.value.detail["error"] == "memory_preview_failed"
        assert exc_info.value.status_code != 503

    @pytest.mark.asyncio
    async def test_successful_preview_is_unaffected(self, controller, current_user):
        controller.generation_orchestrator.preview_memory.return_value = {"estimate": {}, "coverage": {}}

        response = await controller.preview_memory(_request(), current_user)

        assert response.success is True
        assert response.data == {"estimate": {}, "coverage": {}}
