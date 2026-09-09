"""
Sampling catalog controller.

Exposes the sampler/schedule registries (`src/platform/runtime/native/sampling/registry.py`)
as a catalog for form fields (the `sampler`/`schedule` field types,
`src/features/fields/sampling_fields.py`) and any other consumer to read - the
registries are the single source of truth for what samplers/schedules exist;
there is no other list of names anywhere.
"""
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.platform.runtime.native.sampling.registry import (
    SamplingRegistry,
    sampler_registry,
    schedule_registry,
)

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class SamplingController(BaseController):
    """Controller for the sampler/schedule catalog endpoint."""

    def __init__(self, samplers: SamplingRegistry = sampler_registry, schedules: SamplingRegistry = schedule_registry):
        super().__init__()
        self.samplers = samplers
        self.schedules = schedules

    async def get_catalog(self, family: Optional[str] = None) -> APIResponse:
        """Return every registered sampler/schedule, narrowed to `family` when given."""
        try:
            samplers: List[Any] = self.samplers.for_family(family) if family else self.samplers.definitions()
            schedules: List[Any] = self.schedules.for_family(family) if family else self.schedules.definitions()
            data: Dict[str, Any] = {
                "samplers": [d.to_dict() for d in samplers],
                "schedules": [d.to_dict() for d in schedules],
            }
            return self.success_response(data=data)
        except Exception as e:
            self.logger.error(f"Failed to get sampling catalog: {e}")
            return self.error_response(
                error="sampling_catalog_failed",
                message=f"Failed to get sampling catalog: {str(e)}",
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = SamplingController()

    router = APIRouter(prefix="/api/sampling", tags=["Sampling"])

    @router.get("/catalog", response_model=APIResponse, summary="Get Sampling Catalog")
    async def get_sampling_catalog(
        family: Optional[str] = None,
        current_user=Depends(get_current_active_user),
    ):
        """Get the sampler/schedule catalog, optionally narrowed to a model family."""
        return await controller.get_catalog(family)

    return router
