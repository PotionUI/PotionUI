"""HTTP surface for MCP: user token CRUD, self-service + admin status, and the
stateless JSON-RPC `/api/mcp` endpoint external MCP clients connect to with
`Authorization: Bearer <token>`.

The global on/off (`mcp_enabled`) is an ordinary SYSTEM setting, edited
through the existing admin settings surface (`/api/settings`) — no dedicated
route for it here. Token CRUD and the two status/toggle routes use the app's
usual `APIResponse` envelope; `/api/mcp` itself does not — it must speak raw
JSON-RPC 2.0.

Mutations and toggle reads go through `src.features.mcp.operations`
(formerly `McpManager`); `McpController` holds the token/settings/user
repositories and passes them in.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.features.mcp import operations
from src.features.mcp.dto import McpTokenCreateRequest, McpUserToggleRequest
from src.features.mcp.mappers import token_to_dict
from src.features.mcp.protocol import JsonRpcError, McpToolCollaborators, handle_method, parse_jsonrpc_request
from src.features.mcp.repository import McpTokenRepository
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import User
from src.platform.settings.settings import Settings

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


class McpController(BaseController):
    def __init__(
        self,
        token_repository: McpTokenRepository,
        settings: Settings,
        user_repository,
    ):
        super().__init__()
        self._tokens = token_repository
        self._settings = settings
        self._users = user_repository

    async def list_tokens(self, user: User) -> APIResponse:
        tokens = self._tokens.list_for_user(user.id)
        return self.success_response(data=[token_to_dict(t) for t in tokens])

    async def create_token(self, request: McpTokenCreateRequest, user: User) -> APIResponse:
        name = (request.name or "").strip()
        if not name:
            return self.error_api_response(error="invalid_name", message="name is required")
        token, plaintext = operations.mint_token(self._tokens, user.id, name)
        return self.success_response(data={**token_to_dict(token), "token": plaintext})

    async def revoke_token(self, token_id: str, user: User) -> APIResponse:
        revoked = operations.revoke_token(self._tokens, user.id, token_id)
        if not revoked:
            self.error_response(error="token_not_found", message="Token not found", status_code=404)
        return self.success_response(data={"id": token_id, "revoked": True})

    async def get_status(self, user: User) -> APIResponse:
        global_enabled = operations.is_globally_enabled(self._settings)
        user_enabled = operations.is_user_enabled(self._settings, user.id)
        return self.success_response(data={
            "enabled": global_enabled and user_enabled,
            "global_enabled": global_enabled,
            "user_enabled": user_enabled,
        })

    async def get_user_toggle(self, user_id: str) -> APIResponse:
        if not self._users.get_by_id(user_id):
            self.error_response(error="user_not_found", message="User not found", status_code=404)
        return self.success_response(
            data={"user_id": user_id, "enabled": operations.is_user_enabled(self._settings, user_id)}
        )

    async def set_user_toggle(self, user_id: str, enabled: bool) -> APIResponse:
        try:
            operations.set_user_enabled(self._settings, self._users, user_id, enabled)
        except ValueError:
            self.error_response(error="user_not_found", message="User not found", status_code=404)
        return self.success_response(data={"user_id": user_id, "enabled": enabled})


def _jsonrpc_error(request_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


def build_router(container: "AppContainer") -> APIRouter:
    token_repository = container.mcp_token_repository
    settings = container.settings
    user_repository = container.user_repository
    collaborators: McpToolCollaborators = container.mcp_tool_collaborators
    controller = McpController(
        token_repository=token_repository, settings=settings, user_repository=user_repository,
    )

    router = APIRouter(prefix="/api/mcp", tags=["MCP"])

    # --- self-service: status + token CRUD ---

    @router.get("/status", response_model=APIResponse)
    async def get_status(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.get_status(current_user)

    @router.get("/tokens", response_model=APIResponse)
    async def list_tokens(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.list_tokens(current_user)

    @router.post("/tokens", response_model=APIResponse)
    async def create_token(
        request: McpTokenCreateRequest, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.create_token(request, current_user)

    @router.delete("/tokens/{token_id}", response_model=APIResponse)
    async def revoke_token(token_id: str, current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.revoke_token(token_id, current_user)

    # --- admin per-user toggle ---

    @router.get("/admin/users/{user_id}", response_model=APIResponse)
    async def get_user_toggle(user_id: str, _admin: User = Depends(get_current_admin_user)) -> APIResponse:
        return await controller.get_user_toggle(user_id)

    @router.put("/admin/users/{user_id}", response_model=APIResponse)
    async def set_user_toggle(
        user_id: str, request: McpUserToggleRequest, _admin: User = Depends(get_current_admin_user)
    ) -> APIResponse:
        return await controller.set_user_toggle(user_id, request.enabled)

    # --- the MCP endpoint itself ---

    async def _authenticate(request: Request):
        """Bearer-token auth for MCP requests: resolves an active token to its
        owning user, enforcing both the global and per-user MCP toggles.
        Raises HTTPException (never returns a falsy user)."""
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401, detail="Missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        plaintext = auth_header[len("bearer "):].strip()
        token = operations.resolve_active_token(token_repository, plaintext)
        if token is None:
            raise HTTPException(
                status_code=401, detail="Invalid or revoked token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not operations.is_globally_enabled(settings):
            raise HTTPException(status_code=403, detail="MCP is disabled on this instance")
        if not operations.is_user_enabled(settings, token.user_id):
            raise HTTPException(status_code=403, detail="MCP is disabled for this user")
        user = user_repository.get_by_id(token.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Token owner no longer exists")
        operations.record_use(token_repository, token)
        return user

    @router.post("")
    async def mcp_endpoint(request: Request):
        current_user = await _authenticate(request)

        try:
            body = await request.json()
        except Exception:
            return _jsonrpc_error(None, -32700, "Parse error: invalid JSON")

        try:
            request_id, method, params = parse_jsonrpc_request(body)
        except JsonRpcError as exc:
            return _jsonrpc_error(body.get("id") if isinstance(body, dict) else None, exc.code, exc.message)

        is_notification = isinstance(body, dict) and "id" not in body

        try:
            result = await handle_method(collaborators, method, params, current_user.id)
        except JsonRpcError as exc:
            if is_notification:
                return JSONResponse(status_code=202, content=None)
            return _jsonrpc_error(request_id, exc.code, exc.message)
        except Exception:
            logger.exception("Unhandled error in MCP method '%s'", method)
            if is_notification:
                return JSONResponse(status_code=202, content=None)
            return _jsonrpc_error(request_id, -32603, "Internal error")

        if is_notification:
            return JSONResponse(status_code=202, content=None)

        return JSONResponse(
            status_code=200,
            content={"jsonrpc": "2.0", "id": request_id, "result": result},
        )

    return router
