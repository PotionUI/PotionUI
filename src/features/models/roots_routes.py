import asyncio
import logging
import os
import platform
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.features.model_layouts.detection import UnknownLayoutError, detect_layout
from src.features.model_layouts.schema import GENERIC_LAYOUT_ID
from src.features.models.roots import BindingRef, BindingSpec, ModelRootsError, ModelRootsManager
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_admin_user
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer
    from src.features.models.indexing_coordinator import ModelIndexingCoordinator

logger = logging.getLogger(__name__)

_STATUS_BY_CODE = {
    "model_roots_overlap": 409,
    "model_roots_offline": 409,
    "model_roots_read_only": 409,
    "model_roots_write_probe_failed": 422,
    "model_roots_generation_active": 409,
    "model_roots_home_protected": 409,
    "model_roots_duplicate": 409,
    "model_roots_invalid_binding": 422,
    "model_roots_binding_nested": 422,
    "model_roots_duplicate_binding": 422,
    "model_roots_not_found": 404,
}


class BindingRequest(BaseModel):
    model_type: str
    subdir: str
    scan_headers: Optional[bool] = None
    write: Optional[bool] = None


class BindingRefRequest(BaseModel):
    model_type: str
    subdir: str


class BindingScanRequest(BaseModel):
    model_type: str
    subdir: str
    scan_headers: bool


class DetectRootRequest(BaseModel):
    path: str
    profile: Optional[str] = None


class CreateRootRequest(BaseModel):
    path: str
    label: Optional[str] = None
    bindings: List[BindingRequest] = []
    read_only: bool = False
    write_types: List[str] = []
    profile: Optional[str] = None


class UpdateRootRequest(BaseModel):
    label: Optional[str] = None
    path: Optional[str] = None
    read_only: Optional[bool] = None
    bindings: Optional[List[BindingRequest]] = None
    remove_types: Optional[List[str]] = None
    remove_bindings: Optional[List[BindingRefRequest]] = None


class ReorderRootsRequest(BaseModel):
    model_type: Optional[str] = None
    root_ids: List[str]


class SetWriteRootRequest(BaseModel):
    model_type: Optional[str] = None
    root_id: str
    subdir: Optional[str] = None


class ModelRootsController(BaseController):

    def __init__(
        self, manager: ModelRootsManager, indexing_coordinator: "ModelIndexingCoordinator", layout_catalog: Any = None
    ):
        super().__init__()
        self.manager = manager
        self.indexing = indexing_coordinator
        self.layout_catalog = layout_catalog

    def _raise_for(self, error: ModelRootsError):
        status_code = _STATUS_BY_CODE.get(error.code, 400)
        self.error_response(error=error.code, message=error.reason, status_code=status_code)

    async def list_roots(self) -> APIResponse:
        data = self.manager.get_overview()
        data["server_os"] = platform.system() or "Linux"
        data["path_style"] = "windows" if os.name == "nt" else "posix"
        return self.success_response(data=data)

    async def detect(self, request: DetectRootRequest) -> APIResponse:
        try:
            result = await asyncio.to_thread(
                detect_layout,
                request.path,
                catalog=self.layout_catalog,
                profile=request.profile,
                resolver=self.manager.resolver,
            )
        except UnknownLayoutError as error:
            self.error_response(error=error.code, message=str(error), status_code=400)
        return self.success_response(data=result)

    async def create_root(self, request: CreateRootRequest) -> APIResponse:
        if request.profile and request.profile != GENERIC_LAYOUT_ID:
            if self.layout_catalog is None or self.layout_catalog.get_layout(request.profile) is None:
                self.error_response(
                    error="model_layout_unknown",
                    message=f"Unknown model layout '{request.profile}'",
                    status_code=400,
                )
        try:
            root = self.manager.create_root(
                request.path,
                label=request.label,
                bindings=[BindingSpec(b.model_type, b.subdir, b.scan_headers, b.write) for b in request.bindings],
                read_only=request.read_only,
                write_types=request.write_types,
                idempotent=False,
                layout_profile=request.profile,
            )
        except ModelRootsError as error:
            self._raise_for(error)
        return self.success_response(data=root)

    async def update_root(self, root_id: str, request: UpdateRootRequest) -> APIResponse:
        try:
            root = self.manager.update_root(
                root_id,
                label=request.label,
                path=request.path,
                read_only=request.read_only,
                bindings=(
                    [BindingSpec(b.model_type, b.subdir, b.scan_headers, b.write) for b in request.bindings]
                    if request.bindings is not None
                    else None
                ),
                remove_types=request.remove_types,
                remove_bindings=(
                    [BindingRef(b.model_type, b.subdir) for b in request.remove_bindings]
                    if request.remove_bindings is not None
                    else None
                ),
            )
        except ModelRootsError as error:
            self._raise_for(error)
        return self.success_response(data=root)

    async def set_binding_scan(self, root_id: str, request: BindingScanRequest) -> APIResponse:
        try:
            root = self.manager.set_binding_scan_headers(
                root_id, request.model_type, request.subdir, request.scan_headers
            )
        except ModelRootsError as error:
            self._raise_for(error)
        return self.success_response(data=root)

    async def delete_root(self, root_id: str) -> APIResponse:
        try:
            self.manager.delete_root(root_id)
        except ModelRootsError as error:
            self._raise_for(error)
        return self.success_response(data={"id": root_id, "deleted": True})

    async def reorder(self, request: ReorderRootsRequest) -> APIResponse:
        try:
            self.manager.reorder(request.model_type, request.root_ids)
        except ModelRootsError as error:
            self._raise_for(error)
        return self.success_response(data=self.manager.get_overview())

    async def set_write_root(self, request: SetWriteRootRequest) -> APIResponse:
        try:
            root = self.manager.set_write_root(request.model_type, request.root_id, request.subdir)
        except ModelRootsError as error:
            self._raise_for(error)
        return self.success_response(data=root)

    async def probe_root(self, root_id: str) -> APIResponse:
        try:
            root = self.manager.probe_root(root_id)
        except ModelRootsError as error:
            self._raise_for(error)
        return self.success_response(data=root)


def build_router(container: "AppContainer") -> APIRouter:
    controller = ModelRootsController(
        container.model_roots_manager, container.model_index_manager.indexing, container.model_layout_catalog
    )
    router = APIRouter(prefix="/api/models/roots", tags=["Model Roots"])

    @router.get("", response_model=APIResponse, summary="List Model Roots")
    async def list_roots(current_user: User = Depends(get_current_admin_user)):
        return await controller.list_roots()

    @router.post("/detect", response_model=APIResponse, summary="Detect Model Root Layout")
    async def detect_root(request: DetectRootRequest, current_user: User = Depends(get_current_admin_user)):
        return await controller.detect(request)

    @router.put("/order", response_model=APIResponse, summary="Reorder Model Roots")
    async def reorder_roots(
        request: ReorderRootsRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.reorder(request)

    @router.put("/write", response_model=APIResponse, summary="Set Model Root Write Target")
    async def set_write_root(
        request: SetWriteRootRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.set_write_root(request)

    @router.post("", response_model=APIResponse, status_code=201, summary="Create Model Root")
    async def create_root(
        request: CreateRootRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.create_root(request)

    @router.patch("/{root_id}", response_model=APIResponse, summary="Update Model Root")
    async def update_root(
        root_id: str,
        request: UpdateRootRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.update_root(root_id, request)

    @router.patch("/{root_id}/bindings", response_model=APIResponse, summary="Update Model Root Binding")
    async def update_binding(
        root_id: str,
        request: BindingScanRequest,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.set_binding_scan(root_id, request)

    @router.delete("/{root_id}", response_model=APIResponse, summary="Delete Model Root")
    async def delete_root(
        root_id: str,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.delete_root(root_id)

    @router.post("/{root_id}/probe", response_model=APIResponse, summary="Probe Model Root")
    async def probe_root(root_id: str, current_user: User = Depends(get_current_admin_user)):
        return await controller.probe_root(root_id)

    return router
