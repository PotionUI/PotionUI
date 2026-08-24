"""
Field Controller.

Thin controller exposing the field-type registry - `src/platform/plugins/field_types.py`
- as a manifest the frontend can consume to build its own field component
registry (A4), instead of the hardcoded `FormField.svelte` branch table.
"""
from typing import Any, Dict, List, TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.platform.plugins.field_types import FieldTypeRegistry

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class FieldController(BaseController):
    """Controller for field-type registry operations."""

    def __init__(self, field_registry: FieldTypeRegistry):
        super().__init__()
        self.field_registry = field_registry

    async def get_field_types(self) -> APIResponse:
        """Return the frontend manifest of every registered field type."""
        try:
            manifest: List[Dict[str, Any]] = self.field_registry.frontend_manifest()
            return self.success_response(data=manifest)
        except Exception as e:
            self.logger.error(f"Failed to get field types: {e}")
            return self.error_response(
                error="field_types_failed",
                message=f"Failed to get field types: {str(e)}"
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.field_controller

    router = APIRouter(prefix="/api/fields", tags=["Fields"])

    @router.get("/types", response_model=APIResponse, summary="Get Field Types")
    async def get_field_types(current_user=Depends(get_current_active_user)):
        """Get the frontend manifest of every registered form field type."""
        return await controller.get_field_types()

    return router
