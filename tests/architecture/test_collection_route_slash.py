"""Guard: a collection route under /api/ must match what the frontend sends.

`src/bootstrap/app.py` builds the FastAPI app with `redirect_slashes=False`,
so a route declared with a trailing slash no longer bridges a frontend call
that omits one - it hard 404s instead. A router whose collection GET/POST is
declared at `"/"` while every sibling route on the same router is declared
without a leading prefix segment is exactly the shape that produced that bug
(`src/features/user_groups/routes.py`, before the fix, returned a 307 for
`POST /api/user-groups` that dropped the request body behind a reverse
proxy). This pins the shape so a new router can't reintroduce it.

Purely static (`ast`), no app boot, no DB - same approach and dependencies as
`test_layering.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTES_FILES = sorted((ROOT / "src" / "features").glob("*/routes.py"))

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# Routers where a route is legitimately declared with a trailing slash even
# though the router has other routes too - audited against every frontend
# caller of that path (grep across frontend/src/lib) and confirmed no
# mismatch exists:
#
# - notifications: every frontend caller of the collection endpoint
#   (list/create/clear) sends the trailing slash itself
#   (frontend/src/lib/services/api/notifications.ts) - route and caller agree.
# - prompt_database: the trailing-slash GET/POST are `include_in_schema=False`
#   back-compat aliases kept alongside the real (no-slash) route; no frontend
#   caller uses them (frontend/src/lib/services/api/prompts.ts calls the
#   no-slash form exclusively).
_KNOWN_TRAILING_SLASH_EXCEPTIONS = {
    "src/features/notifications/routes.py::router GET /api/notifications/",
    "src/features/notifications/routes.py::router POST /api/notifications/",
    "src/features/notifications/routes.py::router DELETE /api/notifications/",
    "src/features/prompt_database/routes.py::router GET /api/prompts/",
    "src/features/prompt_database/routes.py::router POST /api/prompts/",
}


def _string_literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _collect_router_routes(path: Path) -> dict[str, tuple[str, list[tuple[str, str]]]]:
    """router_var -> (prefix, [(method, path), ...]) for every APIRouter()
    assignment and every @router.<method>(...) decorator in the file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    prefixes: dict[str, str] = {}
    routes: dict[str, list[tuple[str, str]]] = {}

    class Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "APIRouter"
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                var_name = node.targets[0].id
                prefix = ""
                for kw in node.value.keywords:
                    if kw.arg == "prefix":
                        prefix = _string_literal(kw.value) or ""
                prefixes[var_name] = prefix
                routes.setdefault(var_name, [])
            self.generic_visit(node)

        def _check_decorators(self, node) -> None:
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in _HTTP_METHODS
                    and isinstance(dec.func.value, ast.Name)
                    and dec.args
                ):
                    router_var = dec.func.value.id
                    route_path = _string_literal(dec.args[0])
                    if route_path is None:
                        continue
                    routes.setdefault(router_var, []).append((dec.func.attr, route_path))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._check_decorators(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._check_decorators(node)
            self.generic_visit(node)

    Visitor().visit(tree)
    return {name: (prefixes.get(name, ""), paths) for name, paths in routes.items()}


def test_no_collection_route_declares_an_unexplained_trailing_slash():
    violations: list[str] = []
    seen_exceptions: set[str] = set()

    for routes_file in ROUTES_FILES:
        rel = routes_file.relative_to(ROOT).as_posix()
        for router_var, (prefix, entries) in _collect_router_routes(routes_file).items():
            for method, route_path in entries:
                full_path = prefix + route_path
                if not full_path.startswith("/api/") or not full_path.endswith("/"):
                    continue
                if full_path == "/api/":
                    continue

                key = f"{rel}::{router_var} {method.upper()} {full_path}"

                # A router with exactly one route, declared at its own root,
                # has no bare-prefix sibling to mismatch against.
                if len(entries) == 1 and route_path in ("/", ""):
                    continue

                if key in _KNOWN_TRAILING_SLASH_EXCEPTIONS:
                    seen_exceptions.add(key)
                    continue

                violations.append(key)

    assert not violations, (
        "Route(s) under /api/ declare a trailing-slash path on a router "
        "that has other routes too. redirect_slashes=False means a frontend "
        "call without the slash now hard 404s instead of redirecting - "
        "either drop the trailing slash (declare \"\" for the collection "
        "route) or, if every frontend caller genuinely sends the slash, add "
        "the route to _KNOWN_TRAILING_SLASH_EXCEPTIONS with the audit that "
        "justifies it:\n" + "\n".join(violations)
    )

    stale = _KNOWN_TRAILING_SLASH_EXCEPTIONS - seen_exceptions
    assert not stale, (
        "_KNOWN_TRAILING_SLASH_EXCEPTIONS lists route(s) that no longer "
        "exist - remove them:\n" + "\n".join(sorted(stale))
    )
