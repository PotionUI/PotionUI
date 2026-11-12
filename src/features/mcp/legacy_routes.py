from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from src.features.api_keys.errors import ApiKeyError
from src.features.api_keys.operations import access as access_operations
from src.features.api_keys.operations import keys as key_operations
from src.features.api_keys.operations.keys import ApiKeyCollaborators
from src.features.api_keys.records import ApiKey
from src.features.api_keys.settings import MCP_ENABLED_KEY
from src.features.mcp.dto import McpTokenCreateRequest, McpUserToggleRequest
from src.platform.database.rows import dt_column
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.api_scopes import SESSION_ONLY, ApiScope
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


def token_to_dict(key: ApiKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "token_prefix": key.display_prefix,
        "created_at": key.created_at,
        "last_used_at": key.last_used_at,
        "revoked_at": key.revoked_at,
    }


class LegacyMcpTokensController(BaseController):
    def __init__(self, keys: ApiKeyCollaborators, users):
        super().__init__()
        self._c = keys
        self._users = users

    def _mcp_keys(self, user_id: str):
        owned = [key for key in self._c.keys.list_for_user(user_id) if ApiScope.MCP in key.scopes]
        return sorted(owned, key=lambda key: dt_column(key.created_at) or _EPOCH, reverse=True)

    async def get_status(self, user: User) -> APIResponse:
        global_enabled = bool(self._c.settings.get_setting(MCP_ENABLED_KEY, False))
        user_enabled = self._c.access.get(user.id).mcp_enabled
        return self.success_response(data={
            "enabled": global_enabled and user_enabled,
            "global_enabled": global_enabled,
            "user_enabled": user_enabled,
        })

    async def list_tokens(self, user: User) -> APIResponse:
        return self.success_response(data=[token_to_dict(key) for key in self._mcp_keys(user.id)])

    async def create_token(self, request: McpTokenCreateRequest, user: User) -> APIResponse:
        name = (request.name or "").strip()
        if not name:
            return self.error_api_response(error="invalid_name", message="name is required")
        try:
            key, secret = key_operations.mint(self._c, user, name, [ApiScope.MCP.value], None, True)
        except ApiKeyError as exc:
            self.error_response(error=exc.error, message=exc.message, status_code=exc.status_code)
        return self.success_response(data={**token_to_dict(key), "token": secret})

    async def revoke_token(self, token_id: str, user: User) -> APIResponse:
        key = self._c.keys.get_by_id(token_id)
        if key is None or key.user_id != user.id or ApiScope.MCP not in key.scopes or key.is_revoked:
            self.error_response(error="token_not_found", message="Token not found", status_code=404)
        key_operations.revoke_own(self._c, user, token_id)
        return self.success_response(data={"id": token_id, "revoked": True})

    async def get_user_toggle(self, user_id: str) -> APIResponse:
        if self._users.get_by_id(user_id) is None:
            self.error_response(error="user_not_found", message="User not found", status_code=404)
        enabled = self._c.access.get(user_id).mcp_enabled
        return self.success_response(data={"user_id": user_id, "enabled": enabled})

    async def set_user_toggle(self, user_id: str, enabled: bool, admin: User) -> APIResponse:
        try:
            current = access_operations.get_user_access(self._c.access, self._users, user_id)
            access_operations.set_user_access(
                self._c.access, self._users, admin.id, user_id, current.keys_enabled, enabled
            )
        except ApiKeyError:
            self.error_response(error="user_not_found", message="User not found", status_code=404)
        return self.success_response(data={"user_id": user_id, "enabled": enabled})


def build_legacy_router(container: "AppContainer") -> APIRouter:
    controller = LegacyMcpTokensController(container.api_key_collaborators, container.user_repository)
    router = APIRouter(prefix="/api/mcp", tags=["MCP"], deprecated=True, dependencies=[SESSION_ONLY])

    @router.get("/status", response_model=APIResponse, summary="MCP status (use /api/keys and /api/settings)")
    async def get_status(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.get_status(current_user)

    @router.get("/tokens", response_model=APIResponse, summary="List MCP tokens (use GET /api/keys)")
    async def list_tokens(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.list_tokens(current_user)

    @router.post("/tokens", response_model=APIResponse, summary="Create an MCP token (use POST /api/keys)")
    async def create_token(
        request: McpTokenCreateRequest, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.create_token(request, current_user)

    @router.delete(
        "/tokens/{token_id}", response_model=APIResponse, summary="Revoke an MCP token (use DELETE /api/keys/{id})"
    )
    async def revoke_token(token_id: str, current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.revoke_token(token_id, current_user)

    @router.get(
        "/admin/users/{user_id}", response_model=APIResponse,
        summary="A user's MCP switch (use GET /api/admin/users/{id}/api-access)",
    )
    async def get_user_toggle(user_id: str, _admin: User = Depends(get_current_admin_user)) -> APIResponse:
        return await controller.get_user_toggle(user_id)

    @router.put(
        "/admin/users/{user_id}", response_model=APIResponse,
        summary="Set a user's MCP switch (use PUT /api/admin/users/{id}/api-access)",
    )
    async def set_user_toggle(
        user_id: str, request: McpUserToggleRequest, admin: User = Depends(get_current_admin_user)
    ) -> APIResponse:
        return await controller.set_user_toggle(user_id, request.enabled, admin)

    return router
