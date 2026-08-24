"""Serves the built SvelteKit SPA (frontend/build) so a single backend
process is "production": no Vite dev server, no reverse proxy in front.

Implemented as a pure-ASGI 404 fallback, NOT a catch-all route. A catch-all
route *matches* every path, which has two corrosive side effects on the
router's native semantics: Starlette's slash redirect (GET /api/keybindings
-> 307 -> /api/keybindings/) never fires because the catch-all counts as a
full match, and any non-GET request to a near-miss path becomes a 405
against the GET-only catch-all instead of redirecting. As a response-phase
fallback, the router keeps every behavior it has without a frontend mounted
- 307 slash redirects for all methods, JSON 404s under /api, real 405s -
and the SPA only serves where routing genuinely found nothing.

No-op when `frontend/build` (or its index.html) doesn't exist, e.g. `npm run
build` was never run - the dev flow (Vite proxying `/api`, `/ws`, `/health` to
this backend) is completely unaffected.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

FRONTEND_BUILD_DIR = Path(__file__).resolve().parents[2] / "frontend" / "build"

# Path segments routed elsewhere (API routers, health check, docs,
# websockets). A 404 under one of these stays a JSON 404 - never
# index.html, which an API client can't tell apart from a real failure.
_RESERVED_FIRST_SEGMENTS = ("api", "ws", "docs", "redoc", "openapi.json", "health")


class _SpaFallback:
    """Replace GET 404s outside the reserved prefixes with the built SPA:
    a real file from build_dir when one exists at the path, index.html
    otherwise (client-side routes resolve in the browser)."""

    def __init__(self, app, build_dir: Path):
        self.app = app
        self.build_dir = build_dir
        self.resolved_build_dir = build_dir.resolve()
        self.index_file = build_dir / "index.html"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return
        path = scope["path"].lstrip("/")
        if path.split("/", 1)[0] in _RESERVED_FIRST_SEGMENTS:
            await self.app(scope, receive, send)
            return

        swallowing = False

        async def send_unless_404(message):
            nonlocal swallowing
            if message["type"] == "http.response.start" and message["status"] == 404:
                swallowing = True
            if not swallowing:
                await send(message)

        await self.app(scope, receive, send_unless_404)
        if swallowing:
            await self._serve_spa(path, scope, receive, send)

    async def _serve_spa(self, path: str, scope, receive, send):
        # Resolve + containment-check before trusting the path is inside
        # build_dir - the path is attacker-controlled and can contain "..",
        # which os.path.join/resolve would otherwise walk out with.
        candidate = (self.build_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(self.resolved_build_dir):
            response = FileResponse(candidate)
        else:
            response = FileResponse(self.index_file)
        await response(scope, receive, send)


def mount_frontend(app: FastAPI, build_dir: Path = FRONTEND_BUILD_DIR) -> None:
    """Register the SPA fallback middleware. Safe to call at any point during
    create_app(); it engages only on responses the router 404'd."""
    index_file = build_dir / "index.html"
    if not index_file.is_file():
        return

    app.add_middleware(_SpaFallback, build_dir=build_dir)
    logger.info(f"Serving built frontend from {build_dir}")
