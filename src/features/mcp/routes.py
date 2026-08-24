"""HTTP surface for MCP: user token CRUD, self-service + admin status, and the
stateless JSON-RPC `/api/mcp` endpoint external MCP clients connect to with
`Authorization: Bearer <token>`.

The global on/off (`mcp_enabled`) is an ordinary SYSTEM setting, edited
through the existing admin settings surface (`/api/settings`) — no dedicated
route for it here. Token CRUD and the two status/toggle routes use the app's
usual `APIResponse` envelope; `/api/mcp` itself does not — it must speak raw
JSON-RPC 2.0.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.features.mcp.dto import McpTokenCreateRequest, McpUserToggleRequest
from src.features.mcp.manager import McpManager
from src.features.mcp.protocol import JsonRpcError, McpProtocolManager, parse_jsonrpc_request
from src.features.mcp.repository import McpTokenRepository
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


def _token_dict(token) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "token_prefix": token.token_prefix,
        "created_at": token.created_at,
        "last_used_at": token.last_used_at,
        "revoked_at": token.revoked_at,
    }


class McpController(BaseController):
    def __init__(self, token_repository: McpTokenRepository, manager: McpManager, user_repository):
        super().__init__()
        self._tokens = token_repository
        self._manager = manager
        self._users = user_repository

    async def list_tokens(self, user: User) -> APIResponse:
        tokens = self._tokens.list_for_user(user.id)
        return self.success_response(data=[_token_dict(t) for t in tokens])

    async def create_token(self, request: McpTokenCreateRequest, user: User) -> APIResponse:
        name = (request.name or "").strip()
        if not name:
            return self.error_api_response(error="invalid_name", message="name is required")
        token, plaintext = self._manager.mint_token(user.id, name)
        return self.success_response(data={**_token_dict(token), "token": plaintext})

    async def revoke_token(self, token_id: str, user: User) -> APIResponse:
        revoked = self._manager.revoke_token(user.id, token_id)
        if not revoked:
            self.error_response(error="token_not_found", message="Token not found", status_code=404)
        return self.success_response(data={"id": token_id, "revoked": True})

    async def get_status(self, user: User) -> APIResponse:
        global_enabled = self._manager.is_globally_enabled()
        user_enabled = self._manager.is_user_enabled(user.id)
        return self.success_response(data={
            "enabled": global_enabled and user_enabled,
            "global_enabled": global_enabled,
            "user_enabled": user_enabled,
        })

    async def get_user_toggle(self, user_id: str) -> APIResponse:
        if not self._users.get_by_id(user_id):
            self.error_response(error="user_not_found", message="User not found", status_code=404)
        return self.success_response(data={"user_id": user_id, "enabled": self._manager.is_user_enabled(user_id)})

    async def set_user_toggle(self, user_id: str, enabled: bool) -> APIResponse:
        try:
            self._manager.set_user_enabled(user_id, enabled)
        except ValueError:
            self.error_response(error="user_not_found", message="User not found", status_code=404)
        return self.success_response(data={"user_id": user_id, "enabled": enabled})


def _jsonrpc_error(request_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


def build_router(container: "AppContainer") -> APIRouter:
    manager: McpManager = container.mcp_manager
    protocol_manager: McpProtocolManager = container.mcp_protocol_manager
    user_repository = container.user_repository
    controller = McpController(
        token_repository=container.mcp_token_repository, manager=manager, user_repository=user_repository,
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
        token = manager.resolve_active_token(plaintext)
        if token is None:
            raise HTTPException(
                status_code=401, detail="Invalid or revoked token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not manager.is_globally_enabled():
            raise HTTPException(status_code=403, detail="MCP is disabled on this instance")
        if not manager.is_user_enabled(token.user_id):
            raise HTTPException(status_code=403, detail="MCP is disabled for this user")
        user = user_repository.get_by_id(token.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Token owner no longer exists")
        manager.record_use(token)
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
            result = await protocol_manager.handle_method(method, params, current_user.id)
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
