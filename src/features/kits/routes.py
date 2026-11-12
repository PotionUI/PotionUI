import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from src.features.kits import operations
from src.features.kits.catalog import KitCatalog
from src.features.kits.dto import (
    AddItemRequest,
    AddLibraryItemsRequest,
    CreateKitRequest,
    OrderRequest,
    ResolveRequest,
    UpdateItemRequest,
    UpdateKitRequest,
)
from src.features.kits.expansion import KitExpander
from src.features.kits.mappers import kit_with_items, summary_to_dict
from src.features.kits.repository import KitRepository
from src.features.kits.resolve import resolve_citations
from src.features.media.validators import UPLOAD_PURPOSE_USER
from src.features.prompt.resources import mode_prompt_resources
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.http.error_pipeline import Fail, guarded
from src.platform.http.errors import InvalidInputError, NotFoundError
from src.platform.security.api_scopes import ApiScope, key_access, key_scope
from src.platform.security.current_user import get_current_active_user

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)

PresetLookup = Callable[[str], Any]


class KitController(BaseController):

    def __init__(
        self,
        repository: KitRepository,
        expander: KitExpander,
        preset_lookup: Optional[PresetLookup] = None,
        media_store: Any = None,
    ):
        super().__init__()
        self.repository = repository
        self.expander = expander
        self.preset_lookup = preset_lookup
        self.media_store = media_store

    def _view(self, owner_id: str, kit_id: str) -> APIResponse:
        view = KitCatalog(self.repository, owner_id).by_id(kit_id)
        if view is None:
            raise NotFoundError("kit_not_found", "Kit not found")
        return self.success_response(data={"kit": kit_with_items(view)})

    @guarded(fallback=Fail("list_kits_failed", "Failed to list kits", 400))
    async def list_kits(
        self, user, label: Optional[str] = None, search: Optional[str] = None, limit: int = 50, offset: int = 0,
    ) -> APIResponse:
        kits, total = await asyncio.to_thread(
            self.repository.list_kits, user.id, label, search, max(1, min(limit, 200)), max(0, offset)
        )
        ids = [kit.id for kit in kits]
        counts = await asyncio.to_thread(self.repository.counts_for, ids)
        thumbnails = await asyncio.to_thread(self.repository.thumbnails_for, ids)
        labels = await asyncio.to_thread(self.repository.labels, user.id)
        return self.success_response(data={
            "kits": [summary_to_dict(kit, counts[kit.id], thumbnails[kit.id]) for kit in kits],
            "total": total,
            "labels": labels,
        })

    @guarded(fallback=Fail("get_kit_failed", "Failed to get kit", 400))
    async def get_kit(self, kit_id: str, user) -> APIResponse:
        return await asyncio.to_thread(self._view, user.id, kit_id)

    @guarded(fallback=Fail("create_kit_failed", "Failed to create kit", 400))
    async def create_kit(self, request: CreateKitRequest, user) -> APIResponse:
        kit = await asyncio.to_thread(
            operations.create_kit, self.repository, user.id, request.name, request.handle, request.label
        )
        return await asyncio.to_thread(self._view, user.id, kit.id)

    @guarded(fallback=Fail("update_kit_failed", "Failed to update kit", 400))
    async def update_kit(self, kit_id: str, request: UpdateKitRequest, user) -> APIResponse:
        label = request.label if "label" in request.model_fields_set else operations.UNSET
        await asyncio.to_thread(
            operations.update_kit, self.repository, user.id, kit_id, request.name, request.handle, label
        )
        return await asyncio.to_thread(self._view, user.id, kit_id)

    @guarded(fallback=Fail("duplicate_kit_failed", "Failed to duplicate kit", 400))
    async def duplicate_kit(self, kit_id: str, user) -> APIResponse:
        copy = await asyncio.to_thread(operations.duplicate_kit, self.repository, user.id, kit_id)
        return await asyncio.to_thread(self._view, user.id, copy.id)

    @guarded(fallback=Fail("delete_kit_failed", "Failed to delete kit", 400))
    async def delete_kit(self, kit_id: str, user) -> APIResponse:
        await asyncio.to_thread(operations.delete_kit, self.repository, user.id, kit_id)
        return self.success_response(data={"id": kit_id, "deleted": True})

    @guarded(fallback=Fail("reorder_kits_failed", "Failed to reorder kits", 400))
    async def reorder_kits(self, request: OrderRequest, user) -> APIResponse:
        await asyncio.to_thread(operations.reorder_kits, self.repository, user.id, request.ids)
        return self.success_response(data={"ids": request.ids})

    @guarded(fallback=Fail("add_item_failed", "Failed to add the item", 400))
    async def add_item(self, kit_id: str, request: AddItemRequest, user) -> APIResponse:
        if (request.upload_id is None) == (request.text is None):
            raise InvalidInputError("item_source_required", "Give either a Library item or a text")
        if request.upload_id is not None:
            await asyncio.to_thread(
                operations.add_media_item, self.repository, user.id, kit_id, request.upload_id, request.name
            )
        else:
            await asyncio.to_thread(
                operations.add_text_item, self.repository, user.id, kit_id, request.text, request.name
            )
        return await asyncio.to_thread(self._view, user.id, kit_id)

    @guarded(fallback=Fail("upload_item_failed", "Failed to upload the file", 400))
    async def upload_item(self, kit_id: str, file: UploadFile, name: Optional[str], user) -> APIResponse:
        if self.media_store is None:
            raise InvalidInputError("upload_unavailable", "Uploads are not available here")
        await asyncio.to_thread(operations.get_kit, self.repository, user.id, kit_id)
        await asyncio.to_thread(
            self.media_store.check_upload_size,
            user.id, getattr(file, "size", None), UPLOAD_PURPOSE_USER, file.content_type or "",
        )
        stored = await self.media_store.upload_media(
            file_data=await file.read(), filename=file.filename, content_type=file.content_type,
            user_id=user.id, purpose=UPLOAD_PURPOSE_USER,
        )
        if not stored.id:
            raise InvalidInputError("upload_failed", "The file could not be stored")
        await asyncio.to_thread(operations.add_media_item, self.repository, user.id, kit_id, stored.id, name)
        return await asyncio.to_thread(self._view, user.id, kit_id)

    @guarded(fallback=Fail("add_library_items_failed", "Failed to add the Library items", 400))
    async def add_library_items(self, request: AddLibraryItemsRequest, user) -> APIResponse:
        kit, added = await asyncio.to_thread(
            operations.add_library_items,
            self.repository, user.id, request.upload_ids, request.kit_id,
            request.new_kit.name if request.new_kit else None,
            request.new_kit.label if request.new_kit else None,
            request.names,
        )
        response = await asyncio.to_thread(self._view, user.id, kit.id)
        response.data["added"] = [item.id for item in added]
        return response

    @guarded(fallback=Fail("update_item_failed", "Failed to update the item", 400))
    async def update_item(self, kit_id: str, item_id: str, request: UpdateItemRequest, user) -> APIResponse:
        await asyncio.to_thread(
            operations.update_item, self.repository, user.id, kit_id, item_id, request.name, request.text
        )
        return await asyncio.to_thread(self._view, user.id, kit_id)

    @guarded(fallback=Fail("remove_item_failed", "Failed to remove the item", 400))
    async def remove_item(self, kit_id: str, item_id: str, user) -> APIResponse:
        await asyncio.to_thread(operations.remove_item, self.repository, user.id, kit_id, item_id)
        return await asyncio.to_thread(self._view, user.id, kit_id)

    @guarded(fallback=Fail("reorder_items_failed", "Failed to reorder the items", 400))
    async def reorder_items(self, kit_id: str, request: OrderRequest, user) -> APIResponse:
        await asyncio.to_thread(operations.reorder_items, self.repository, user.id, kit_id, request.ids)
        return await asyncio.to_thread(self._view, user.id, kit_id)

    @guarded(fallback=Fail("kit_citations_failed", "Failed to read where the kit is cited", 400))
    async def citations(self, kit_id: str, user) -> APIResponse:
        await asyncio.to_thread(operations.get_kit, self.repository, user.id, kit_id)
        total, films, tabs = await asyncio.to_thread(self.repository.cited_in_tabs, user.id, kit_id)
        return self.success_response(data={"total": total, "sessions": total - films, "films": films, "tabs": tabs})

    @guarded(fallback=Fail("kit_usage_failed", "Failed to read where the files are used", 400))
    async def usage(self, upload_ids: str, user) -> APIResponse:
        ids = [value for value in upload_ids.split(",") if value][:200]
        usage = await asyncio.to_thread(self.repository.usage_for_uploads, user.id, ids)
        return self.success_response(data={
            "usage": {upload_id: {"count": len({row["kit_id"] for row in rows}), "items": rows} for upload_id, rows in usage.items()},
        })

    @guarded(fallback=Fail("resolve_kits_failed", "Failed to resolve the kit citations", 400))
    async def resolve(self, request: ResolveRequest, user) -> APIResponse:
        specs: Optional[List[Dict[str, Any]]] = None
        if request.preset_id:
            template = self.preset_lookup(request.preset_id) if self.preset_lookup is not None else None
            if template is None:
                raise NotFoundError("preset_not_found", "The selected preset is not available")
            specs = mode_prompt_resources(template, request.mode)
        result = await asyncio.to_thread(
            resolve_citations, self.expander, user.id, specs, request.field, request.text, request.cites,
            request.loaded, specs, request.form_data,
        )
        return self.success_response(data=result)


def build_router(container: "AppContainer") -> APIRouter:
    controller: KitController = container.kit_controller
    router = APIRouter(prefix="/api/kits", tags=["Kits"], dependencies=[key_access(read=ApiScope.READ, write=ApiScope.CONTENT)])

    @router.get("", response_model=APIResponse, summary="List Kits")
    async def list_kits(
        label: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.list_kits(current_user, label, search, limit, offset)

    @router.post("", response_model=APIResponse, summary="Create Kit")
    async def create_kit(request: CreateKitRequest, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.create_kit(request, current_user)

    @router.put("/order", response_model=APIResponse, summary="Reorder Kits")
    async def reorder_kits(request: OrderRequest, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.reorder_kits(request, current_user)

    @router.post("/add-library-items", response_model=APIResponse, summary="Add Library Items To A Kit")
    async def add_library_items(request: AddLibraryItemsRequest, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.add_library_items(request, current_user)

    @router.get("/usage", response_model=APIResponse, summary="Kits Using Library Items")
    async def usage(upload_ids: str = Query(...), current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.usage(upload_ids, current_user)

    @router.post("/resolve", response_model=APIResponse, summary="Resolve Kit Citations", dependencies=[key_scope(ApiScope.READ)])
    async def resolve(request: ResolveRequest, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.resolve(request, current_user)

    @router.get("/{kit_id}/usage", response_model=APIResponse, summary="Where A Kit Is Cited")
    async def kit_citations(kit_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.citations(kit_id, current_user)

    @router.get("/{kit_id}", response_model=APIResponse, summary="Get Kit")
    async def get_kit(kit_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.get_kit(kit_id, current_user)

    @router.patch("/{kit_id}", response_model=APIResponse, summary="Update Kit")
    async def update_kit(kit_id: str, request: UpdateKitRequest, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.update_kit(kit_id, request, current_user)

    @router.delete("/{kit_id}", response_model=APIResponse, summary="Delete Kit")
    async def delete_kit(kit_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.delete_kit(kit_id, current_user)

    @router.post("/{kit_id}/duplicate", response_model=APIResponse, summary="Duplicate Kit")
    async def duplicate_kit(kit_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.duplicate_kit(kit_id, current_user)

    @router.post("/{kit_id}/items", response_model=APIResponse, summary="Add Kit Item")
    async def add_item(kit_id: str, request: AddItemRequest, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.add_item(kit_id, request, current_user)

    @router.post("/{kit_id}/items/upload", response_model=APIResponse, summary="Upload A File Into A Kit")
    async def upload_item(
        kit_id: str,
        file: UploadFile = File(...),
        name: Optional[str] = Form(None),
        current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.upload_item(kit_id, file, name, current_user)

    @router.put("/{kit_id}/order", response_model=APIResponse, summary="Reorder Kit Items")
    async def reorder_items(kit_id: str, request: OrderRequest, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.reorder_items(kit_id, request, current_user)

    @router.patch("/{kit_id}/items/{item_id}", response_model=APIResponse, summary="Update Kit Item")
    async def update_item(
        kit_id: str, item_id: str, request: UpdateItemRequest, current_user=Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.update_item(kit_id, item_id, request, current_user)

    @router.delete("/{kit_id}/items/{item_id}", response_model=APIResponse, summary="Remove Kit Item")
    async def remove_item(kit_id: str, item_id: str, current_user=Depends(get_current_active_user)) -> APIResponse:
        return await controller.remove_item(kit_id, item_id, current_user)

    return router
