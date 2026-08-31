"""Compute-provisioning controller and admin router.

Every route is admin-only - provisioning infrastructure and spending a
provider's credits is not a regular-user action (mirrors the former
`runpod-provider` plugin's own router-level gating, now here instead since
this is where provisioning lives).
"""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import User
from src.features.backends.backend_registry import BackendRegistry
from src.features.provisioning import operations
from src.features.provisioning.contracts import ProvisionRequest
from src.features.provisioning.dto import ProvisionComputeRequest
from src.features.provisioning.operations import (
    ProvisionedComputeNotFoundError,
    UnknownProviderError,
)
from src.features.provisioning.registry import ComputeProvisionerRegistry
from src.features.provisioning.repository import ProvisionedComputeRepository

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class ProvisioningController(BaseController):
    def __init__(
        self,
        registry: ComputeProvisionerRegistry,
        repository: ProvisionedComputeRepository,
        backend_registry: BackendRegistry,
    ):
        super().__init__()
        self.registry = registry
        self.repository = repository
        self.backend_registry = backend_registry

    async def list_providers(self) -> APIResponse:
        providers = operations.list_providers(self.registry)
        return self.success_response(data={
            "providers": [
                {"provider_id": p.provider_id, "label": p.label or p.provider_id}
                for p in providers
            ]
        })

    async def list_gpu_types(self, provider_id: str) -> APIResponse:
        try:
            gpu_types = await operations.list_gpu_types(self.registry, provider_id)
        except UnknownProviderError as e:
            return self.error_api_response(error="unknown_provider", message=str(e))
        return self.success_response(data={
            "gpu_types": [{"id": g.id, "memory_gb": g.memory_gb} for g in gpu_types]
        })

    async def list_provisioned(self) -> APIResponse:
        rows = self.repository.list_all()
        return self.success_response(data={"items": [row.to_dict() for row in rows]})

    async def provision(self, request: ProvisionComputeRequest, user: User) -> APIResponse:
        try:
            row = await operations.provision_compute(
                self.registry,
                self.repository,
                self.backend_registry,
                provider_id=request.provider_id,
                request=ProvisionRequest(
                    profile_name=request.profile_name,
                    gpu_type_id=request.gpu_type_id,
                    region=request.region,
                    image_ref=request.image_ref,
                    volume_size_gb=request.volume_size_gb,
                    worker_port=request.worker_port,
                    container_disk_gb=request.container_disk_gb,
                ),
                backend_name=request.backend_name,
                created_by=user.id,
            )
        except UnknownProviderError as e:
            return self.error_api_response(error="unknown_provider", message=str(e))
        except Exception as e:
            self.handle_exception(e, error_code="provision_failed")
        return self.success_response(data=row.to_dict())

    async def refresh_status(self, row_id: str) -> APIResponse:
        try:
            row = await operations.refresh_status(self.registry, self.repository, row_id)
        except ProvisionedComputeNotFoundError as e:
            return self.error_api_response(error="not_found", message=str(e))
        except UnknownProviderError as e:
            return self.error_api_response(error="unknown_provider", message=str(e))
        return self.success_response(data=row.to_dict())

    async def stop(self, row_id: str) -> APIResponse:
        try:
            row = await operations.stop_compute(self.registry, self.repository, self.backend_registry, row_id)
        except ProvisionedComputeNotFoundError as e:
            return self.error_api_response(error="not_found", message=str(e))
        except UnknownProviderError as e:
            return self.error_api_response(error="unknown_provider", message=str(e))
        return self.success_response(data=row.to_dict())

    async def terminate(self, row_id: str) -> APIResponse:
        try:
            await operations.terminate_compute(self.registry, self.repository, self.backend_registry, row_id)
        except ProvisionedComputeNotFoundError as e:
            return self.error_api_response(error="not_found", message=str(e))
        except UnknownProviderError as e:
            return self.error_api_response(error="unknown_provider", message=str(e))
        return self.success_response(data={"terminated": row_id})


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller = container.provisioning_controller
    router = APIRouter(
        prefix="/api/admin/provisioning",
        tags=["Compute Provisioning"],
        dependencies=[Depends(get_current_admin_user)],
    )

    @router.get("/providers", response_model=APIResponse, summary="List Compute Providers")
    async def list_providers() -> APIResponse:
        return await controller.list_providers()

    @router.get("/providers/{provider_id}/gpu-types", response_model=APIResponse, summary="List GPU Types")
    async def list_gpu_types(provider_id: str) -> APIResponse:
        return await controller.list_gpu_types(provider_id)

    @router.get("", response_model=APIResponse, summary="List Provisioned Compute")
    async def list_provisioned() -> APIResponse:
        return await controller.list_provisioned()

    @router.post("", response_model=APIResponse, summary="Provision Compute")
    async def provision(
        request: ProvisionComputeRequest,
        current_user: User = Depends(get_current_admin_user),
    ) -> APIResponse:
        return await controller.provision(request, current_user)

    @router.get("/{row_id}", response_model=APIResponse, summary="Refresh Provisioned Compute Status")
    async def refresh_status(row_id: str) -> APIResponse:
        return await controller.refresh_status(row_id)

    @router.post("/{row_id}/stop", response_model=APIResponse, summary="Stop Provisioned Compute")
    async def stop(row_id: str) -> APIResponse:
        return await controller.stop(row_id)

    @router.post("/{row_id}/terminate", response_model=APIResponse, summary="Terminate Provisioned Compute")
    async def terminate(row_id: str) -> APIResponse:
        return await controller.terminate(row_id)

    return router
