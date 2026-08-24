"""Admin usage-statistics endpoints.

Every route is admin-only. This codebase has no route-level admin dependency: protected routes
all use `Depends(get_current_active_user)` and check `account_type` inside the controller (see
`settings_controller`). That convention is followed here rather than introducing a second
mechanism.
"""

from enum import Enum
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, Query

from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_active_user
from src.features.stats import StatsManager
from src.features.stats.repository import BUCKETS, DIMENSIONS, METRICS, StatsRepository
from src.features.stats.generation_stats_manager import GenerationStatsManager
from src.features.stats.generation_stats_repository import GenerationStatsRepository
from src.platform.security.user import AccountType, User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

# Closed query sets, mirrored from the repository's own vocabulary
# (stats/repository.py) so FastAPI rejects an unknown value with a 422 before
# it ever reaches the controller - the manager no longer re-validates them.
MetricParam = Enum("MetricParam", {v: v for v in METRICS})
BucketParam = Enum("BucketParam", {v: v for v in BUCKETS})
DimensionParam = Enum("DimensionParam", {v: v for v in DIMENSIONS})


class StatsController(BaseController):
    def __init__(
        self,
        stats_manager: StatsManager,
        stats_repository: StatsRepository,
        generation_stats_manager: Optional[GenerationStatsManager] = None,
        generation_stats_repository: Optional[GenerationStatsRepository] = None,
    ):
        super().__init__()
        self.stats_manager = stats_manager
        self.stats_repository = stats_repository
        self.generation_stats_manager = generation_stats_manager
        self.generation_stats_repository = generation_stats_repository

    def _require_admin(self, user: Optional[User]) -> None:
        if not user:
            self.error_response(
                error="authentication_required",
                message="Authentication required to access statistics",
                status_code=401,
            )
        if user.account_type != AccountType.ADMIN:
            self.error_response(
                error="admin_required",
                message="Statistics are available to administrators only",
                status_code=403,
            )

    async def get_overview(self, date_from=None, date_to=None, user=None) -> APIResponse:
        self._require_admin(user)
        try:
            return self.success_response(self.stats_repository.overview(date_from=date_from, date_to=date_to))
        except Exception as e:
            return self.handle_exception(e, "stats_overview_failed")

    async def get_timeseries(self, metric: str, bucket: str, date_from=None, date_to=None,
                             user=None) -> APIResponse:
        self._require_admin(user)
        try:
            return self.success_response(
                self.stats_manager.timeseries(metric, bucket, date_from, date_to)
            )
        except Exception as e:
            return self.handle_exception(e, "stats_timeseries_failed")

    async def get_breakdown(self, dimension: str, limit=10, date_from=None, date_to=None,
                            user=None) -> APIResponse:
        self._require_admin(user)
        try:
            return self.success_response(
                self.stats_manager.breakdown(dimension, limit, date_from, date_to)
            )
        except Exception as e:
            return self.handle_exception(e, "stats_breakdown_failed")

    async def get_durations(self, date_from=None, date_to=None, user=None) -> APIResponse:
        self._require_admin(user)
        try:
            return self.success_response(self.stats_repository.durations(date_from=date_from, date_to=date_to))
        except Exception as e:
            return self.handle_exception(e, "stats_durations_failed")

    async def get_storage(self, date_from=None, date_to=None, bucket: str = 'day', limit=30, user=None) -> APIResponse:
        self._require_admin(user)
        try:
            return self.success_response(self.stats_manager.storage(date_from, date_to, bucket, limit))
        except Exception as e:
            return self.handle_exception(e, "stats_storage_failed")

    async def get_dimensions(self, user=None) -> APIResponse:
        self._require_admin(user)
        return self.success_response({'dimensions': self.stats_manager.dimensions()})

    # --- generation_stats (durable, generation-independent store) ------

    async def get_preset_timing(self, limit=10, user=None) -> APIResponse:
        """Cold vs. warm start counts/averages per preset."""
        self._require_admin(user)
        if self.generation_stats_repository is None:
            return self.success_response({'items': []})
        try:
            return self.success_response({'items': self.generation_stats_repository.preset_timing(limit)})
        except Exception as e:
            return self.handle_exception(e, "stats_preset_timing_failed")

    async def get_preset_resources(self, limit=10, user=None) -> APIResponse:
        """Peak/average VRAM, RAM and CPU per preset."""
        self._require_admin(user)
        if self.generation_stats_repository is None:
            return self.success_response({'items': []})
        try:
            return self.success_response({'items': self.generation_stats_repository.preset_resources(limit)})
        except Exception as e:
            return self.handle_exception(e, "stats_preset_resources_failed")


# `from`/`to` are the public query names; `from` is a Python keyword, hence the aliases.
_FROM = Query(None, alias="from", description="Inclusive start date, YYYY-MM-DD")
_TO = Query(None, alias="to", description="Inclusive end date, YYYY-MM-DD")


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.stats_controller
    router = APIRouter(prefix="/api/stats", tags=["Stats"])

    @router.get("/overview", response_model=APIResponse, summary="Aggregate usage totals")
    async def get_overview(date_from: Optional[str] = _FROM, date_to: Optional[str] = _TO,
                           current_user=Depends(get_current_active_user)):
        return await controller.get_overview(date_from, date_to, current_user)

    @router.get("/timeseries", response_model=APIResponse, summary="A metric bucketed over time")
    async def get_timeseries(metric: MetricParam = Query(MetricParam.count), bucket: BucketParam = Query(BucketParam.day),
                             date_from: Optional[str] = _FROM, date_to: Optional[str] = _TO,
                             current_user=Depends(get_current_active_user)):
        return await controller.get_timeseries(metric.value, bucket.value, date_from, date_to, current_user)

    @router.get("/breakdown", response_model=APIResponse, summary="Ranked counts for one dimension")
    async def get_breakdown(dimension: DimensionParam = Query(...), limit: int = Query(10, ge=1, le=100),
                            date_from: Optional[str] = _FROM, date_to: Optional[str] = _TO,
                            current_user=Depends(get_current_active_user)):
        return await controller.get_breakdown(dimension.value, limit, date_from, date_to, current_user)

    @router.get("/durations", response_model=APIResponse, summary="Duration histogram and percentiles")
    async def get_durations(date_from: Optional[str] = _FROM, date_to: Optional[str] = _TO,
                            current_user=Depends(get_current_active_user)):
        return await controller.get_durations(date_from, date_to, current_user)

    @router.get("/storage", response_model=APIResponse, summary="Output file sizes and resolutions")
    async def get_storage(date_from: Optional[str] = _FROM, date_to: Optional[str] = _TO,
                          bucket: BucketParam = Query(BucketParam.day), limit: int = Query(30, ge=1, le=200),
                          current_user=Depends(get_current_active_user)):
        return await controller.get_storage(date_from, date_to, bucket.value, limit, current_user)

    @router.get("/dimensions", response_model=APIResponse, summary="Dimensions valid for /breakdown")
    async def get_dimensions(current_user=Depends(get_current_active_user)):
        return await controller.get_dimensions(current_user)

    @router.get(
        "/presets/timing", response_model=APIResponse,
        summary="Cold vs. warm start counts/averages per preset (durable generation_stats store)",
    )
    async def get_preset_timing(limit: int = Query(10, ge=1, le=200),
                                current_user=Depends(get_current_active_user)):
        return await controller.get_preset_timing(limit, current_user)

    @router.get(
        "/presets/resources", response_model=APIResponse,
        summary="Peak/average VRAM, RAM and CPU per preset (durable generation_stats store)",
    )
    async def get_preset_resources(limit: int = Query(10, ge=1, le=200),
                                   current_user=Depends(get_current_active_user)):
        return await controller.get_preset_resources(limit, current_user)

    return router
