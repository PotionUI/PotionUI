"""
Phrasebook Controller

Handles phrasebook categories and values with thin route handlers
delegating to controller methods. Business logic is in
`src.features.phrasebook.operations`.
"""
import dataclasses
import os
from typing import Optional, TYPE_CHECKING
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import PlainTextResponse, FileResponse

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.phrasebook.dto import (
    BatchRequest,
    PhrasebookCategoryRequest,
    PhrasebookFindMode,
    PhrasebookFindScope,
    PhrasebookValueRequest,
    PhrasebookSearchRequest,
    PhrasebookStateFilter,
    ToggleActiveRequest,
    GeneratePreviewRequest,
)
from src.features.phrasebook import PhrasebookPreviewGenerator, operations
from src.features.phrasebook.hooks import PHRASEBOOK_HOOKS
from src.features.phrasebook.import_service import phrasebook_import_service
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.plugins.phrasebook_ops import BatchOperationError, PhrasebookOperationRegistry
from src.platform.security.user import User
from src.features.generation.orchestrator import GenerationOrchestrator

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class PhrasebookController(BaseController):
    """
    Controller for phrasebook operations.

    Handles CRUD operations for categories and values, search functionality,
    and YAML import/export. Uses `src.features.phrasebook.operations` for
    business logic.
    """

    def __init__(
        self,
        category_repository: PhrasebookCategoryRepository,
        value_repository: PhrasebookValueRepository,
        plugin_registry: PluginRegistry,
        preview_generator: PhrasebookPreviewGenerator,
        generation_orchestrator: GenerationOrchestrator,
        operation_registry: PhrasebookOperationRegistry,
    ):
        super().__init__()
        self.categories = category_repository
        self.values = value_repository
        self.plugins = plugin_registry
        self.preview_generator = preview_generator
        self.generation_orchestrator = generation_orchestrator
        self.operations = operation_registry

    # ========== Category Methods ==========

    async def get_categories(
        self,
        user: User,
        root_only: bool = False,
        state_filter: PhrasebookStateFilter = PhrasebookStateFilter.ALL
    ) -> APIResponse:
        """Get phrasebook categories for the current user with optional state filtering."""
        try:
            if root_only:
                categories = self.categories.get_children(None, user.id)
                # Apply state filter manually for root categories
                if state_filter == PhrasebookStateFilter.ACTIVE:
                    categories = [c for c in categories if c.is_active]
                elif state_filter == PhrasebookStateFilter.INACTIVE:
                    categories = [c for c in categories if not c.is_active]
            else:
                categories = self.categories.get_all(user.id, state_filter)
            return self.success_response(
                data={"categories": [cat.model_dump() for cat in categories]}
            )
        except ValueError as e:
            return self.error_api_response(error="get_categories_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="get_categories_failed", message=str(e))

    async def get_category_children(self, category_id: str, user: User) -> APIResponse:
        """Get direct children of a category."""
        try:
            children = self.categories.get_children(category_id, user.id)
            return self.success_response(
                data={"categories": [cat.model_dump() for cat in children]}
            )
        except ValueError as e:
            return self.error_api_response(error="get_children_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="get_children_failed", message=str(e))

    async def get_category(self, category_id: str, user: User) -> APIResponse:
        """Get a specific phrasebook category with its values."""
        try:
            category = operations.get_category(self.categories, category_id, user.id)
            values = self.values.get_by_category(category_id, user.id)
            return self.success_response(
                data={
                    "category": category.model_dump(),
                    "values": [val.model_dump() for val in values]
                }
            )
        except ValueError as e:
            return self.error_api_response(error="get_category_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="get_category_failed", message=str(e))

    async def create_category(
        self, request: PhrasebookCategoryRequest, user: User
    ) -> APIResponse:
        """Create a new phrasebook category."""
        try:
            category = operations.create_category(self.categories, self.plugins, request, user.id)
            return self.success_response(data=category.model_dump())
        except ValueError as e:
            return self.error_api_response(error="create_category_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="create_category_failed", message=str(e))

    async def update_category(
        self, category_id: str, request: PhrasebookCategoryRequest, user: User
    ) -> APIResponse:
        """Update an phrasebook category."""
        try:
            category = operations.update_category(self.categories, self.plugins, category_id, request, user.id)
            return self.success_response(data=category.model_dump())
        except ValueError as e:
            return self.error_api_response(error="update_category_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="update_category_failed", message=str(e))

    async def delete_category(self, category_id: str, user: User) -> APIResponse:
        """Delete an phrasebook category and all its values."""
        try:
            operations.delete_category(self.categories, self.plugins, category_id, user.id)
            return self.success_response(data={"message": "Category deleted successfully"})
        except ValueError as e:
            return self.error_api_response(error="delete_category_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="delete_category_failed", message=str(e))

    async def toggle_category_active(
        self, category_id: str, request: ToggleActiveRequest, user: User
    ) -> APIResponse:
        """Toggle the active state of a category."""
        try:
            category = operations.toggle_category_active(self.categories, category_id, request.is_active, user.id)
            return self.success_response(data=category.model_dump())
        except ValueError as e:
            return self.error_api_response(error="toggle_category_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="toggle_category_failed", message=str(e))

    # ========== Value Methods ==========

    async def create_value(
        self, request: PhrasebookValueRequest, user: User
    ) -> APIResponse:
        """Create a new phrasebook value."""
        try:
            value = operations.create_value(self.values, self.categories, self.plugins, request, user.id)
            return self.success_response(data=value.model_dump())
        except ValueError as e:
            return self.error_api_response(error="create_value_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="create_value_failed", message=str(e))

    async def update_value(
        self, value_id: str, request: PhrasebookValueRequest, user: User
    ) -> APIResponse:
        """Update an phrasebook value."""
        try:
            value = operations.update_value(self.values, self.categories, self.plugins, value_id, request, user.id)
            return self.success_response(data=value.model_dump())
        except ValueError as e:
            return self.error_api_response(error="update_value_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="update_value_failed", message=str(e))

    async def delete_value(self, value_id: str, user: User) -> APIResponse:
        """Delete an phrasebook value."""
        try:
            operations.delete_value(self.values, self.plugins, value_id, user.id)
            return self.success_response(data={"message": "Value deleted successfully"})
        except ValueError as e:
            return self.error_api_response(error="delete_value_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="delete_value_failed", message=str(e))

    async def toggle_value_active(
        self, value_id: str, request: ToggleActiveRequest, user: User
    ) -> APIResponse:
        """Toggle the active state of a value."""
        try:
            value = operations.toggle_value_active(self.values, value_id, request.is_active, user.id)
            return self.success_response(data=value.model_dump())
        except ValueError as e:
            return self.error_api_response(error="toggle_value_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="toggle_value_failed", message=str(e))


    # ========== Search Methods ==========

    async def search(
        self,
        path: str,
        limit: int,
        user: User,
        state_filter: PhrasebookStateFilter = PhrasebookStateFilter.ACTIVE
    ) -> APIResponse:
        """Search for phrasebook suggestions by path prefix with state filtering."""
        try:
            results = operations.search_phrasebook(self.categories, self.values, path, user.id, limit, state_filter)
            return self.success_response(data=results)
        except ValueError as e:
            return self.error_api_response(error="search_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="search_failed", message=str(e))

    async def find(
        self,
        user: User,
        query: str,
        mode: PhrasebookFindMode = PhrasebookFindMode.CONTAINS,
        case_sensitive: bool = False,
        scope: PhrasebookFindScope = PhrasebookFindScope.ALL,
        include_inactive: bool = True,
        path_prefix: str = "",
        fields: Optional[str] = None,
        limit: int = 200,
    ) -> APIResponse:
        """Text search across categories and values with per-field match spans."""
        try:
            field_names = operations.parse_fields(fields)
        except operations.InvalidFields as e:
            self.error_response(error="invalid_fields", message=str(e), status_code=400)
        try:
            results = operations.find_phrasebook(
                self.categories,
                self.values,
                user.id,
                query,
                mode=mode.value,
                case_sensitive=case_sensitive,
                scope=scope.value,
                include_inactive=include_inactive,
                path_prefix=path_prefix,
                fields=field_names,
                limit=limit,
            )
        except operations.InvalidPattern as e:
            self.error_response(error="invalid_pattern", message=str(e), status_code=400)
        except Exception as e:
            return self.error_api_response(error="find_failed", message=str(e))
        hook_data, _ = execute_hook(
            self.plugins,
            PHRASEBOOK_HOOKS.find_results,
            {
                **results,
                "include_inactive": include_inactive,
                "path_prefix": path_prefix,
                "fields": field_names,
                "limit": limit,
                "user_id": user.id,
            },
        )
        results = {
            **results,
            "categories": hook_data.get("categories", results.get("categories", [])),
            "values": hook_data.get("values", results.get("values", [])),
        }
        return self.success_response(data=results)

    def list_batch_ops(self) -> APIResponse:
        """Every registered batch operation, core and plugin, for the selection bar."""
        return self.success_response(data=self.operations.frontend_manifest())

    def _batch_target(self, request: BatchRequest):
        value_ids = list(dict.fromkeys(request.value_ids))
        if not value_ids:
            self.error_response(error="empty_selection", message="No values selected", status_code=400)
        definition = self.operations.get(request.op)
        if definition is None:
            self.error_response(error="unknown_op", message=f"Unknown batch operation: {request.op}", status_code=404)
        return definition, value_ids

    def _batch_context(self, user: User):
        return operations.RepositoryBatchContext(self.values, self.categories, user.id, plugins=self.plugins)

    async def batch_values(self, request: BatchRequest, user: User) -> APIResponse:
        """Run one registered batch operation over the user's selected values,
        between the `phrasebook.batch.before` / `after` hooks."""
        definition, value_ids = self._batch_target(request)
        hook_data, blocked = execute_hook(
            self.plugins,
            PHRASEBOOK_HOOKS.batch_before,
            {"op": request.op, "value_ids": value_ids, "params": dict(request.params), "user_id": user.id},
        )
        if blocked:
            self.error_response(
                error="blocked", message=hook_data.get("block_reason", "Batch operation blocked"), status_code=400
            )
        value_ids = list(hook_data.get("value_ids", value_ids))
        params = dict(hook_data.get("params", request.params))
        try:
            outcome = await definition.backend.run(self._batch_context(user), value_ids, params)
        except BatchOperationError as e:
            self.error_response(error=e.code, message=e.message, status_code=e.status)
        except Exception as e:
            return self.error_api_response(error="batch_failed", message=str(e))
        data = dataclasses.asdict(outcome)
        execute_hook(
            self.plugins,
            PHRASEBOOK_HOOKS.batch_after,
            {"op": request.op, "value_ids": value_ids, "params": params, "user_id": user.id, "outcome": data},
        )
        return self.success_response(data=data)

    async def preview_batch(self, request: BatchRequest, user: User) -> APIResponse:
        """Dry-run a registered batch operation; no hooks, no writes."""
        definition, value_ids = self._batch_target(request)
        try:
            preview = await definition.backend.preview(self._batch_context(user), value_ids, dict(request.params))
        except BatchOperationError as e:
            self.error_response(error=e.code, message=e.message, status_code=e.status)
        except Exception as e:
            return self.error_api_response(error="batch_failed", message=str(e))
        return self.success_response(data=dataclasses.asdict(preview))

    # ========== Import/Export Methods ==========

    async def import_yaml(
        self, file: UploadFile, root_category: Optional[str], user: User
    ) -> APIResponse:
        """Import a YAML file to create phrasebook categories and values."""
        try:
            # Validate file type
            if not file.filename.endswith(('.yaml', '.yml')):
                return self.error_api_response(
                    error="invalid_file_type",
                    message="File must be a YAML file (.yaml or .yml)"
                )

            # Read file content
            content = await file.read()
            yaml_content = content.decode('utf-8')

            # Import using the service
            result = phrasebook_import_service.import_yaml(
                yaml_content, user.id, root_category
            )

            if not result['success']:
                return self.error_api_response(
                    error="import_failed",
                    message=result.get('error', 'Import failed')
                )

            return self.success_response(data=result)
        except Exception as e:
            return self.error_api_response(error="import_failed", message=str(e))

    async def export_category(self, category_id: str, user: User) -> PlainTextResponse:
        """Export a category and its values as YAML."""
        yaml_content = phrasebook_import_service.export_to_yaml(category_id, user.id)

        if yaml_content is None:
            # Return error as plain text since we can't mix response types easily
            return PlainTextResponse(
                content="Error: Category not found",
                status_code=404,
                media_type="text/plain"
            )

        return PlainTextResponse(content=yaml_content, media_type="application/x-yaml")

    # ========== Preview Generation Methods ==========

    async def generate_previews(
        self, category_id: str, request: GeneratePreviewRequest, user: User
    ) -> APIResponse:
        """Generate preview images for values in a category."""
        try:
            result = await self.preview_generator.generate_previews(
                category_id=category_id,
                session_id=request.session_id,
                prompt_template=request.prompt_template,
                mode=request.mode,
                user_id=user.id,
                generation_orchestrator=self.generation_orchestrator,
                value_ids=request.value_ids,
                negative_prompt=request.negative_prompt,
                seed=request.seed
            )
            return self.success_response(data=result)
        except ValueError as e:
            return self.error_api_response(error="generate_previews_failed", message=str(e))
        except Exception as e:
            return self.error_api_response(error="generate_previews_failed", message=str(e))


# ========== Route Handlers ==========

def build_router(container: "AppContainer") -> APIRouter:
    controller = container.phrasebook_controller
    router = APIRouter(prefix="/api/phrasebook", tags=["Phrasebook"])

    # Category Routes

    @router.get("/categories", response_model=APIResponse, summary="Get Phrasebook Categories")
    async def get_categories(
        root_only: bool = False,
        state: PhrasebookStateFilter = PhrasebookStateFilter.ALL,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Get phrasebook categories for the current user with optional state filtering."""
        return await controller.get_categories(current_user, root_only, state)

    @router.get(
        "/categories/{category_id}/children",
        response_model=APIResponse,
        summary="Get Category Children"
    )
    async def get_category_children(
        category_id: str, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Get direct children of a category."""
        return await controller.get_category_children(category_id, current_user)

    @router.get(
        "/categories/{category_id}",
        response_model=APIResponse,
        summary="Get Phrasebook Category"
    )
    async def get_category(
        category_id: str, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Get a specific phrasebook category with its values."""
        return await controller.get_category(category_id, current_user)

    @router.post("/categories", response_model=APIResponse, summary="Create Phrasebook Category")
    async def create_category(
        request: PhrasebookCategoryRequest, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Create a new phrasebook category."""
        return await controller.create_category(request, current_user)

    @router.put(
        "/categories/{category_id}",
        response_model=APIResponse,
        summary="Update Phrasebook Category"
    )
    async def update_category(
        category_id: str,
        request: PhrasebookCategoryRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Update an phrasebook category."""
        return await controller.update_category(category_id, request, current_user)

    @router.delete(
        "/categories/{category_id}",
        response_model=APIResponse,
        summary="Delete Phrasebook Category"
    )
    async def delete_category(
        category_id: str, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Delete an phrasebook category and all its values."""
        return await controller.delete_category(category_id, current_user)

    @router.patch(
        "/categories/{category_id}/active",
        response_model=APIResponse,
        summary="Toggle Category Active State"
    )
    async def toggle_category_active(
        category_id: str,
        request: ToggleActiveRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Toggle the active state of an phrasebook category."""
        return await controller.toggle_category_active(category_id, request, current_user)

    @router.post(
        "/categories/{category_id}/generate-previews",
        response_model=APIResponse,
        summary="Generate Preview Images"
    )
    async def generate_previews(
        category_id: str,
        request: GeneratePreviewRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Generate preview images for values in a category."""
        return await controller.generate_previews(category_id, request, current_user)

    # Value Routes

    @router.post("/values", response_model=APIResponse, summary="Create Phrasebook Value")
    async def create_value(
        request: PhrasebookValueRequest, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Create a new phrasebook value."""
        return await controller.create_value(request, current_user)

    @router.put("/values/{value_id}", response_model=APIResponse, summary="Update Phrasebook Value")
    async def update_value(
        value_id: str,
        request: PhrasebookValueRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Update an phrasebook value."""
        return await controller.update_value(value_id, request, current_user)

    @router.delete(
        "/values/{value_id}",
        response_model=APIResponse,
        summary="Delete Phrasebook Value"
    )
    async def delete_value(
        value_id: str, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Delete an phrasebook value."""
        return await controller.delete_value(value_id, current_user)

    @router.patch(
        "/values/{value_id}/active",
        response_model=APIResponse,
        summary="Toggle Value Active State"
    )
    async def toggle_value_active(
        value_id: str,
        request: ToggleActiveRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Toggle the active state of an phrasebook value."""
        return await controller.toggle_value_active(value_id, request, current_user)

    @router.get("/batch-ops", response_model=APIResponse, summary="List Batch Operations")
    async def list_batch_ops(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        """Registered batch operations (core and plugin) for the Find & replace selection bar."""
        return controller.list_batch_ops()

    @router.post("/values/batch", response_model=APIResponse, summary="Run Batch Operation")
    async def batch_values(
        request: BatchRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Run the registered operation `op` over `value_ids` with `params`,
        all-or-nothing, between the phrasebook.batch.before/after hooks."""
        return await controller.batch_values(request, current_user)

    @router.post("/values/batch/preview", response_model=APIResponse, summary="Preview Batch Operation")
    async def preview_batch(
        request: BatchRequest,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Dry-run `op` over `value_ids`; 400 `no_preview` when the operation has none."""
        return await controller.preview_batch(request, current_user)

    # Search Routes

    @router.post("/search", response_model=APIResponse, summary="Search Phrasebook")
    async def search_phrasebook(
        request: PhrasebookSearchRequest,
        state: PhrasebookStateFilter = PhrasebookStateFilter.ACTIVE,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Search for phrasebook suggestions by path prefix with state filtering.

        Default state is ACTIVE to only return active values for the chip editor.
        """
        return await controller.search(request.path, request.limit, current_user, state)

    @router.get("/search", response_model=APIResponse, summary="Search Phrasebook (GET)")
    async def search_phrasebook_get(
        path: str,
        limit: int = 50,
        state: PhrasebookStateFilter = PhrasebookStateFilter.ACTIVE,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Search for phrasebook suggestions by path prefix (GET method) with state filtering.

        Default state is ACTIVE to only return active values for the chip editor.
        """
        return await controller.search(path, limit, current_user, state)

    @router.get("/find", response_model=APIResponse, summary="Find Phrasebook Text")
    async def find_phrasebook(
        q: str = "",
        mode: PhrasebookFindMode = PhrasebookFindMode.CONTAINS,
        case_sensitive: bool = False,
        scope: PhrasebookFindScope = PhrasebookFindScope.ALL,
        include_inactive: bool = True,
        path_prefix: str = "",
        fields: Optional[str] = None,
        limit: int = 200,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Text search across categories and values: contains / whole word /
        regex, optional case sensitivity, scope, subtree and field limits.
        Every hit carries `matches` spans per field."""
        return await controller.find(
            current_user, q,
            mode=mode, case_sensitive=case_sensitive, scope=scope,
            include_inactive=include_inactive, path_prefix=path_prefix,
            fields=fields, limit=limit,
        )

    # Import/Export Routes

    @router.post("/import", response_model=APIResponse, summary="Import YAML File")
    async def import_yaml_file(
        file: UploadFile = File(...),
        root_category: Optional[str] = None,
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Import a YAML file to create phrasebook categories and values."""
        return await controller.import_yaml(file, root_category, current_user)

    @router.get("/export/{category_id}", summary="Export Category as YAML")
    async def export_category(
        category_id: str, current_user: User = Depends(get_current_active_user)
    ) -> PlainTextResponse:
        """Export a category and its values as YAML."""
        return await controller.export_category(category_id, current_user)

    return router
