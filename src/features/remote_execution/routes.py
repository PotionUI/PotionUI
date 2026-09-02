"""Admin routes for syncing the host's model files onto a `native.remote`
worker's depot - Admin -> Backends -> <name> -> Models.

Every route is admin-only, same as `src.features.provisioning.routes` - model
sync spends worker disk/bandwidth and is configuration, not a regular-user
action.
"""

from typing import TYPE_CHECKING, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.features.providers.registry import ensure_providers_discovered
from src.features.remote_execution import ops
from src.features.remote_execution.transport import WorkerTransportError, WorkerUnreachableError
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_admin_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class ModelIdsRequest(BaseModel):
    model_ids: List[str]


class RemoteModelsController(BaseController):
    def __init__(self, container: "AppContainer"):
        super().__init__()
        self.container = container

    def _transport(self, backend_id: str):
        config = ops.resolve_remote_backend_config(self.container.backend_registry, backend_id)
        return ops.transport_for(config)

    def _transport_error_response(self, e: WorkerTransportError) -> APIResponse:
        if isinstance(e, WorkerUnreachableError) and e.reason == "not_running":
            return self.error_api_response(
                error="worker_not_running", message="The worker is stopped or still starting",
            )
        return self.error_api_response(error="worker_unreachable", message=str(e))

    async def sync_view(self, backend_id: str) -> APIResponse:
        try:
            transport = self._transport(backend_id)
        except ops.RemoteModelsBackendError as e:
            return self.error_api_response(error="invalid_backend", message=str(e))
        try:
            provider_registry = await ensure_providers_discovered()
            models = await ops.sync_view(self.container.model_repository, provider_registry, transport)
        except WorkerTransportError as e:
            return self._transport_error_response(e)
        return self.success_response(data={"models": models})

    async def push(self, backend_id: str, body: ModelIdsRequest) -> APIResponse:
        try:
            transport = self._transport(backend_id)
        except ops.RemoteModelsBackendError as e:
            return self.error_api_response(error="invalid_backend", message=str(e))
        try:
            transfers = await ops.push_models(
                body.model_ids, model_repository=self.container.model_repository, transport=transport,
            )
        except WorkerTransportError as e:
            return self._transport_error_response(e)
        return self.success_response(data={"transfers": transfers})

    async def fetch(self, backend_id: str, body: ModelIdsRequest) -> APIResponse:
        try:
            transport = self._transport(backend_id)
        except ops.RemoteModelsBackendError as e:
            return self.error_api_response(error="invalid_backend", message=str(e))
        provider_registry = await ensure_providers_discovered()
        try:
            transfers = await ops.fetch_models(
                body.model_ids, model_repository=self.container.model_repository,
                provider_registry=provider_registry, transport=transport,
            )
        except WorkerTransportError as e:
            return self._transport_error_response(e)
        return self.success_response(data={"transfers": transfers})

    async def transfers(self, backend_id: str) -> APIResponse:
        try:
            transport = self._transport(backend_id)
        except ops.RemoteModelsBackendError as e:
            return self.error_api_response(error="invalid_backend", message=str(e))
        try:
            transfers = await ops.list_transfers(transport)
        except WorkerTransportError as e:
            return self._transport_error_response(e)
        return self.success_response(data={"transfers": transfers})


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller = RemoteModelsController(container)
    router = APIRouter(
        prefix="/api/admin/remote-models",
        tags=["Remote Model Sync"],
        dependencies=[Depends(get_current_admin_user)],
    )

    @router.get("/{backend_id}", response_model=APIResponse, summary="Remote Model Sync View")
    async def sync_view(backend_id: str) -> APIResponse:
        return await controller.sync_view(backend_id)

    @router.post("/{backend_id}/push", response_model=APIResponse, summary="Push Models To Worker")
    async def push(backend_id: str, body: ModelIdsRequest) -> APIResponse:
        return await controller.push(backend_id, body)

    @router.post("/{backend_id}/fetch", response_model=APIResponse, summary="Fetch Models Onto Worker")
    async def fetch(backend_id: str, body: ModelIdsRequest) -> APIResponse:
        return await controller.fetch(backend_id, body)

    @router.get("/{backend_id}/transfers", response_model=APIResponse, summary="List Worker Model Transfers")
    async def transfers(backend_id: str) -> APIResponse:
        return await controller.transfers(backend_id)

    return router
