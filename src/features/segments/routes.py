"""Clean APIs for saved Segments, Segment Templates, and Segment Categories."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.platform.security.current_user import get_current_active_user
from src.features.segments.dto import (
    SavedSegmentRequest,
    SegmentCategoryRequest,
    SegmentTemplateRequest,
)
from src.features.segments import operations
from src.features.segments.repository import (
    SavedSegmentRepository,
    SegmentCategoryRepository,
    SegmentTemplateRepository,
)
from src.platform.plugins import PluginRegistry

from src.platform.http.base_controller import APIResponse, BaseController

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class SegmentController(BaseController):
    def __init__(
        self,
        category_repository: SegmentCategoryRepository,
        segment_repository: SavedSegmentRepository,
        template_repository: SegmentTemplateRepository,
        plugin_registry: PluginRegistry,
    ):
        super().__init__()
        self.categories = category_repository
        self.segments = segment_repository
        self.templates = template_repository
        self.plugins = plugin_registry

    # Categories ---------------------------------------------------------

    def get_categories(self, user_id: str) -> APIResponse:
        try:
            categories = self.categories.get_all(user_id)
            return self.success_response(
                data={"categories": [item.model_dump() for item in categories]}
            )
        except ValueError as exc:
            return self.error_api_response("get_categories_failed", str(exc))

    def create_category(
        self, request: SegmentCategoryRequest, user_id: str
    ) -> APIResponse:
        try:
            return self.success_response(
                data=operations.create_category(self.categories, self.plugins, request, user_id).model_dump()
            )
        except ValueError as exc:
            return self.error_api_response("create_category_failed", str(exc))

    def update_category(
        self,
        category_id: str,
        request: SegmentCategoryRequest,
        user_id: str,
    ) -> APIResponse:
        try:
            return self.success_response(
                data=operations.update_category(
                    self.categories, self.plugins, category_id, request, user_id
                ).model_dump()
            )
        except ValueError as exc:
            return self.error_api_response("update_category_failed", str(exc))

    def delete_category(self, category_id: str, user_id: str) -> APIResponse:
        try:
            operations.delete_category(self.categories, self.plugins, category_id, user_id)
            return self.success_response(message="Segment Category deleted")
        except ValueError as exc:
            return self.error_api_response("delete_category_failed", str(exc))

    # Saved Segments -----------------------------------------------------

    def get_segments(
        self, user_id: str, category_id: Optional[str] = None
    ) -> APIResponse:
        try:
            if category_id:
                operations.get_category(self.categories, category_id, user_id)
            segments = self.segments.get_all(user_id, category_id)
            return self.success_response(
                data={"segments": [item.model_dump() for item in segments]}
            )
        except ValueError as exc:
            return self.error_api_response("get_segments_failed", str(exc))

    def get_segment_by_id(self, segment_id: str, user_id: str) -> APIResponse:
        try:
            return self.success_response(
                data=operations.get_segment(self.segments, segment_id, user_id).model_dump()
            )
        except ValueError as exc:
            return self.error_api_response("get_segment_failed", str(exc))

    def create_segment(
        self, request: SavedSegmentRequest, user_id: str
    ) -> APIResponse:
        try:
            return self.success_response(
                data=operations.create_segment(
                    self.segments, self.categories, self.plugins, request, user_id
                ).model_dump()
            )
        except ValueError as exc:
            return self.error_api_response("create_segment_failed", str(exc))

    def update_segment(
        self,
        segment_id: str,
        request: SavedSegmentRequest,
        user_id: str,
    ) -> APIResponse:
        try:
            return self.success_response(
                data=operations.update_segment(
                    self.segments, self.categories, self.plugins, segment_id, request, user_id
                ).model_dump()
            )
        except ValueError as exc:
            return self.error_api_response("update_segment_failed", str(exc))

    def delete_segment(self, segment_id: str, user_id: str) -> APIResponse:
        try:
            operations.delete_segment(self.segments, self.plugins, segment_id, user_id)
            return self.success_response(message="Saved Segment deleted")
        except ValueError as exc:
            return self.error_api_response("delete_segment_failed", str(exc))

    # Segment Templates --------------------------------------------------

    def get_templates(self, user_id: str) -> APIResponse:
        try:
            templates = self.templates.get_all(user_id)
            return self.success_response(
                data={"templates": [item.model_dump() for item in templates]}
            )
        except ValueError as exc:
            return self.error_api_response("get_templates_failed", str(exc))

    def get_template_by_id(self, template_id: str, user_id: str) -> APIResponse:
        try:
            return self.success_response(
                data=operations.get_template(self.templates, template_id, user_id).model_dump()
            )
        except ValueError as exc:
            return self.error_api_response("get_template_failed", str(exc))

    def create_template(
        self, request: SegmentTemplateRequest, user_id: str
    ) -> APIResponse:
        try:
            return self.success_response(
                data=operations.create_template(self.templates, self.plugins, request, user_id).model_dump()
            )
        except ValueError as exc:
            return self.error_api_response("create_template_failed", str(exc))

    def update_template(
        self,
        template_id: str,
        request: SegmentTemplateRequest,
        user_id: str,
    ) -> APIResponse:
        try:
            return self.success_response(
                data=operations.update_template(
                    self.templates, self.plugins, template_id, request, user_id
                ).model_dump()
            )
        except ValueError as exc:
            return self.error_api_response("update_template_failed", str(exc))

    def delete_template(self, template_id: str, user_id: str) -> APIResponse:
        try:
            operations.delete_template(self.templates, self.plugins, template_id, user_id)
            return self.success_response(message="Segment Template deleted")
        except ValueError as exc:
            return self.error_api_response("delete_template_failed", str(exc))


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.segment_controller

    saved_segments_router = APIRouter(prefix="/api/segments", tags=["Segments"])
    segment_templates_router = APIRouter(
        prefix="/api/segment-templates", tags=["Segment Templates"]
    )
    segment_categories_router = APIRouter(
        prefix="/api/segment-categories", tags=["Segment Categories"]
    )

    # Segment Categories -----------------------------------------------------

    @segment_categories_router.get("", response_model=APIResponse, summary="List segment categories")
    async def get_segment_categories(current_user=Depends(get_current_active_user)):
        return controller.get_categories(current_user.id)

    @segment_categories_router.post("", response_model=APIResponse, summary="Create a segment category")
    async def create_segment_category(
        request: SegmentCategoryRequest,
        current_user=Depends(get_current_active_user),
    ):
        return controller.create_category(request, current_user.id)

    @segment_categories_router.put("/{category_id}", response_model=APIResponse, summary="Update a segment category")
    async def update_segment_category(
        category_id: str,
        request: SegmentCategoryRequest,
        current_user=Depends(get_current_active_user),
    ):
        return controller.update_category(category_id, request, current_user.id)

    @segment_categories_router.delete("/{category_id}", response_model=APIResponse, summary="Delete a segment category")
    async def delete_segment_category(
        category_id: str,
        current_user=Depends(get_current_active_user),
    ):
        return controller.delete_category(category_id, current_user.id)

    # Saved Segments ---------------------------------------------------------------

    @saved_segments_router.get("", response_model=APIResponse, summary="List saved segments")
    async def get_saved_segments(
        category_id: Optional[str] = None,
        current_user=Depends(get_current_active_user),
    ):
        return controller.get_segments(current_user.id, category_id)

    @saved_segments_router.get("/{segment_id}", response_model=APIResponse, summary="Get a saved segment")
    async def get_saved_segment(
        segment_id: str,
        current_user=Depends(get_current_active_user),
    ):
        return controller.get_segment_by_id(segment_id, current_user.id)

    @saved_segments_router.post("", response_model=APIResponse, summary="Create a saved segment")
    async def create_saved_segment(
        request: SavedSegmentRequest,
        current_user=Depends(get_current_active_user),
    ):
        return controller.create_segment(request, current_user.id)

    @saved_segments_router.put("/{segment_id}", response_model=APIResponse, summary="Update a saved segment")
    async def update_saved_segment(
        segment_id: str,
        request: SavedSegmentRequest,
        current_user=Depends(get_current_active_user),
    ):
        return controller.update_segment(segment_id, request, current_user.id)

    @saved_segments_router.delete("/{segment_id}", response_model=APIResponse, summary="Delete a saved segment")
    async def delete_saved_segment(
        segment_id: str,
        current_user=Depends(get_current_active_user),
    ):
        return controller.delete_segment(segment_id, current_user.id)

    # Segment Templates ------------------------------------------------------------

    @segment_templates_router.get("", response_model=APIResponse, summary="List segment templates")
    async def get_segment_templates(current_user=Depends(get_current_active_user)):
        return controller.get_templates(current_user.id)

    @segment_templates_router.get("/{template_id}", response_model=APIResponse, summary="Get a segment template")
    async def get_segment_template(
        template_id: str,
        current_user=Depends(get_current_active_user),
    ):
        return controller.get_template_by_id(template_id, current_user.id)

    @segment_templates_router.post("", response_model=APIResponse, summary="Create a segment template")
    async def create_segment_template(
        request: SegmentTemplateRequest,
        current_user=Depends(get_current_active_user),
    ):
        return controller.create_template(request, current_user.id)

    @segment_templates_router.put("/{template_id}", response_model=APIResponse, summary="Update a segment template")
    async def update_segment_template(
        template_id: str,
        request: SegmentTemplateRequest,
        current_user=Depends(get_current_active_user),
    ):
        return controller.update_template(template_id, request, current_user.id)

    @segment_templates_router.delete("/{template_id}", response_model=APIResponse, summary="Delete a segment template")
    async def delete_segment_template(
        template_id: str,
        current_user=Depends(get_current_active_user),
    ):
        return controller.delete_template(template_id, current_user.id)

    # Keep api.py's existing single include point while exposing three clean prefixes.
    router = APIRouter()
    router.include_router(saved_segments_router)
    router.include_router(segment_templates_router)
    router.include_router(segment_categories_router)
    return router
