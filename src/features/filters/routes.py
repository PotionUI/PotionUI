import asyncio
from typing import TYPE_CHECKING, Any, Dict

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from src.features.filters import operations
from src.features.filters.collaborators import FilterCollaborators
from src.features.filters.dto import CreateFilterRequest, UpdateFilterRequest
from src.features.filters.errors import FilterError
from src.features.filters.payload import PAYLOAD_SCHEMA, USER_ID_PREFIX, file_item, user_item
from src.features.filters.schema import FILTER_JSON_SCHEMA, SOURCE_MINE
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_active_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class FilterController(BaseController):

    def __init__(self, collaborators: FilterCollaborators):
        super().__init__()
        self.collaborators = collaborators

    def build_listing(self, owner_id: str) -> Dict[str, Any]:
        catalog = self.collaborators.catalog
        known = catalog.known_ops()
        enabled = catalog.enabled_ops()
        items = [file_item(definition, known, enabled) for definition in catalog.list_filters()]
        items += [user_item(row, known, enabled) for row in operations.list_mine(self.collaborators, owner_id)]
        groups = catalog.groups(extra=[item["group"] for item in items if item["source"] == SOURCE_MINE])
        for item in items:
            if item["group"] not in groups:
                groups.append(item["group"])
        items.sort(
            key=lambda item: (
                groups.index(item["group"]),
                item["source"] == SOURCE_MINE,
                item["order"],
                item["name"].casefold(),
                item["id"],
            )
        )
        ops = sorted(enabled.values(), key=lambda spec: (spec.source != "core", spec.id))
        return {
            "schema": PAYLOAD_SCHEMA,
            "filters": items,
            "ops": [spec.to_dict() for spec in ops],
            "groups": groups,
            "load_errors": catalog.load_errors,
        }

    async def list_all(self, owner_id: str) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(self.build_listing, owner_id)
        except Exception as exc:
            self.handle_exception(exc, error_code="list_filters_failed", message="Filter request failed")

    async def _run(self, error: str, func, *args) -> APIResponse:
        try:
            return self.success_response(data=await asyncio.to_thread(func, *args))
        except FilterError as exc:
            self.error_response(error=exc.code, message=exc.message, status_code=exc.status_code)
        except Exception as exc:
            self.handle_exception(exc, error_code=error, message="Filter request failed")

    def _item(self, record) -> Dict[str, Any]:
        catalog = self.collaborators.catalog
        return user_item(record, catalog.known_ops(), catalog.enabled_ops())

    async def list_mine(self, owner_id: str) -> APIResponse:
        def run():
            return [self._item(row) for row in operations.list_mine(self.collaborators, owner_id)]
        return await self._run("list_filters_failed", run)

    async def create_mine(self, owner_id: str, request: CreateFilterRequest) -> APIResponse:
        def run():
            return self._item(operations.create_filter(self.collaborators, owner_id, request))
        return await self._run("create_filter_failed", run)

    async def update_mine(self, owner_id: str, filter_id: str, request: UpdateFilterRequest) -> APIResponse:
        def run():
            return self._item(operations.update_filter(self.collaborators, owner_id, filter_id, request))
        return await self._run("update_filter_failed", run)

    async def delete_mine(self, owner_id: str, filter_id: str) -> APIResponse:
        def run():
            operations.delete_filter(self.collaborators, owner_id, filter_id)
            return None
        return await self._run("delete_filter_failed", run)

    def lut_response(self, filter_id: str, request: Request):
        definition = None if filter_id.startswith(USER_ID_PREFIX) else self.collaborators.catalog.get_filter(filter_id)
        path = definition.lut_path if definition is not None else None
        if definition is None or path is None or not path.is_file():
            self.error_response(error="filter_lut_not_found", message="This filter has no LUT", status_code=404)
        etag = f'"{definition.revision}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type="text/plain; charset=utf-8", headers=headers)


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.filter_controller
    router = APIRouter(prefix="/api/filters", tags=["Filters"])

    @router.get("/mine", response_model=APIResponse, summary="List my filters")
    async def list_mine(current_user=Depends(get_current_active_user)):
        return await controller.list_mine(current_user.id)

    @router.post("/mine", response_model=APIResponse, summary="Create a filter of my own")
    async def create_mine(request: CreateFilterRequest, current_user=Depends(get_current_active_user)):
        return await controller.create_mine(current_user.id, request)

    @router.patch("/mine/{filter_id}", response_model=APIResponse, summary="Update a filter of my own")
    async def update_mine(
        filter_id: str, request: UpdateFilterRequest, current_user=Depends(get_current_active_user)
    ):
        return await controller.update_mine(current_user.id, filter_id, request)

    @router.delete("/mine/{filter_id}", response_model=APIResponse, summary="Delete a filter of my own")
    async def delete_mine(filter_id: str, current_user=Depends(get_current_active_user)):
        return await controller.delete_mine(current_user.id, filter_id)

    @router.get("/schema", summary="The filter.yml JSON schema")
    async def filter_schema(current_user=Depends(get_current_active_user)):
        return FILTER_JSON_SCHEMA

    @router.get("", summary="Available filters")
    async def list_filters(current_user=Depends(get_current_active_user)):
        return await controller.list_all(current_user.id)

    @router.get("/{filter_id}/lut", summary="A filter's .cube LUT")
    async def filter_lut(filter_id: str, request: Request, current_user=Depends(get_current_active_user)):
        return controller.lut_response(filter_id, request)

    return router
