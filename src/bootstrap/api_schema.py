import json
import sys
from typing import Any, Dict, Iterator, List

from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute

from src.bootstrap.app import _API_DESCRIPTION, run_migrations_sync, tags_metadata
from src.bootstrap.container import build_container
from src.bootstrap.openapi import install_api_key_scheme
from src.bootstrap.routers import register_routers
from src.platform.security import current_user as current_user_module
from src.platform.security.api_scopes import ApiScope, policy_source, required_scopes

SOURCE_PUBLIC = "public"
IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})
SCOPE_ORDER = {scope: index for index, scope in enumerate(ApiScope)}


def build_app() -> FastAPI:
    run_migrations_sync()
    app = FastAPI(title="PotionUI API", description=_API_DESCRIPTION, openapi_tags=tags_metadata)
    install_api_key_scheme(app)
    register_routers(app, build_container())
    return app


def walk(dependant) -> Iterator[Any]:
    yield dependant
    for child in dependant.dependencies:
        yield from walk(child)


def requires_sign_in(route) -> bool:
    if isinstance(route, APIWebSocketRoute):
        return True
    guards = {
        current_user_module.get_principal,
        current_user_module.get_current_user,
        current_user_module.get_current_active_user,
        current_user_module.get_current_admin_user,
        current_user_module.require_media_viewer,
        current_user_module.get_media_viewer,
    }
    return any(node.call in guards for node in walk(route.dependant))


def scope_names(scopes) -> List[str]:
    return [scope.value for scope in sorted(scopes, key=SCOPE_ORDER.get)]


def route_rows(app: FastAPI) -> List[Dict[str, Any]]:
    rows = []
    for route in app.routes:
        if not hasattr(route, "path"):
            continue
        sockets = isinstance(route, APIWebSocketRoute)
        for method in sorted(getattr(route, "methods", None) or ["WS"]):
            if method in IGNORED_METHODS:
                continue
            row = {
                "method": method,
                "path": getattr(route, "path_format", route.path),
                "route_path": route.path,
                "scopes": None,
                "source": SOURCE_PUBLIC,
            }
            if hasattr(route, "dependant") and requires_sign_in(route):
                scopes = required_scopes(route, "GET" if sockets else method)
                row["scopes"] = scope_names(scopes) if scopes else None
                row["source"] = policy_source(route, method)
            rows.append(row)
    rows.sort(key=lambda row: (row["path"], row["method"]))
    return rows


def snapshot() -> Dict[str, Any]:
    app = build_app()
    return {"openapi": app.openapi(), "routes": route_rows(app)}


def main(argv: List[str]) -> int:
    target = argv[0]
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(snapshot(), handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
