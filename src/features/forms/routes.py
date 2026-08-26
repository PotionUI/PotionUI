"""
Form Controller.

Thin controller for form-related API endpoints.
Delegates all business logic to `src.features.forms.operations`.
"""
from typing import Dict, Any, Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.forms.dto import FormFieldOptionsRequest, FormValidationRequest
from src.features.forms import operations

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer
    from src.platform.security.user import User


class FormController(BaseController):
    """Controller for form operations."""

    def __init__(self, field_registry, plugin_registry):
        super().__init__()
        self.field_registry = field_registry
        self.plugin_registry = plugin_registry

    async def get_field_options(
        self, field_type: str, field_config: Dict[str, Any], current_user: Optional["User"] = None,
    ) -> APIResponse:
        """Get options for a specific field based on its configuration.

        `current_user` scopes a `model`/`models` field's options to the
        caller's model access - see
        `src.features.forms.operations.get_field_options`. Optional only so
        this controller can still be constructed/called without a request
        context (tests); the router always supplies the authenticated user.
        """
        try:
            options = operations.get_field_options(
                self.field_registry, self.plugin_registry, field_type, field_config, current_user
            )
            return self.success_response(data=options)
        except ValueError as e:
            return self.error_response(
                error="field_options_failed",
                message=str(e)
            )
        except Exception as e:
            self.logger.error(f"Failed to get field options: {e}")
            return self.error_response(
                error="field_options_failed",
                message=f"Failed to get field options: {str(e)}"
            )

    async def validate_form_data(self, form_schema: Dict[str, Any], form_data: Dict[str, Any]) -> APIResponse:
        """Validate form data against schema."""
        try:
            result = operations.validate_form_data(self.plugin_registry, form_schema, form_data)
            return self.success_response(message="Form data is valid", data=result)
        except ValueError as e:
            return self.error_response(
                error="validation_error",
                message=str(e),
                status_code=422
            )
        except Exception as e:
            self.logger.error(f"Failed to validate form data: {e}")
            return self.error_response(
                error="validation_error",
                message=f"Failed to validate form data: {str(e)}"
            )

    async def get_form_defaults(self, preset_id: str) -> APIResponse:
        """Get default values for a preset form."""
        try:
            defaults = operations.get_form_defaults(preset_id)
            return self.success_response(data=defaults)
        except ValueError as e:
            return self.error_response(
                error="defaults_failed",
                message=str(e)
            )
        except Exception as e:
            self.logger.error(f"Failed to get form defaults: {e}")
            return self.error_response(
                error="defaults_failed",
                message=f"Failed to get form defaults: {str(e)}"
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.form_controller

    router = APIRouter(prefix="/api/form", tags=["Forms"])

    @router.post("/options", response_model=APIResponse, summary="Get Field Options")
    async def get_field_options(request: FormFieldOptionsRequest, current_user=Depends(get_current_active_user)):
        """Get available options for a specific field type based on its configuration."""
        return await controller.get_field_options(request.field_type, request.field_config, current_user)

    @router.post("/validate", response_model=APIResponse, summary="Validate Form Data")
    async def validate_form_data(request: FormValidationRequest, current_user=Depends(get_current_active_user)):
        """Validate submitted form data against the schema and return validation results."""
        return await controller.validate_form_data(request.form_schema, request.form_data)

    @router.get("/defaults/{preset_id}", response_model=APIResponse, summary="Get Default Values")
    async def get_form_defaults(preset_id: str, current_user=Depends(get_current_active_user)):
        """Get the default form values for a specific preset configuration."""
        return await controller.get_form_defaults(preset_id)

    return router
