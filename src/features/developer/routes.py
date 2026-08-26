"""
Developer Controller

Provides the template-functions documentation endpoint (rendered by the
Documentation admin tab's "Template Functions" live reference) and the
preset-lint endpoint used to validate presets.
"""
from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.developer import operations
from src.features.developer.template_functions_documenter import TemplateFunctionsDocumenter
from src.platform.security.user import User, AccountType

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class DeveloperController(BaseController):
    """
    Controller for developer documentation endpoints.

    Handles the template-functions documentation and preset-lint endpoints.
    Pipes/fields/IO-types documentation now lives behind
    `/api/docs/live/pipes` (docs_controller.py, reading `PipesDocumenter`
    directly) and `/api/fields/types` (reading `FieldTypeRegistry` directly) -
    this controller no longer exposes them as standalone HTTP routes.
    """

    def __init__(self, template_functions_documenter: TemplateFunctionsDocumenter, preset_loader):
        super().__init__()
        self.template_functions_documenter = template_functions_documenter
        self.preset_loader = preset_loader

    async def get_template_functions_documentation(self) -> APIResponse:
        """Get documentation for all template functions available in pipeline.yml files."""
        try:
            data = self.template_functions_documenter.generate_documentation()
            return self.success_response(data=data)
        except ValueError as e:
            return self.error_api_response(
                error="template_functions_failed",
                message=str(e)
            )
        except Exception as e:
            self.logger.exception(f"Failed to get template functions documentation: {e}")
            return self.error_api_response(
                error="template_functions_failed",
                message="Failed to get template functions documentation"
            )

    async def get_presets_lint(self) -> APIResponse:
        """Get preset schema validation errors and a full lint run."""
        try:
            data = operations.get_presets_lint(self.preset_loader)
            return self.success_response(data=data)
        except ValueError as e:
            return self.error_api_response(
                error="presets_lint_failed",
                message=str(e)
            )
        except Exception as e:
            self.logger.exception(f"Failed to lint presets: {e}")
            return self.error_api_response(
                error="presets_lint_failed",
                message="Failed to lint presets"
            )

    async def get_docs_lint(self) -> APIResponse:
        """Lint the typed documentation (Docs 2.0)."""
        try:
            data = operations.get_docs_lint()
            return self.success_response(data=data)
        except ValueError as e:
            return self.error_api_response(
                error="docs_lint_failed",
                message=str(e)
            )
        except Exception as e:
            self.logger.exception(f"Failed to lint docs: {e}")
            return self.error_api_response(
                error="docs_lint_failed",
                message="Failed to lint docs"
            )


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.developer_controller
    router = APIRouter(prefix="/api/developer", tags=["Developer"])

    # ========== Route Handlers ==========
    # Keep these thin - just delegate to controller methods

    @router.get(
        "/template-functions",
        response_model=APIResponse,
        summary="Get template-function documentation for pipelines",
    )
    async def get_template_functions(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Get documentation for the template surface available in pipeline.yml files.

        This endpoint documents the Jinja2 globals (path, icon, get_speed_profile),
        filters (matches, default), and template context roots (form, request,
        generation, preset, runtime, paths) usable in pipeline configurations.

        Requires: Admin authentication
        """
        if current_user.account_type != AccountType.ADMIN:
            raise HTTPException(status_code=403, detail="Admin access required")

        return await controller.get_template_functions_documentation()

    @router.get(
        "/presets/lint",
        response_model=APIResponse,
        summary="Lint all presets and return validation errors",
    )
    async def get_presets_lint(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Get preset schema validation errors plus a full lint run.

        Returns the loader's per-preset validation errors (from the last load,
        i.e. what's currently live) together with a fresh PresetLinter pass that
        additionally checks cross-file things like orphaned mode directories and
        missing option-file references.

        Requires: Admin authentication
        """
        if current_user.account_type != AccountType.ADMIN:
            raise HTTPException(status_code=403, detail="Admin access required")

        return await controller.get_presets_lint()

    @router.get(
        "/docs/lint",
        response_model=APIResponse,
        summary="Lint the typed documentation (Docs 2.0)",
    )
    async def get_docs_lint(
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Lint the typed documentation (Docs 2.0).

        Validates every ``type: technique`` / ``type: model`` doc under
        ``docs/techniques`` and ``docs/models`` (schema, family_keys, enum values,
        broken related slugs, arxiv format) and returns the issues.

        Requires: Admin authentication
        """
        if current_user.account_type != AccountType.ADMIN:
            raise HTTPException(status_code=403, detail="Admin access required")

        return await controller.get_docs_lint()

    return router
