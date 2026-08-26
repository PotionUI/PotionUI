from unittest.mock import MagicMock, Mock

import pytest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.features.presets.file_repository import FilePresetRepository
from src.features.stats.repository import DIMENSIONS, StatsRepository
from src.features.stats.routes import StatsController, build_router
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User


def _user(account_type: AccountType) -> User:
    return User(
        id="user123",
        username="tester",
        email="tester@example.com",
        password_hash="hash",
        account_type=account_type,
    )


class TestStatsController:
    """StatsController is admin-only, and enforces that in the method body (this codebase has
    no route-level admin dependency). These tests bypass FastAPI entirely, as the other
    controller tests do, and assert the gate by passing different account types."""

    @pytest.fixture
    def mock_stats_repository(self):
        repo = Mock(spec=StatsRepository)
        repo.overview.return_value = {'total_generations': 42}
        repo.durations.return_value = {'buckets': [], 'p50_ms': 17000}
        repo.timeseries.return_value = []
        repo.breakdown.return_value = {'dimension': 'preset', 'items': [], 'total_distinct': 0}
        repo.storage.return_value = {'by_type': []}
        return repo

    @pytest.fixture
    def mock_file_preset_repository(self):
        repo = Mock(spec=FilePresetRepository)
        repo.list_all_presets.return_value = []
        return repo

    @pytest.fixture
    def mock_generation_stats_repository(self):
        from src.features.stats.generation_stats_repository import GenerationStatsRepository

        repo = Mock(spec=GenerationStatsRepository)
        repo.preset_timing.return_value = [{'preset_id': 'p1', 'total_runs': 3}]
        repo.preset_resources.return_value = [{'preset_id': 'p1', 'peak_vram_mb': 8000.0}]
        return repo

    @pytest.fixture
    def controller(self, mock_stats_repository, mock_file_preset_repository, mock_generation_stats_repository):
        return StatsController(
            stats_repository=mock_stats_repository,
            file_preset_repository=mock_file_preset_repository,
            generation_stats_repository=mock_generation_stats_repository,
        )

    @pytest.fixture
    def admin(self):
        return _user(AccountType.ADMIN)

    # --- the admin gate ---------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,args", [
        ("get_overview", ()),
        ("get_timeseries", ("count", "day")),
        ("get_breakdown", ("preset",)),
        ("get_durations", ()),
        ("get_storage", ()),
        ("get_dimensions", ()),
        ("get_preset_timing", ()),
        ("get_preset_resources", ()),
    ])
    async def test_non_admin_is_forbidden(self, controller, method, args):
        with pytest.raises(HTTPException) as exc:
            await getattr(controller, method)(*args, user=_user(AccountType.USER))
        assert exc.value.status_code == 403
        assert exc.value.detail['error'] == 'admin_required'

    @pytest.mark.asyncio
    async def test_anonymous_is_unauthorized(self, controller):
        with pytest.raises(HTTPException) as exc:
            await controller.get_overview(user=None)
        assert exc.value.status_code == 401
        assert exc.value.detail['error'] == 'authentication_required'

    @pytest.mark.asyncio
    async def test_non_admin_never_reaches_the_repository(self, controller, mock_stats_repository):
        with pytest.raises(HTTPException):
            await controller.get_overview(user=_user(AccountType.USER))
        mock_stats_repository.overview.assert_not_called()

    # --- happy paths -------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_admin_gets_overview(self, controller, admin, mock_stats_repository):
        response = await controller.get_overview(user=admin)
        assert response.success is True
        assert response.data['total_generations'] == 42
        mock_stats_repository.overview.assert_called_once_with(date_from=None, date_to=None)

    @pytest.mark.asyncio
    async def test_date_range_is_forwarded(self, controller, admin, mock_stats_repository):
        await controller.get_overview('2026-01-01', '2026-01-31', user=admin)
        mock_stats_repository.overview.assert_called_once_with(date_from='2026-01-01', date_to='2026-01-31')

    @pytest.mark.asyncio
    async def test_breakdown_forwards_dimension_and_limit(self, controller, admin, mock_stats_repository):
        await controller.get_breakdown('model', 5, '2026-01-01', None, user=admin)
        mock_stats_repository.breakdown.assert_called_once_with(
            dimension='model', limit=5, date_from='2026-01-01', date_to=None
        )

    @pytest.mark.asyncio
    async def test_dimensions_lists_valid_dimensions(self, controller, admin):
        response = await controller.get_dimensions(user=admin)
        assert response.data['dimensions'] == list(DIMENSIONS)

    @pytest.mark.asyncio
    async def test_storage_forwards_limit(self, controller, admin, mock_stats_repository):
        await controller.get_storage('2026-01-01', None, 'day', 15, user=admin)
        mock_stats_repository.storage.assert_called_once_with(
            date_from='2026-01-01', date_to=None, bucket='day', limit=15
        )

    # --- generation_stats -------------------------------------------------

    @pytest.mark.asyncio
    async def test_preset_timing_forwards_limit(self, controller, admin, mock_generation_stats_repository):
        response = await controller.get_preset_timing(5, user=admin)
        mock_generation_stats_repository.preset_timing.assert_called_once_with(5)
        assert response.data['items'] == [{'preset_id': 'p1', 'total_runs': 3}]

    @pytest.mark.asyncio
    async def test_preset_resources_forwards_limit(self, controller, admin, mock_generation_stats_repository):
        response = await controller.get_preset_resources(5, user=admin)
        mock_generation_stats_repository.preset_resources.assert_called_once_with(5)
        assert response.data['items'] == [{'preset_id': 'p1', 'peak_vram_mb': 8000.0}]

    @pytest.mark.asyncio
    async def test_preset_timing_with_no_generation_stats_repository_returns_empty(self, admin, mock_stats_repository, mock_file_preset_repository):
        controller = StatsController(
            stats_repository=mock_stats_repository,
            file_preset_repository=mock_file_preset_repository,
            generation_stats_repository=None,
        )
        response = await controller.get_preset_timing(user=admin)
        assert response.data['items'] == []

    @pytest.mark.asyncio
    async def test_preset_resources_error_is_a_server_error(self, controller, admin, mock_generation_stats_repository):
        mock_generation_stats_repository.preset_resources.side_effect = RuntimeError("db is on fire")
        with pytest.raises(HTTPException) as exc:
            await controller.get_preset_resources(user=admin)
        assert exc.value.status_code == 500
        assert exc.value.detail['error'] == 'stats_preset_resources_failed'

    @pytest.mark.asyncio
    async def test_unexpected_error_is_a_server_error(self, controller, admin, mock_stats_repository):
        mock_stats_repository.overview.side_effect = RuntimeError("database is on fire")
        with pytest.raises(HTTPException) as exc:
            await controller.get_overview(user=admin)
        assert exc.value.status_code == 500
        assert exc.value.detail['error'] == 'stats_overview_failed'


class TestClosedQuerySets:
    """metric/bucket/dimension validation moved out onto the route's Enum
    Query types (MetricParam/BucketParam/DimensionParam) - an unknown value
    is now a 422 straight from FastAPI, not a ValueError translated to a 400."""

    @pytest.fixture
    def client(self):
        container = MagicMock()
        container.stats_controller = StatsController(
            stats_repository=Mock(spec=StatsRepository),
        )

        app = FastAPI()
        app.include_router(build_router(container))

        async def _fake_admin():
            return _user(AccountType.ADMIN)

        app.dependency_overrides[get_current_active_user] = _fake_admin
        return TestClient(app, raise_server_exceptions=False)

    def test_bad_metric_is_unprocessable(self, client):
        response = client.get("/api/stats/timeseries", params={"metric": "bogus"})
        assert response.status_code == 422

    def test_bad_bucket_is_unprocessable(self, client):
        response = client.get("/api/stats/timeseries", params={"bucket": "fortnight"})
        assert response.status_code == 422

    def test_bad_dimension_is_unprocessable(self, client):
        response = client.get("/api/stats/breakdown", params={"dimension": "; DROP TABLE generations;--"})
        assert response.status_code == 422
