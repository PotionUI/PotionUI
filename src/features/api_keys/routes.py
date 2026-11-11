from typing import TYPE_CHECKING, Callable, Dict, Iterable, Literal, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, Query

from src.features.api_keys.dto import ApiKeyCreateRequest, ApiKeyUpdateRequest, UserApiAccessRequest
from src.features.api_keys.errors import ApiKeyError
from src.features.api_keys.mappers import (
    access_to_dict,
    event_to_dict,
    key_to_dict,
    limits_to_dict,
    related_user_ids,
)
from src.features.api_keys.operations import access as access_operations
from src.features.api_keys.operations import keys as operations
from src.features.api_keys.operations.keys import ApiKeyCollaborators
from src.features.api_keys.records import ApiKey
from src.platform.database.rows import now_utc
from src.platform.http.base_controller import APIResponse, BaseController
from src.platform.security.api_scopes import SESSION_ONLY, ApiScope
from src.platform.security.current_user import get_current_active_user, get_current_admin_user
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.bootstrap.container import AppContainer

ToolCounts = Callable[[Iterable[str]], Dict[str, Tuple[int, int]]]


class ApiKeyController(BaseController):
    def __init__(
        self,
        collaborators: ApiKeyCollaborators,
        users,
        tool_counts: ToolCounts,
    ):
        super().__init__()
        self._c = collaborators
        self._users = users
        self._tool_counts = tool_counts

    def _fail(self, exc: ApiKeyError):
        self.error_response(error=exc.error, message=exc.message, status_code=exc.status_code)

    def _key_dicts(self, keys: Sequence[ApiKey], owners: Optional[Dict[str, str]] = None) -> list:
        now = now_utc()
        names = self._c.keys.usernames(related_user_ids(keys))
        counts = self._tool_counts(
            dict.fromkeys(key.user_id for key in keys if ApiScope.MCP in key.scopes)
        )
        result = []
        for key in keys:
            item = key_to_dict(key, now, names, counts.get(key.user_id))
            if owners is not None:
                item["owner"] = {"id": key.user_id, "username": owners.get(key.user_id)}
            result.append(item)
        return result

    async def list_keys(self, user: User) -> APIResponse:
        keys = operations.list_for_user(self._c, user.id)
        limits, access = operations.effective_switches(self._c, user.id)
        return self.success_response(data={
            "keys": self._key_dicts(keys),
            "limits": limits_to_dict(limits, access, user.account_type),
        })

    async def create_key(self, request: ApiKeyCreateRequest, user: User) -> APIResponse:
        try:
            key, secret = operations.mint(
                self._c, user, request.name, request.scopes,
                request.expires_in_days, "expires_in_days" in request.model_fields_set,
            )
        except ApiKeyError as exc:
            self._fail(exc)
        return self.success_response(data={"key": self._key_dicts([key])[0], "secret": secret})

    async def update_key(self, key_id: str, request: ApiKeyUpdateRequest, user: User) -> APIResponse:
        try:
            key = operations.update(self._c, user, key_id, request.name, request.scopes)
        except ApiKeyError as exc:
            self._fail(exc)
        return self.success_response(data=self._key_dicts([key])[0])

    async def revoke_key(self, key_id: str, user: User) -> APIResponse:
        try:
            operations.revoke_own(self._c, user, key_id)
        except ApiKeyError as exc:
            self._fail(exc)
        return self.success_response(data={"id": key_id, "revoked": True})

    async def admin_list(
        self, status: str, user_id: Optional[str], page: int, page_size: int
    ) -> APIResponse:
        now = now_utc()
        keys, total = self._c.keys.list_page(
            status, user_id, now.isoformat(), page_size, (page - 1) * page_size
        )
        owners = self._c.keys.usernames(key.user_id for key in keys)
        return self.success_response(data={
            "items": self._key_dicts(keys, owners),
            "total": total,
            "page": page,
            "page_size": page_size,
        })

    async def admin_revoke(self, key_id: str, admin: User) -> APIResponse:
        try:
            operations.revoke_any(self._c, admin, key_id)
        except ApiKeyError as exc:
            self._fail(exc)
        return self.success_response(data={"id": key_id, "revoked": True})

    async def admin_events(self, key_id: str) -> APIResponse:
        if self._c.keys.get_by_id(key_id) is None:
            self.error_response(error="key_not_found", message="Key not found", status_code=404)
        key = self._c.keys.get_by_id(key_id)
        events = operations.key_history(self._c, key)
        names = self._c.keys.usernames(event.actor_id for event in events)
        return self.success_response(data=[event_to_dict(event, names) for event in events])

    async def key_events(self, key_id: str, user: User, page: int, page_size: int) -> APIResponse:
        try:
            key = operations.owned_key(self._c, key_id, user.id)
        except ApiKeyError as exc:
            self._fail(exc)
        events = list(reversed(operations.key_history(self._c, key)))
        window = events[(page - 1) * page_size: page * page_size]
        names = self._c.keys.usernames(event.actor_id for event in window)
        return self.success_response(data={
            "items": [event_to_dict(event, names) for event in window],
            "total": len(events),
            "page": page,
            "page_size": page_size,
        })

    async def get_user_access(self, user_id: str) -> APIResponse:
        try:
            access = access_operations.get_user_access(self._c.access, self._users, user_id)
        except ApiKeyError as exc:
            self._fail(exc)
        return self.success_response(data=access_to_dict(access))

    async def set_user_access(self, user_id: str, request: UserApiAccessRequest, admin: User) -> APIResponse:
        try:
            access = access_operations.set_user_access(
                self._c.access, self._users, admin.id, user_id, request.keys_enabled, request.mcp_enabled
            )
        except ApiKeyError as exc:
            self._fail(exc)
        return self.success_response(data=access_to_dict(access))


def _controller(container: "AppContainer") -> ApiKeyController:
    return ApiKeyController(container.api_key_collaborators, container.user_repository, container.mcp_tool_counts)


def build_router(container: "AppContainer") -> APIRouter:
    controller = _controller(container)
    router = APIRouter(prefix="/api/keys", tags=["API Keys"], dependencies=[SESSION_ONLY])

    @router.get("", response_model=APIResponse, summary="List your API keys and what you may grant")
    async def list_keys(current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.list_keys(current_user)

    @router.post("", response_model=APIResponse, status_code=201, summary="Create an API key")
    async def create_key(
        request: ApiKeyCreateRequest, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.create_key(request, current_user)

    @router.patch("/{key_id}", response_model=APIResponse, summary="Rename an API key or change its scopes")
    async def update_key(
        key_id: str, request: ApiKeyUpdateRequest, current_user: User = Depends(get_current_active_user)
    ) -> APIResponse:
        return await controller.update_key(key_id, request, current_user)

    @router.get("/{key_id}/events", response_model=APIResponse, summary="Recent activity of one of your API keys")
    async def key_events(
        key_id: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        current_user: User = Depends(get_current_active_user),
    ) -> APIResponse:
        return await controller.key_events(key_id, current_user, page, page_size)

    @router.delete("/{key_id}", response_model=APIResponse, summary="Revoke an API key")
    async def revoke_key(key_id: str, current_user: User = Depends(get_current_active_user)) -> APIResponse:
        return await controller.revoke_key(key_id, current_user)

    return router


def build_admin_router(container: "AppContainer") -> APIRouter:
    controller = _controller(container)
    router = APIRouter(prefix="/api/admin", tags=["API Keys Admin"], dependencies=[SESSION_ONLY])

    @router.get("/keys", response_model=APIResponse, summary="List every API key on the instance")
    async def admin_list(
        status: Literal["active", "expired", "revoked", "all"] = "all",
        user_id: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        _admin: User = Depends(get_current_admin_user),
    ) -> APIResponse:
        return await controller.admin_list(status, user_id, page, page_size)

    @router.delete("/keys/{key_id}", response_model=APIResponse, summary="Revoke any API key")
    async def admin_revoke(key_id: str, admin: User = Depends(get_current_admin_user)) -> APIResponse:
        return await controller.admin_revoke(key_id, admin)

    @router.get("/keys/{key_id}/events", response_model=APIResponse, summary="History of an API key")
    async def admin_events(key_id: str, _admin: User = Depends(get_current_admin_user)) -> APIResponse:
        return await controller.admin_events(key_id)

    @router.get("/users/{user_id}/api-access", response_model=APIResponse, summary="A user's API key switches")
    async def get_user_access(user_id: str, _admin: User = Depends(get_current_admin_user)) -> APIResponse:
        return await controller.get_user_access(user_id)

    @router.put("/users/{user_id}/api-access", response_model=APIResponse, summary="Set a user's API key switches")
    async def set_user_access(
        user_id: str, request: UserApiAccessRequest, admin: User = Depends(get_current_admin_user)
    ) -> APIResponse:
        return await controller.set_user_access(user_id, request, admin)

    return router
