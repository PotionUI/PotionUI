"""`GET /api/generations/history/version` - the history change-detection token.

Route-level tests prove the path is matched by its own handler rather than
swallowed by the `/history/{generation_id}` catch-all registered after it.
Controller-level tests cover the response shape and the saturated-executor 503.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.features.generation.history_executor import HistoryExecutorSaturated
from src.features.generation.history_facade import GenerationHistoryFacade
from src.features.generation.routes import GenerationController, build_router
from src.features.generation.run_report_recorder import RunReportRecorder
from src.platform.http.base_controller import APIResponse
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import User, AccountType


def _user(user_id="user-1"):
    return User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        password_hash="$2b$12$x",
        account_type=AccountType.USER,
        created_at=datetime.utcnow(),
        last_login=None,
    )


class TestHistoryVersionRouting:
    """The route is registered before `/history/{generation_id}`."""

    @pytest.fixture
    def controller(self):
        controller = GenerationController(
            Mock(), Mock(), Mock(), Mock(spec=RunReportRecorder)
        )
        controller.get_history_version = AsyncMock(
            return_value=APIResponse(success=True, data={"version": "abc123"})
        )
        controller.get_generation_by_id = AsyncMock(
            return_value=APIResponse(success=True, data={"id": "version"})
        )
        return controller

    @pytest.fixture
    def client(self, controller):
        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(_generation_controller=controller)))
        app.dependency_overrides[get_current_active_user] = lambda: _user()
        return TestClient(app)

    def test_version_path_reaches_its_own_handler(self, controller, client):
        response = client.get("/api/generations/history/version")

        assert response.status_code == 200
        assert response.json()["data"] == {"version": "abc123"}
        controller.get_history_version.assert_awaited_once()
        controller.get_generation_by_id.assert_not_awaited()

    def test_generation_ids_still_reach_the_detail_handler(self, controller, client):
        response = client.get("/api/generations/history/some-generation-id")

        assert response.status_code == 200
        controller.get_generation_by_id.assert_awaited_once()


class TestHistoryVersionController:
    """Response shape and the shared history-busy path."""

    @pytest.fixture
    def controller(self):
        return GenerationController(
            Mock(),
            Mock(spec=GenerationHistoryFacade),
            Mock(),
            Mock(spec=RunReportRecorder),
        )

    def test_returns_the_token_for_the_current_user(self, controller):
        controller.history_facade.get_history_version_async.return_value = "tok-1"

        result = asyncio.run(controller.get_history_version(_user("user-42")))

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data == {"version": "tok-1"}
        controller.history_facade.get_history_version_async.assert_awaited_once_with("user-42")

    def test_saturated_executor_maps_to_503(self, controller):
        controller.history_facade.get_history_version_async.side_effect = (
            HistoryExecutorSaturated()
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(controller.get_history_version(_user()))

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["error"] == "history_busy"
