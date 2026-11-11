import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.features.mcp.protocol import JsonRpcError, McpToolCollaborators, handle_method, parse_jsonrpc_request
from src.platform.security.api_scopes import ApiScope, key_scope
from src.platform.security.current_user import get_current_active_user
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


def build_router(container: "AppContainer") -> APIRouter:
    collaborators: McpToolCollaborators = container.mcp_tool_collaborators

    router = APIRouter(prefix="/api/mcp", tags=["MCP"])

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
                is_admin=current_user.owner_account_type == AccountType.ADMIN,
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
