"""
FastAPI authentication dependencies.

This module provides FastAPI dependencies for authentication:
- oauth2_scheme: OAuth2 password bearer scheme
- get_principal: Resolve the bearer (JWT, or API key when a resolver is set) once per request
- get_current_user: Get authenticated user from the principal
- get_current_active_user: Authenticated user (accounts have no disabled flag)
- authenticate_websocket: WebSocket authentication helper (ticket or Authorization header)
"""
import asyncio
import logging
from dataclasses import replace
from typing import FrozenSet, Optional, Tuple

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import OAuth2PasswordBearer

from src.platform.security import Auth
from src.platform.security.api_scopes import ApiScope, ScopeSpec, coerce_scopes, expand_scopes, required_scopes
from src.platform.security.attempt_limiter import LoginAttemptLimiter
from src.platform.security.media_session import media_cookie_token
from src.platform.security.principal import KEY_PREFIX, KeyResolver, Principal, ResolvedKey
from src.platform.security.user import User, AccountType
from src.platform.websocket.tickets import TicketEntry, ws_tickets

# OAuth2 scheme - tokenUrl should match the login endpoint
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Global reference to the Auth coordinator (set during app startup)
_auth: Optional[Auth] = None

_key_resolver: Optional[KeyResolver] = None

_key_failures = LoginAttemptLimiter("Too many invalid API key attempts. Please try again later.")


def set_auth(auth: Auth) -> None:
    """
    Set the global auth instance.

    This should be called during application startup to provide
    the Auth for dependency injection.

    Args:
        auth: Auth instance from DI container
    """
    global _auth
    _auth = auth


def get_auth() -> Auth:
    """
    Get the global auth instance.

    Returns:
        Auth instance

    Raises:
        RuntimeError: If auth not initialized
    """
    if _auth is None:
        raise RuntimeError("Auth not initialized. Call set_auth() during app startup.")
    return _auth


def set_key_resolver(resolver: Optional[KeyResolver]) -> None:
    global _key_resolver
    _key_resolver = resolver


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    return header[len("bearer "):].strip() or None


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _key_error(status_code: int, error: str, message: str, headers: Optional[dict] = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": error, "message": message},
        headers=headers,
    )


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_key(bearer: Optional[str]) -> bool:
    return _key_resolver is not None and bearer is not None and bearer.startswith(KEY_PREFIX)


def _authorize_key(resolved: ResolvedKey, request: Request):
    grant = resolved.grant
    required = required_scopes(request.scope.get("route"), request.method)
    if required is None:
        raise _key_error(
            status.HTTP_403_FORBIDDEN, "key_not_allowed",
            "This route cannot be used with an API key.",
        )
    matched = expand_scopes(grant.scopes) & required
    if not matched:
        needed = " ".join(scope.value for scope in ApiScope if scope in required)
        raise _key_error(
            status.HTTP_403_FORBIDDEN, "insufficient_scope",
            f"This API key needs the '{needed}' scope for this route.",
            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{needed}"'},
        )
    return matched


async def _resolve_key_principal(request: Request, bearer: str) -> Principal:
    resolver = _key_resolver
    client = _client_key(request)
    _key_failures.check(client)
    resolved = await asyncio.to_thread(resolver.resolve, bearer)
    if resolved is None:
        _key_failures.record_failure(client)
        logging.warning("Rejected API key of length %d from %s", len(bearer), client)
        raise _key_error(
            status.HTTP_401_UNAUTHORIZED, "invalid_api_key",
            "The API key is not valid.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    matched = _authorize_key(resolved, request)
    mcp_only = matched == {ApiScope.MCP}
    if mcp_only:
        refusal = await asyncio.to_thread(resolver.mcp_gate, resolved.grant)
        if refusal:
            raise _key_error(status.HTTP_403_FORBIDDEN, "mcp_disabled", refusal)
    try:
        await asyncio.to_thread(resolver.record_use, resolved.grant, "mcp" if mcp_only else "api")
    except Exception:
        logging.exception("Could not record use of API key %s", resolved.grant.display_prefix)
    logging.debug(
        "API key %s (%s) authenticated for user %s via %s",
        resolved.grant.key_id, resolved.grant.display_prefix, resolved.grant.user_id, "mcp" if mcp_only else "api",
    )
    return Principal(user=_key_user(resolved), key=resolved.grant)


def _key_user(resolved: ResolvedKey) -> User:
    user, grant = resolved.user, resolved.grant
    if ApiScope.ADMIN in grant.scopes:
        return replace(user, key=grant)
    return replace(user, key=grant, account_type=AccountType.USER)


async def get_principal(request: Request, token: str = Depends(oauth2_scheme)) -> Principal:
    if _is_key(token):
        return await _resolve_key_principal(request, token)
    user = await asyncio.to_thread(get_auth().get_user_from_token, token)
    if user is None:
        raise _credentials_exception()
    return Principal(user=user)


async def _resolve_media_principal(request: Request) -> Optional[Principal]:
    bearer = _bearer_token(request)
    cookie = media_cookie_token(request)
    if not bearer and not cookie:
        return None
    if _is_key(bearer):
        return await _resolve_key_principal(request, bearer)
    auth = get_auth()
    if bearer:
        user = await asyncio.to_thread(auth.get_user_from_token, bearer)
        if user is not None:
            return Principal(user=user)
    if cookie:
        user = await asyncio.to_thread(auth.get_user_from_token, cookie)
        if user is not None:
            return Principal(user=user)
    return None


async def get_media_viewer(request: Request) -> Optional[User]:
    principal = await _resolve_media_principal(request)
    return principal.user if principal is not None else None


async def require_media_viewer(viewer: Optional[User] = Depends(get_media_viewer)) -> User:
    if viewer is None:
        raise _credentials_exception()
    return viewer


async def get_current_user(principal: Principal = Depends(get_principal)) -> User:
    return principal.user


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Alias dependency for an authenticated user; accounts carry no
    disabled flag, so a valid token is the whole check.
    """
    return current_user


async def get_current_admin_user(current_user: User = Depends(get_current_active_user)) -> User:
    """
    Ensure the authenticated user is an administrator.

    Use this dependency to gate endpoints that manage global, cross-user
    state (LLM provider configs, user assignments, backend management, etc.).

    Args:
        current_user: Currently authenticated active user (injected by FastAPI)

    Returns:
        User object if the user is an administrator

    Raises:
        HTTPException: 403 if the user is not an administrator
    """
    if current_user.account_type != AccountType.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return current_user


SOCKET_TICKET_PARAM = "ticket"
SOCKET_REFUSAL_CODE = 4001


def socket_route_path(websocket: WebSocket) -> str:
    route = websocket.scope.get("route")
    return getattr(route, "path", None) or websocket.scope.get("path", "")


def _socket_required(websocket: WebSocket, scope: ScopeSpec) -> Optional[FrozenSet[ApiScope]]:
    if scope is not None:
        return coerce_scopes(scope)
    return required_scopes(websocket.scope.get("route"), "GET")


def _scope_refusal(required: Optional[FrozenSet[ApiScope]], held) -> Optional[str]:
    if required is None:
        return "This socket cannot be used with an API key"
    if not (expand_scopes(held) & required):
        needed = " ".join(scope.value for scope in ApiScope if scope in required)
        return f"This API key needs the '{needed}' scope for this socket"
    return None


def ensure_socket_scope(principal: Principal, required: Optional[FrozenSet[ApiScope]]) -> None:
    if principal.key is None:
        return
    if required is None:
        raise _key_error(
            status.HTTP_403_FORBIDDEN, "key_not_allowed",
            "This socket cannot be used with an API key.",
        )
    if not (expand_scopes(principal.key.scopes) & required):
        needed = " ".join(scope.value for scope in ApiScope if scope in required)
        raise _key_error(
            status.HTTP_403_FORBIDDEN, "insufficient_scope",
            f"This API key needs the '{needed}' scope for this socket.",
            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{needed}"'},
        )


def _user_for_ticket(entry: TicketEntry, required: Optional[FrozenSet[ApiScope]]) -> Tuple[Optional[User], Optional[str]]:
    if entry.key_id is None:
        user = get_auth().users.get_by_id(entry.user_id)
        return (user, None) if user is not None else (None, "User not found")
    resolver = _key_resolver
    resolved = resolver.resolve_id(entry.key_id) if resolver is not None else None
    if resolved is None or resolved.grant.user_id != entry.user_id:
        return None, "The API key is no longer valid"
    refusal = _scope_refusal(required, expand_scopes(resolved.grant.scopes) & expand_scopes(entry.scopes))
    if refusal:
        return None, refusal
    return _key_user(resolved), None


def _user_for_key_secret(websocket: WebSocket, secret: str, required: Optional[FrozenSet[ApiScope]]) -> Tuple[Optional[User], Optional[str]]:
    client = websocket.client.host if websocket.client else "unknown"
    try:
        _key_failures.check(client)
    except HTTPException:
        return None, "Too many invalid API key attempts"
    resolved = _key_resolver.resolve(secret)
    if resolved is None:
        _key_failures.record_failure(client)
        logging.warning("Rejected API key of length %d on a socket from %s", len(secret), client)
        return None, "The API key is not valid"
    refusal = _scope_refusal(required, resolved.grant.scopes)
    if refusal:
        return None, refusal
    try:
        _key_resolver.record_use(resolved.grant, "api")
    except Exception:
        logging.exception("Could not record use of API key %s", resolved.grant.display_prefix)
    return _key_user(resolved), None


def _authenticate_socket(websocket: WebSocket, scope: ScopeSpec) -> Tuple[Optional[User], Optional[str]]:
    if "token" in websocket.query_params:
        return None, "Credentials in the query string are not accepted"
    required = _socket_required(websocket, scope)
    ticket = websocket.query_params.get(SOCKET_TICKET_PARAM)
    if ticket:
        entry = ws_tickets.redeem(ticket, socket_route_path(websocket))
        if entry is None:
            return None, "Invalid or expired ticket"
        return _user_for_ticket(entry, required)
    bearer = _bearer_token(websocket)
    if not bearer:
        return None, "Authentication required"
    if _is_key(bearer):
        return _user_for_key_secret(websocket, bearer, required)
    return get_auth().authenticate_websocket(bearer)


async def authenticate_websocket(websocket: WebSocket, scope: ScopeSpec = None) -> Tuple[Optional[User], Optional[str]]:
    try:
        return await asyncio.to_thread(_authenticate_socket, websocket, scope)
    except Exception:
        logging.exception("WebSocket authentication failed")
        return None, "Authentication error"


async def refuse_websocket(websocket: WebSocket, reason: Optional[str], code: int = SOCKET_REFUSAL_CODE) -> None:
    logging.warning("WebSocket refused on %s: %s", socket_route_path(websocket), reason)
    try:
        await websocket.accept()
        await websocket.close(code=code, reason=reason or "Authentication failed")
    except Exception as exc:
        logging.error("Could not close a refused WebSocket: %s", exc)
