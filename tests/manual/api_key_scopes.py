import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "api_key_scopes.tsv"
HEADER = "method\tpath\tread\twrite\tsource"
SOURCE_PUBLIC = "public"
READ_METHODS = ("GET", "HEAD", "OPTIONS")


def _walk(dependant):
    yield dependant
    for child in dependant.dependencies:
        yield from _walk(child)


def _authenticated(route) -> bool:
    from src.platform.security import current_user as cu

    guards = {cu.get_current_user, cu.get_current_active_user, cu.get_current_admin_user, cu.require_media_viewer, cu.get_media_viewer}
    return any(dep.call in guards for dep in _walk(route.dependant))


def _cell(scopes) -> str:
    from src.platform.security.api_scopes import ApiScope

    if not scopes:
        return "-"
    return "+".join(scope.value for scope in ApiScope if scope in scopes)


def render(app):
    from src.platform.security.api_scopes import policy_source, required_scopes

    rows = []
    for route in app.routes:
        if not hasattr(route, "path"):
            continue
        methods = sorted(getattr(route, "methods", None) or ["WS"])
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            if not hasattr(route, "dependant") or not _authenticated(route):
                rows.append((route.path, method, "-", "-", SOURCE_PUBLIC))
                continue
            scopes = _cell(required_scopes(route, method))
            read, write = (scopes, "-") if method in READ_METHODS else ("-", scopes)
            rows.append((route.path, method, read, write, policy_source(route, method)))
    rows.sort()
    return [HEADER] + [f"{method}\t{path}\t{read}\t{write}\t{source}" for path, method, read, write, source in rows]


def build_app(scratch):
    os.environ["POTIONUI_DB_PATH"] = str(Path(scratch) / "db.sqlite")
    os.environ["HOME"] = scratch
    sys.path.insert(0, str(ROOT))
    from fastapi import FastAPI

    from src.bootstrap.app import run_migrations_sync
    from src.bootstrap.container import build_container
    from src.bootstrap.routers import register_routers

    run_migrations_sync()
    app = FastAPI()
    register_routers(app, build_container())
    return app


def main(argv) -> int:
    with tempfile.TemporaryDirectory(prefix="api-key-scopes-") as scratch:
        lines = render(build_app(scratch))
    text = "\n".join(lines) + "\n"
    if "--default-deny" in argv:
        undeclared = [line for line in lines[1:] if line.endswith("\tdefault_deny")]
        print("\n".join(undeclared))
        return 1 if undeclared else 0
    if "--check" in argv:
        current = GOLDEN.read_text() if GOLDEN.exists() else ""
        if current != text:
            print("tests/golden/api_key_scopes.tsv is out of date: run python tests/manual/api_key_scopes.py")
            return 1
        return 0
    GOLDEN.write_text(text)
    print(f"wrote {GOLDEN} ({len(lines) - 1} routes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
