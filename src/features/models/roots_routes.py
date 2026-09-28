import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from src.features.models import root_detection
from src.features.models.roots import BindingSpec, ModelRootsError, ModelRootsManager
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
    "model_roots_not_found": 404,
}


class BindingRequest(BaseModel):
    model_type: str
    subdir: str


class DetectRootRequest(BaseModel):
    path: str


class CreateRootRequest(BaseModel):
    path: str
    label: Optional[str] = None
    bindings: List[BindingRequest] = []
    read_only: bool = False
    write_types: List[str] = []


class UpdateRootRequest(BaseModel):
    label: Optional[str] = None
    path: Optional[str] = None
    read_only: Optional[bool] = None
    bindings: Optional[List[BindingRequest]] = None


class ReorderRootsRequest(BaseModel):
    model_type: Optional[str] = None
    root_ids: List[str]


class SetWriteRootRequest(BaseModel):
    model_type: Optional[str] = None
    root_id: str


class ModelRootsController(BaseController):

    def __init__(self, manager: ModelRootsManager, indexing_coordinator: "ModelIndexingCoordinator"):
        super().__init__()
        self.manager = manager
        self.indexing = indexing_coordinator

    def _raise_for(self, error: ModelRootsError):
        status_code = _STATUS_BY_CODE.get(error.code, 400)
        self.error_response(error=error.code, message=error.reason, status_code=status_code)

    def _queue_reindex(self, background_tasks: BackgroundTasks) -> None:
        background_tasks.add_task(self.indexing.run_indexing)

    async def list_roots(self) -> APIResponse:
        return self.success_response(data=self.manager.get_overview())

    async def detect(self, request: DetectRootRequest) -> APIResponse:
        result = root_detection.detect(request.path, resolver=self.manager.resolver)
        return self.success_response(data=result.to_dict())

    async def create_root(self, request: CreateRootRequest, background_tasks: BackgroundTasks) -> APIResponse:
        try:
            root = self.manager.create_root(
                request.path,
                label=request.label,
                bindings=[BindingSpec(b.model_type, b.subdir) for b in request.bindings],
                read_only=request.read_only,
                write_types=request.write_types,
                idempotent=False,
            )
        except ModelRootsError as error:
            self._raise_for(error)
        self._queue_reindex(background_tasks)
        return self.success_response(data=root)

    async def update_root(
        self, root_id: str, request: UpdateRootRequest, background_tasks: BackgroundTasks
    ) -> APIResponse:
        try:
            root = self.manager.update_root(
                root_id,
                label=request.label,
                path=request.path,
                read_only=request.read_only,
                bindings=(
                    [BindingSpec(b.model_type, b.subdir) for b in request.bindings]
                    if request.bindings is not None
                    else None
                ),
            )
        except ModelRootsError as error:
            self._raise_for(error)
        self._queue_reindex(background_tasks)
        return self.success_response(data=root)

    async def delete_root(self, root_id: str, background_tasks: BackgroundTasks) -> APIResponse:
        try:
            self.manager.delete_root(root_id)
        except ModelRootsError as error:
            self._raise_for(error)
        self._queue_reindex(background_tasks)
        return self.success_response(data={"id": root_id, "deleted": True})

    async def reorder(self, request: ReorderRootsRequest, background_tasks: BackgroundTasks) -> APIResponse:
        try:
            self.manager.reorder(request.model_type, request.root_ids)
        except ModelRootsError as error:
            self._raise_for(error)
        self._queue_reindex(background_tasks)
        return self.success_response(data=self.manager.get_overview())

    async def set_write_root(self, request: SetWriteRootRequest, background_tasks: BackgroundTasks) -> APIResponse:
        try:
            root = self.manager.set_write_root(request.model_type, request.root_id)
        except ModelRootsError as error:
            self._raise_for(error)
        self._queue_reindex(background_tasks)
        return self.success_response(data=root)

    async def probe_root(self, root_id: str) -> APIResponse:
        try:
            root = self.manager.probe_root(root_id)
        except ModelRootsError as error:
            self._raise_for(error)
        return self.success_response(data=root)


def build_router(container: "AppContainer") -> APIRouter:
    controller = ModelRootsController(container.model_roots_manager, container.model_index_manager.indexing)
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
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.reorder(request, background_tasks)

    @router.put("/write", response_model=APIResponse, summary="Set Model Root Write Target")
    async def set_write_root(
        request: SetWriteRootRequest,
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.set_write_root(request, background_tasks)

    @router.post("", response_model=APIResponse, status_code=201, summary="Create Model Root")
    async def create_root(
        request: CreateRootRequest,
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.create_root(request, background_tasks)

    @router.patch("/{root_id}", response_model=APIResponse, summary="Update Model Root")
    async def update_root(
        root_id: str,
        request: UpdateRootRequest,
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.update_root(root_id, request, background_tasks)

    @router.delete("/{root_id}", response_model=APIResponse, summary="Delete Model Root")
    async def delete_root(
        root_id: str,
        background_tasks: BackgroundTasks,
        current_user: User = Depends(get_current_admin_user),
    ):
        return await controller.delete_root(root_id, background_tasks)

    @router.post("/{root_id}/probe", response_model=APIResponse, summary="Probe Model Root")
    async def probe_root(root_id: str, current_user: User = Depends(get_current_admin_user)):
        return await controller.probe_root(root_id)

    return router
