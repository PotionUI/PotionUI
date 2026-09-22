"""Who is calling.

`get_current_active_user` is a FastAPI dependency: declare it on a route and the
request arrives with an authenticated `User`, or never arrives at all.

    @router.get("/things")
    async def list_things(user: User = Depends(get_current_active_user)):
        ...

`get_current_admin_user` is the same, narrowed to administrators: the request
either arrives with an admin `User` or is rejected with 403. Use it to gate a
route that manages global, cross-user state.

    @router.post("/dangerous")
    async def do_it(user: User = Depends(get_current_admin_user)):
        ...

`authenticate_websocket_token` is the WebSocket counterpart. WebSocket routes
cannot use `Depends`, so authenticate the query-string token by hand before
accepting the connection:

    @ws_router.websocket("/ws/plugins/<id>")
    async def endpoint(websocket: WebSocket, token: str = Query(None)):
        user, error = authenticate_websocket_token(token)
        if user is None:
            await websocket.accept()
            await websocket.close(code=4001, reason=error or "Authentication failed")
            return

Check `user.account_type == AccountType.ADMIN` to restrict a route to admins.
"""

from typing import Any, Dict, List, Optional

from src.features.auth.external_login import ExternalLoginError, ExternalSession
from src.platform.plugins.login_providers import (
    DuplicateLoginProviderError,
    InvalidLoginProviderError,
    LoginProviderDefinition,
    login_provider_registry,
    source_from_start_path,
)
from src.platform.plugins.runtime_registries import get_container
from src.platform.security.current_user import (
    authenticate_websocket_token,
    get_current_active_user,
    get_current_admin_user,
)
from src.platform.security.user import AccountType, User

__all__ = [
    "AccountType",
    "DuplicateLoginProviderError",
    "ExternalLoginError",
    "ExternalSession",
    "InvalidLoginProviderError",
    "User",
    "authenticate_websocket_token",
    "get_current_active_user",
    "get_current_admin_user",
    "list_user_ids",
    "register_login_provider",
    "sign_in_external",
    "unregister_login_provider",
]


def list_user_ids() -> List[str]:
    """Every user id in the instance, admins included - the "fan out to all
    users" default a plugin uses when a caller doesn't name specific ones."""
    return [user.id for user in get_container().user_repository.get_all()]


def register_login_provider(id: str, label: str, start_path: str) -> None:
    login_provider_registry.register(
        LoginProviderDefinition(
            id=id,
            label=label,
            start_path=start_path,
            source=source_from_start_path(start_path) or id,
        )
    )


def unregister_login_provider(id: str) -> None:
    login_provider_registry.unregister(id)


def sign_in_external(
    issuer: str,
    sub: str,
    claims: Optional[Dict[str, Any]] = None,
) -> ExternalSession:
    return get_container().external_login.sign_in_external(issuer, sub, claims)
