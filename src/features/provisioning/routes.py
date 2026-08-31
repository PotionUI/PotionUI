"""Compute-provisioning controller and admin router.

Every route is admin-only - provisioning infrastructure and spending a
provider's credits is not a regular-user action (mirrors the former
`runpod-provider` plugin's own router-level gating, now here instead since
this is where provisioning lives).
"""

from typing import TYPE_CHECKING, Any, Dict

from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import User
from src.features.backends.backend_registry import BackendRegistry
from src.features.provisioning import operations
from src.features.provisioning.contracts import ComputeProvisionerError
from src.features.provisioning.dto import ProviderFieldsRequest, ProvisionComputeRequest
from src.features.provisioning.operations import (
    BackendAlreadyProvisionedError,
    BackendNotFoundError,
    InvalidProvisionValuesError,
    NotARemoteBackendError,
    ProvisionedComputeNotFoundError,
    UnknownProviderError,
)
from src.features.provisioning.registry import ComputeProvisionerRegistry
from src.features.provisioning.repository import ProvisionedComputeRepository

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


def _field_dict(descriptor) -> dict:
    return {
        "key": descriptor.key,
        "label": descriptor.label,
        "type": descriptor.type,
        "required": descriptor.required,
        "default": descriptor.default,
        "help_text": descriptor.help_text,
        "depends_on": list(descriptor.depends_on),
        "options": [
            {"value": o.value, "label": o.label, "detail": o.detail}
            for o in descriptor.options
        ] if descriptor.options else None,
    }


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
                {
                    "provider_id": p.provider_id,
                    "label": p.label or p.provider_id,
                    "signup_url": p.signup_url,
                    "signup_note": p.signup_note,
                }
                for p in providers
            ]
        })

    async def describe_fields(self, provider_id: str, values: Dict[str, Any]) -> APIResponse:
        try:
            fields = await operations.describe_fields(self.registry, provider_id, values)
        except UnknownProviderError as e:
            return self.error_api_response(error="unknown_provider", message=str(e))
        except ComputeProvisionerError as e:
            # A provider that cannot describe its form right now (e.g. its
            # upstream catalog is unreachable) - the admin UI shows this
            # message with a retry instead of rendering a degraded form.
            return self.error_api_response(error="provider_unavailable", message=str(e))
        return self.success_response(data={"fields": [_field_dict(f) for f in fields]})

    async def list_provisioned(self) -> APIResponse:
        rows = self.repository.list_all()
        return self.success_response(data={"items": [row.to_dict() for row in rows]})

    async def get_by_backend(self, backend_id: str) -> APIResponse:
        row = self.repository.get_by_backend_id(backend_id)
        if row is None:
            return self.error_response(
                error="not_found",
                message=f"No provisioned compute linked to backend '{backend_id}'",
                status_code=404,
            )
        return self.success_response(data=row.to_dict())

    async def provision(self, request: ProvisionComputeRequest, user: User) -> APIResponse:
        try:
            row = await operations.provision_compute(
                self.registry,
                self.repository,
                self.backend_registry,
                provider_id=request.provider_id,
                backend_id=request.backend_id,
                profile_name=request.name,
                values=request.values,
                created_by=user.id,
            )
        except UnknownProviderError as e:
            return self.error_api_response(error="unknown_provider", message=str(e))
        except BackendNotFoundError as e:
            return self.error_api_response(error="backend_not_found", message=str(e))
        except NotARemoteBackendError as e:
            return self.error_api_response(error="not_a_remote_backend", message=str(e))
        except BackendAlreadyProvisionedError as e:
            return self.error_api_response(error="backend_already_provisioned", message=str(e))
        except InvalidProvisionValuesError as e:
            return self.error_api_response(error="invalid_values", message=str(e))
        except ComputeProvisionerError as e:
            # Raised by the provisioner itself (a rejected API key, a rate limit, a
            # gone resource, ...) - its message is provider-facing and safe to show
            # verbatim, unlike handle_exception's default (which never echoes str(e),
            # since an arbitrary exception can carry paths or connection strings).
            return self.error_api_response(error="provision_failed", message=str(e))
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

    @router.post("/providers/{provider_id}/fields", response_model=APIResponse, summary="Describe Provider Fields")
    async def describe_fields(provider_id: str, body: ProviderFieldsRequest) -> APIResponse:
        return await controller.describe_fields(provider_id, body.values)

    @router.get("", response_model=APIResponse, summary="List Provisioned Compute")
    async def list_provisioned() -> APIResponse:
        return await controller.list_provisioned()

    @router.get("/by-backend/{backend_id}", response_model=APIResponse, summary="Get Provisioned Compute By Backend")
    async def get_by_backend(backend_id: str) -> APIResponse:
        return await controller.get_by_backend(backend_id)

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
