import logging
from typing import TYPE_CHECKING, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.features.api_keys.errors import ApiKeyError
from src.features.api_keys.operations.keys import ApiKeyCollaborators
from src.features.mcp.dto import McpKeyToolsPatchRequest, McpToolsPatchRequest
from src.features.mcp.errors import McpToolError
from src.features.mcp.operations import tools as tool_operations
from src.features.mcp.protocol import JsonRpcError, McpToolCollaborators, handle_method, parse_jsonrpc_request
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.api_scopes import SESSION_ONLY, ApiScope, key_scope
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import AccountType, User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

logger = logging.getLogger(__name__)


def _jsonrpc_error(request_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


def request_origin(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    scheme = forwarded_proto or request.url.scheme
    host = forwarded_host or request.headers.get("host", "").strip() or request.url.netloc
    return f"{scheme}://{host}" if host else ""


async def require_key_caller(current_user: User = Depends(get_current_active_user)) -> User:
    if current_user.key is None:
        raise HTTPException(
            status_code=401,
            detail={"success": False, "error": "api_key_required", "message": "MCP needs an API key"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


class McpToolsController(BaseController):
    def __init__(self, collaborators: McpToolCollaborators, keys: ApiKeyCollaborators):
        super().__init__()
        self._c = collaborators
        self._keys = keys

    def _fail(self, exc: Union[McpToolError, ApiKeyError]):
        self.error_response(error=exc.error, message=exc.message, status_code=exc.status_code)

    async def default_listing(self, user: User) -> APIResponse:
        return self.success_response(data=tool_operations.default_listing(self._c, user.id))

    async def patch_default(self, request: McpToolsPatchRequest, user: User) -> APIResponse:
        try:
            listing = tool_operations.patch_default(self._c, user.id, request.policy, request.overrides)
        except (McpToolError, ApiKeyError) as exc:
            self._fail(exc)
        return self.success_response(data=listing)

    async def key_listing(self, key_id: str, user: User) -> APIResponse:
        try:
            key = tool_operations.key_for_tools(self._keys, user, key_id)
        except (McpToolError, ApiKeyError) as exc:
            self._fail(exc)
        return self.success_response(data=tool_operations.key_listing(self._c, user.id, key.id))

    async def patch_key(self, key_id: str, request: McpKeyToolsPatchRequest, user: User) -> APIResponse:
        try:
            listing = tool_operations.patch_key(
                self._c, self._keys, user, key_id, request.overrides, bool(request.reset)
            )
        except (McpToolError, ApiKeyError) as exc:
            self._fail(exc)
        return self.success_response(data=listing)

    async def ceiling_listing(self) -> APIResponse:
        return self.success_response(data=tool_operations.ceiling_listing(self._c))

    async def patch_ceiling(self, request: McpToolsPatchRequest, admin: User) -> APIResponse:
        try:
            listing = tool_operations.patch_ceiling(self._c, admin.id, request.policy, request.overrides)
        except McpToolError as exc:
            self._fail(exc)
        return self.success_response(data=listing)


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller = McpToolsController(container.mcp_tool_collaborators, container.api_key_collaborators)
    router = APIRouter(prefix="/api/admin/mcp", tags=["MCP Admin"], dependencies=[SESSION_ONLY])

    @router.get("/tools", response_model=APIResponse, summary="MCP tools the administrator allows")
    async def get_ceiling(_admin: User = Depends(get_current_admin_user)) -> APIResponse:
        return await controller.ceiling_listing()

    @router.patch("/tools", response_model=APIResponse, summary="Change which MCP tools users may expose")
    async def patch_ceiling(
        request: McpToolsPatchRequest, admin: User = Depends(get_current_admin_user)
    ) -> APIResponse:
        return await controller.patch_ceiling(request, admin)

    return router


def build_router(container: "AppContainer") -> APIRouter:
    collaborators: McpToolCollaborators = container.mcp_tool_collaborators
    controller = McpToolsController(collaborators, container.api_key_collaborators)

    router = APIRouter(prefix="/api/mcp", tags=["MCP"])

    @router.get("/tools", response_model=APIResponse, summary="Your default MCP tools", dependencies=[SESSION_ONLY])
    async def get_default_tools(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.default_listing(current_user)

    @router.patch(
        "/tools", response_model=APIResponse, summary="Change your default MCP tools", dependencies=[SESSION_ONLY]
    )
    async def patch_default_tools(
        request: McpToolsPatchRequest, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.patch_default(request, current_user)

    @router.get(
        "/keys/{key_id}/tools", response_model=APIResponse, summary="The MCP tools of one API key",
        dependencies=[SESSION_ONLY],
    )
    async def get_key_tools(key_id: str, current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.key_listing(key_id, current_user)

    @router.patch(
        "/keys/{key_id}/tools", response_model=APIResponse, summary="Narrow the MCP tools of one API key",
        dependencies=[SESSION_ONLY],
    )
    async def patch_key_tools(
        key_id: str, request: McpKeyToolsPatchRequest, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.patch_key(key_id, request, current_user)

    @router.post("", dependencies=[key_scope(ApiScope.MCP)])
    async def mcp_endpoint(request: Request, current_user: User = Depends(require_key_caller)):
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
            result = await handle_method(
                collaborators, method, params, current_user.id,
                key_id=current_user.key.key_id,
                is_admin=current_user.owner_account_type == AccountType.ADMIN,
                acts_as_admin=current_user.account_type == AccountType.ADMIN,
                request_origin=request_origin(request),
            )
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
