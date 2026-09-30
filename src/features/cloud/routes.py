from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, Depends, Query

from src.features.cloud.capabilities import CloudCapabilities
from src.features.cloud.catalog import CloudCatalog
from src.features.cloud.contracts import CloudError
from src.features.cloud.dto import CatalogSelectionRequest
from src.features.cloud.errors import CloudCatalogError
from src.platform.http.base_controller import APIResponse, BaseController
from src.features.models.exceptions import ModelAccessDeniedException, ModelNotFoundException
from src.platform.security.current_user import get_current_active_user, get_current_admin_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class CloudCatalogController(BaseController):
    def __init__(self, catalog: CloudCatalog):
        super().__init__()
        self.catalog = catalog

    async def list_entries(
        self,
        backend_id: str,
        task: Optional[str],
        output: Optional[str],
        enabled: Optional[bool],
        search: Optional[str],
        limit: int,
        offset: int,
    ) -> APIResponse:
        try:
            data = self.catalog.list_entries(
                backend_id, task=task, output=output, enabled=enabled, search=search, limit=limit, offset=offset
            )
        except CloudCatalogError as error:
            self.error_response(error=error.code, message=str(error), status_code=error.status_code)
        return self.success_response(data=data)

    async def refresh(self, backend_id: str) -> APIResponse:
        try:
            data = await self.catalog.refresh(backend_id)
        except CloudCatalogError as error:
            self.error_response(error=error.code, message=str(error), status_code=error.status_code)
        except CloudError as error:
            self.error_response(error=f"cloud_{error.kind}", message=error.user_message, status_code=502)
        except Exception as error:
            self.handle_exception(error, "cloud_catalog_refresh_failed", "Refreshing the catalog failed.")
        return self.success_response(data=data)

    async def set_enabled(self, backend_id: str, request: CatalogSelectionRequest, enabled: bool) -> APIResponse:
        try:
            data = await self.catalog.set_enabled(backend_id, request.slugs, enabled)
        except CloudCatalogError as error:
            self.error_response(error=error.code, message=str(error), status_code=error.status_code)
        except Exception as error:
            self.handle_exception(error, "cloud_catalog_update_failed", "Updating the catalog failed.")
        return self.success_response(data=data)


class CloudCapabilitiesController(BaseController):
    def __init__(self, capabilities: CloudCapabilities):
        super().__init__()
        self.capabilities = capabilities

    async def get_capabilities(self, model_id: str, user: Any, driver: Optional[str]) -> APIResponse:
        try:
            data = self.capabilities.payload(model_id, user, driver)
        except (ModelNotFoundException, ModelAccessDeniedException):
            self.error_response(error="model_not_found", message=f"Model '{model_id}' not found", status_code=404)
        return self.success_response(data=data)


def build_router(container: "AppContainer") -> APIRouter:
    controller = CloudCatalogController(container.cloud_catalog)
    capabilities_controller = CloudCapabilitiesController(container.cloud_capabilities)
    router = APIRouter(prefix="/api/cloud", tags=["Cloud"])

    @router.get(
        "/models/{model_id}/capabilities", response_model=APIResponse, summary="Get a cloud model's capabilities"
    )
    async def get_model_capabilities(
        model_id: str,
        driver: Optional[str] = None,
        current_user=Depends(get_current_active_user),
    ):
        return await capabilities_controller.get_capabilities(model_id, current_user, driver)

    @router.get("/backends/{backend_id}/catalog", response_model=APIResponse, summary="List a cloud backend's catalog")
    async def list_catalog(
        backend_id: str,
        task: Optional[str] = None,
        output: Optional[str] = None,
        enabled: Optional[bool] = None,
        search: Optional[str] = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        admin=Depends(get_current_admin_user),
    ):
        return await controller.list_entries(backend_id, task, output, enabled, search, limit, offset)

    @router.post(
        "/backends/{backend_id}/catalog/refresh", response_model=APIResponse, summary="Refresh a cloud backend's catalog"
    )
    async def refresh_catalog(backend_id: str, admin=Depends(get_current_admin_user)):
        return await controller.refresh(backend_id)

    @router.post(
        "/backends/{backend_id}/catalog/enable", response_model=APIResponse, summary="Enable catalog models"
    )
    async def enable_models(backend_id: str, request: CatalogSelectionRequest, admin=Depends(get_current_admin_user)):
        return await controller.set_enabled(backend_id, request, True)

    @router.post(
        "/backends/{backend_id}/catalog/disable", response_model=APIResponse, summary="Disable catalog models"
    )
    async def disable_models(backend_id: str, request: CatalogSelectionRequest, admin=Depends(get_current_admin_user)):
        return await controller.set_enabled(backend_id, request, False)

    return router
