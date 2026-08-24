"""
Documentation Controller.

Exposes the in-app Documentation feature built by `src/features/docs/manager.py`
(`DocsManager`): a role-filtered tree aggregated from repo markdown, plugin
manifests, and live-reference sources, plus per-doc markdown content. Also
hosts the two new live-reference endpoints (pipes, output types) the
developer-only "live" tree entries render from.
"""
from typing import Any, Dict, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.security.current_user import get_current_active_user
from src.features.docs.manager import DocsManager, DocNotFoundError, DocForbiddenError, DocIsLiveError
from src.features.developer import DeveloperManager
from src.features.generation.output_types import output_type_registry
from src.platform.security.user import User, AccountType

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class DocsController(BaseController):
    """Controller for the in-app Documentation feature."""

    def __init__(self, docs_manager: DocsManager, developer_manager: DeveloperManager):
        super().__init__()
        self.docs_manager = docs_manager
        self.developer_manager = developer_manager

    async def get_tree(self, is_admin: bool) -> APIResponse:
        """Get the role-filtered documentation tree."""
        try:
            data = self.docs_manager.build_tree(is_admin)
            return self.success_response(data=data)
        except Exception as e:
            self.logger.error(f"Failed to build docs tree: {e}")
            return self.error_api_response(
                error="docs_tree_failed",
                message=f"Failed to build docs tree: {str(e)}"
            )

    async def get_content(self, doc_id: str, is_admin: bool) -> APIResponse:
        """Get the raw markdown content for a doc id."""
        try:
            data = self.docs_manager.get_content(doc_id, is_admin)
            return self.success_response(data=data)
        except DocNotFoundError as e:
            self.error_response(error="doc_not_found", message=str(e), status_code=404)
        except DocForbiddenError as e:
            self.error_response(error="doc_forbidden", message=str(e), status_code=403)
        except DocIsLiveError as e:
            self.error_response(error="doc_is_live", message=str(e), status_code=400)
        except Exception as e:
            self.logger.error(f"Failed to get doc content: {e}")
            return self.error_api_response(
                error="doc_content_failed",
                message=f"Failed to get doc content: {str(e)}"
            )

    async def get_live_pipes(self) -> APIResponse:
        """Get the live pipe registry reference (name, description, inputs/outputs/config specs)."""
        try:
            data = self.developer_manager.get_pipes_documentation()
            return self.success_response(data=data)
        except Exception as e:
            self.logger.error(f"Failed to get live pipes reference: {e}")
            return self.error_api_response(
                error="live_pipes_failed",
                message=f"Failed to get live pipes reference: {str(e)}"
            )

    async def get_live_output_types(self) -> APIResponse:
        """Get the live output-type registry reference."""
        try:
            specs = output_type_registry.all()
            data = {
                "output_types": [
                    {
                        "key": spec.key,
                        "output_class": spec.output_cls.__name__,
                        "message_type": (
                            spec.message_type if isinstance(spec.message_type, str) else "<dynamic>"
                        ),
                        "has_handler": spec.handler_cls is not None,
                        "has_serializer": spec.serializer is not None,
                    }
                    for spec in specs
                ],
                "total": len(specs),
            }
            return self.success_response(data=data)
        except Exception as e:
            self.logger.error(f"Failed to get live output-types reference: {e}")
            return self.error_api_response(
                error="live_output_types_failed",
                message=f"Failed to get live output-types reference: {str(e)}"
            )


def _is_admin(user: User) -> bool:
    return user.account_type == AccountType.ADMIN


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.docs_controller
    router = APIRouter(prefix="/api/docs", tags=["Documentation"])

    # ========== Route Handlers ==========

    @router.get("/tree", response_model=APIResponse, summary="Get Documentation Tree")
    async def get_tree(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        """Get the role-filtered documentation tree.

        The developer/contributor sections are omitted for non-admins; their
        existence and item counts (not content) are still reported in
        `hidden_sections` so the UI can explain why nothing is missing.
        """
        return await controller.get_tree(_is_admin(current_user))

    @router.get("/content", response_model=APIResponse, summary="Get Documentation Content")
    async def get_content(
        id: str = Query(..., description="Doc id from the tree, e.g. 'user/getting-started'"),
        current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        """Get the raw markdown content for a doc id."""
        return await controller.get_content(id, _is_admin(current_user))

    @router.get("/live/pipes", response_model=APIResponse, summary="Get Live Pipes Reference")
    async def get_live_pipes(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        """Get the live pipe registry reference.

        Requires: Admin authentication
        """
        if current_user.account_type != AccountType.ADMIN:
            raise HTTPException(status_code=403, detail="Admin access required")

        return await controller.get_live_pipes()

    @router.get("/live/output-types", response_model=APIResponse, summary="Get Live Output Types Reference")
    async def get_live_output_types(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        """Get the live output-type registry reference.

        Requires: Admin authentication
        """
        if current_user.account_type != AccountType.ADMIN:
            raise HTTPException(status_code=403, detail="Admin access required")

        return await controller.get_live_output_types()

    return router
