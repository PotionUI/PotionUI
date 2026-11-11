"""
Documentation Controller.

Exposes the in-app Documentation feature built by
`src/features/docs/operations.py`: a role-filtered tree aggregated from repo
markdown, plugin manifests, and live-reference sources, plus per-doc markdown
content. Also hosts the live-reference endpoints (pipes, output types, MCP
tools) the developer-only "live" tree entries render from.
"""
from pathlib import Path
from typing import Any, Dict, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query

from src.platform.http.base_controller import BaseController, APIResponse
from src.platform.http.error_pipeline import Fail, guarded, soft_guarded
from src.platform.security.failure_text import failure_detail
from src.platform.security.api_scopes import ApiScope, key_access, key_scope
from src.platform.security.current_user import get_current_active_user
from src.features.docs import operations
from src.features.docs.operations import DocNotFoundError, DocForbiddenError, DocIsLiveError
from src.features.developer.mcp_tools_documenter import McpToolsDocumenter
from src.features.developer.output_types_documenter import OutputTypesDocumenter
from src.features.developer.pipes_documenter import PipesDocumenter
from src.platform.security.user import User, AccountType

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer


class DocsController(BaseController):
    """Controller for the in-app Documentation feature."""

    def __init__(
        self,
        plugin_registry,
        base_docs_path: str,
        pipes_documenter: PipesDocumenter,
        output_types_documenter: OutputTypesDocumenter,
        mcp_tools_documenter: McpToolsDocumenter,
    ):
        super().__init__()
        self.plugin_registry = plugin_registry
        self.base_docs_path = Path(base_docs_path)
        self.pipes_documenter = pipes_documenter
        self.output_types_documenter = output_types_documenter
        self.mcp_tools_documenter = mcp_tools_documenter

    @soft_guarded("docs_tree_failed", "Failed to build docs tree")
    async def get_tree(self, is_admin: bool) -> APIResponse:
        """Get the role-filtered documentation tree."""
        data = operations.build_tree(self.plugin_registry, self.base_docs_path, is_admin)
        return self.success_response(data=data)

    @guarded(
        {
            DocNotFoundError: Fail("doc_not_found", status=404),
            DocForbiddenError: Fail("doc_forbidden", status=403),
            DocIsLiveError: Fail("doc_is_live", status=400),
        },
        Fail("doc_content_failed", "Failed to get doc content", detail=failure_detail),
    )
    async def get_content(self, doc_id: str, is_admin: bool) -> APIResponse:
        """Get the raw markdown content for a doc id."""
        data = operations.get_content(self.plugin_registry, self.base_docs_path, doc_id, is_admin)
        return self.success_response(data=data)

    @soft_guarded("live_pipes_failed", "Failed to get live pipes reference")
    async def get_live_pipes(self) -> APIResponse:
        """Get the live pipe registry reference (name, description, inputs/outputs/config specs)."""
        data = self.pipes_documenter.generate_documentation()
        return self.success_response(data=data)

    @soft_guarded("live_output_types_failed", "Failed to get live output-types reference")
    async def get_live_output_types(self) -> APIResponse:
        """Get the live output-type registry reference."""
        data = self.output_types_documenter.generate_documentation()
        return self.success_response(data=data)

    @soft_guarded("live_mcp_tools_failed", "Failed to get live MCP tools reference")
    async def get_live_mcp_tools(self) -> APIResponse:
        """Get the live MCP tool-surface reference."""
        data = self.mcp_tools_documenter.generate_documentation()
        return self.success_response(data=data)


def _is_admin(user: User) -> bool:
    return user.account_type == AccountType.ADMIN


def build_router(container: "AppContainer") -> APIRouter:
    controller = container.docs_controller
    router = APIRouter(prefix="/api/docs", tags=["Documentation"], dependencies=[key_access(read=ApiScope.READ, write=None)])

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

    @router.get("/live/pipes", response_model=APIResponse, summary="Get Live Pipes Reference", dependencies=[key_scope(ApiScope.ADMIN)])
    async def get_live_pipes(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        """Get the live pipe registry reference.

        Requires: Admin authentication
        """
        if current_user.account_type != AccountType.ADMIN:
            raise HTTPException(status_code=403, detail="Admin access required")

        return await controller.get_live_pipes()

    @router.get("/live/output-types", response_model=APIResponse, summary="Get Live Output Types Reference", dependencies=[key_scope(ApiScope.ADMIN)])
    async def get_live_output_types(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        """Get the live output-type registry reference.

        Requires: Admin authentication
        """
        if current_user.account_type != AccountType.ADMIN:
            raise HTTPException(status_code=403, detail="Admin access required")

        return await controller.get_live_output_types()

    @router.get("/live/mcp-tools", response_model=APIResponse, summary="Get Live MCP Tools Reference", dependencies=[key_scope(ApiScope.ADMIN)])
    async def get_live_mcp_tools(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        """Get the live MCP tool-surface reference.

        Requires: Admin authentication
        """
        if current_user.account_type != AccountType.ADMIN:
            raise HTTPException(status_code=403, detail="Admin access required")

        return await controller.get_live_mcp_tools()

    return router
