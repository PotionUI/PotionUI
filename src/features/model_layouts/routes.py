from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.features.model_layouts.dto import ModelLayoutListResponse, ModelLayoutSummary
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


def build_router(container: "AppContainer") -> APIRouter:
    router = APIRouter(prefix="/api/models/layouts", tags=["Model Layouts"])

    @router.get("", response_model=ModelLayoutListResponse, summary="Available model layout profiles")
    async def list_layouts(current_user: User = Depends(get_current_admin_user)) -> ModelLayoutListResponse:
        catalog = container.model_layout_catalog
        return ModelLayoutListResponse(
            layouts=[
                ModelLayoutSummary(id=layout.id, label=layout.label, source=layout.source, plugin_id=layout.plugin_id)
                for layout in catalog.list_layouts()
            ],
            load_errors=dict(catalog.load_errors),
        )

    return router
