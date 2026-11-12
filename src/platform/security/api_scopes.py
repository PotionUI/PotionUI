from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet, Iterable, Optional, Union

from fastapi import Depends


class ApiScope(str, Enum):
    READ = "read"
    GENERATE = "generate"
    CONTENT = "content"
    ADMIN = "admin"
    MCP = "mcp"


_IMPLIED = {
    ApiScope.ADMIN: frozenset({ApiScope.READ, ApiScope.GENERATE, ApiScope.CONTENT}),
}

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

ScopeSpec = Union[None, ApiScope, Iterable[ApiScope]]


def expand_scopes(scopes: Iterable[ApiScope]) -> FrozenSet[ApiScope]:
    expanded = set(scopes)
    for scope in list(expanded):
        expanded |= _IMPLIED.get(scope, frozenset())
    return frozenset(expanded)


def parse_scopes(values: Iterable[str]) -> FrozenSet[ApiScope]:
    return frozenset(ApiScope(value) for value in values)


def _coerce(spec: ScopeSpec) -> Optional[FrozenSet[ApiScope]]:
    if spec is None:
        return None
    if isinstance(spec, ApiScope):
        return frozenset({spec})
    scopes = frozenset(spec)
    return scopes or None


def coerce_scopes(spec: ScopeSpec) -> Optional[FrozenSet[ApiScope]]:
    return _coerce(spec)


@dataclass(frozen=True)
class KeyAccess:
    read: Optional[FrozenSet[ApiScope]]
    write: Optional[FrozenSet[ApiScope]]
    session_only: bool = False


def _marker(access: KeyAccess):
    async def key_access_marker() -> None:
        return None

    key_access_marker.key_access = access
    return Depends(key_access_marker)


def key_access(read: ScopeSpec = None, write: ScopeSpec = None):
    return _marker(KeyAccess(read=_coerce(read), write=_coerce(write)))


def key_scope(*scopes: Union[ApiScope, Iterable[ApiScope]]):
    flat = set()
    for scope in scopes:
        flat |= {scope} if isinstance(scope, ApiScope) else set(scope)
    spec = frozenset(flat) or None
    return _marker(KeyAccess(read=spec, write=spec))


SESSION_ONLY = _marker(KeyAccess(read=None, write=None, session_only=True))


@dataclass(frozen=True)
class _RoutePolicy:
    access: Optional[KeyAccess]
    admin_dependency: bool


_POLICY_ATTR = "_key_policy"


def _depends_on_admin(dependant) -> bool:
    from src.platform.security.current_user import get_current_admin_user

    stack = [dependant]
    while stack:
        current = stack.pop()
        if current.call is get_current_admin_user:
            return True
        stack.extend(current.dependencies)
    return False


def _policy_for(route) -> _RoutePolicy:
    cached = getattr(route, _POLICY_ATTR, None)
    if cached is not None:
        return cached
    access = None
    for dependency in getattr(route, "dependencies", None) or []:
        declared = getattr(getattr(dependency, "dependency", None), "key_access", None)
        if isinstance(declared, KeyAccess):
            access = declared
    dependant = getattr(route, "dependant", None)
    policy = _RoutePolicy(
        access=access,
        admin_dependency=bool(dependant is not None and _depends_on_admin(dependant)),
    )
    setattr(route, _POLICY_ATTR, policy)
    return policy


def required_scopes(route, method: str) -> Optional[FrozenSet[ApiScope]]:
    if route is None:
        return None
    policy = _policy_for(route)
    if policy.access is not None and policy.access.session_only:
        return None
    if policy.admin_dependency:
        return frozenset({ApiScope.ADMIN})
    if policy.access is None:
        return None
    return policy.access.read if method.upper() in _READ_METHODS else policy.access.write


SOURCE_SESSION_ONLY = "session_only"
SOURCE_ADMIN_DEPENDENCY = "admin_dependency"
SOURCE_MARKER = "marker"
SOURCE_DEFAULT_DENY = "default_deny"


def policy_source(route, method: str) -> str:
    policy = _policy_for(route)
    if policy.access is not None and policy.access.session_only:
        return SOURCE_SESSION_ONLY
    if policy.admin_dependency:
        return SOURCE_ADMIN_DEPENDENCY
    if policy.access is None:
        return SOURCE_DEFAULT_DENY
    return SOURCE_MARKER
