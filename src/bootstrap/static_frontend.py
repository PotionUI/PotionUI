"""Serves the built SvelteKit SPA (frontend/build) so a single backend
process is "production": no Vite dev server, no reverse proxy in front.

`mount_frontend()` must be called after every API router (and the plugin
routers) is registered - the catch-all route below only sees requests that
matched no earlier route, so registration order is what keeps it from
shadowing real API 404s.

No-op when `frontend/build` (or its index.html) doesn't exist, e.g. `npm run
build` was never run - the dev flow (Vite proxying `/api`, `/ws`, `/health` to
this backend) is completely unaffected.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

FRONTEND_BUILD_DIR = Path(__file__).resolve().parents[2] / "frontend" / "build"

# Path segments already routed elsewhere (API routers, health check, docs,
# websockets). A request under one of these that reaches the catch-all
# below matched no real route, so it must come back as a JSON 404 - never
# index.html, which an API client can't tell apart from a real failure.
_RESERVED_FIRST_SEGMENTS = ("api", "ws", "docs", "redoc", "openapi.json", "health")


def mount_frontend(app: FastAPI, build_dir: Path = FRONTEND_BUILD_DIR) -> None:
    """Register the catch-all SPA route. See module docstring for ordering."""
    index_file = build_dir / "index.html"
    if not index_file.is_file():
        return

    resolved_build_dir = build_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        if full_path.split("/", 1)[0] in _RESERVED_FIRST_SEGMENTS:
            raise HTTPException(status_code=404)

        # Resolve + containment-check before trusting the path is inside
        # build_dir - `full_path` is attacker-controlled and can contain
        # "..", which os.path.join/resolve would otherwise walk out with.
        candidate = (build_dir / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(resolved_build_dir):
            return FileResponse(candidate)

        # SPA fallback: any other path (a client-side route like /generate,
        # a file that doesn't exist) resolves in the browser via index.html.
        return FileResponse(index_file)

    logger.info(f"Serving built frontend from {build_dir}")
