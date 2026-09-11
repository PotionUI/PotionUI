"""`GET /api/generations/history/matching-ids` - ids for "select all N matching".

Route-level tests prove the path is matched by its own handler rather than
swallowed by the `/history/{generation_id}` catch-all registered after it.
Controller-level tests cover the response shape, filter parsing (comma-separated
tag_ids) and the invalid-date-filter 400.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.features.generation.exceptions import InvalidDateFilterException
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


class TestMatchingIdsRouting:
    """The route is registered before `/history/{generation_id}`."""

    @pytest.fixture
    def controller(self):
        controller = GenerationController(
            Mock(), Mock(), Mock(), Mock(spec=RunReportRecorder)
        )
        controller.get_matching_generation_ids = AsyncMock(
            return_value=APIResponse(success=True, data={"ids": ["a"], "total": 1, "truncated": False})
        )
        controller.get_generation_by_id = AsyncMock(
            return_value=APIResponse(success=True, data={"id": "matching-ids"})
        )
        return controller

    @pytest.fixture
    def client(self, controller):
        app = FastAPI()
        app.include_router(build_router(SimpleNamespace(_generation_controller=controller)))
        app.dependency_overrides[get_current_active_user] = lambda: _user()
        return TestClient(app)

    def test_matching_ids_path_reaches_its_own_handler(self, controller, client):
        response = client.get("/api/generations/history/matching-ids")

        assert response.status_code == 200
        assert response.json()["data"] == {"ids": ["a"], "total": 1, "truncated": False}
        controller.get_matching_generation_ids.assert_awaited_once()
        controller.get_generation_by_id.assert_not_awaited()

    def test_generation_ids_still_reach_the_detail_handler(self, controller, client):
        response = client.get("/api/generations/history/some-generation-id")

        assert response.status_code == 200
        controller.get_generation_by_id.assert_awaited_once()


class TestMatchingIdsController:
    """Response shape, filter parsing and the shared invalid-date-filter 400."""

    @pytest.fixture
    def controller(self):
        return GenerationController(
            Mock(),
            Mock(spec=GenerationHistoryFacade),
            Mock(),
            Mock(spec=RunReportRecorder),
        )

    def test_returns_the_ids_for_the_current_user(self, controller):
        controller.history_query.get_matching_ids.return_value = {
            "ids": ["gen-1", "gen-2"],
            "total": 2,
            "truncated": False,
        }

        result = asyncio.run(
            controller.get_matching_generation_ids(_user("user-42"), favorites_only=True)
        )

        assert isinstance(result, APIResponse)
        assert result.success is True
        assert result.data == {"ids": ["gen-1", "gen-2"], "total": 2, "truncated": False}
        _, kwargs = controller.history_query.get_matching_ids.call_args
        assert kwargs["user_id"] == "user-42"
        assert kwargs["favorites_only"] is True

    def test_comma_separated_tag_ids_are_split(self, controller):
        controller.history_query.get_matching_ids.return_value = {
            "ids": [], "total": 0, "truncated": False,
        }

        asyncio.run(
            controller.get_matching_generation_ids(_user(), tag_ids="tag-a, tag-b,")
        )

        _, kwargs = controller.history_query.get_matching_ids.call_args
        assert kwargs["tag_ids"] == ["tag-a", "tag-b"]

    def test_invalid_date_filter_maps_to_400(self, controller):
        controller.history_query.get_matching_ids.side_effect = InvalidDateFilterException("bad date")

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(controller.get_matching_generation_ids(_user(), created_from="not-a-date"))

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "invalid_date_format"
