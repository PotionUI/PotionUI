"""Authorization regression test for the comfyui-backend plugin's mounted
route (split out of the main repo's tests/plugins/test_plugin_route_authz.py
when this plugin moved out of tree; form-builder's equivalent class moved
with that plugin too).

Loads the plugin's real `router` and exercises the auth gate through a
FastAPI TestClient. The leaf `get_current_active_user` dependency is
overridden to inject a user of a chosen role, so the real admin gate under
test still runs; requests with no token exercise the real 401 path.

Run from a PotionUI checkout with this plugin linked into
content/plugins/local/ (see this repo's README.md).
"""
import importlib
import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugin_api.identity import get_current_active_user, User, AccountType

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))


def _load_plugin_router(module_alias: str, plugin_id: str):
    """Load `<plugin_id>/backend/api.py` as the real `backend` package (not a
    disconnected `spec_from_file_location` module) so a relative import
    inside it - e.g. `from .preset_import.emit import ...` - resolves the
    same way it does under the real plugin loader
    (src/platform/plugins/loader.py, which also gives the module a proper
    `__package__`). `module_alias` is unused now but kept so existing callers
    don't need updating.
    """
    plugin_dir = os.path.join(REPO_ROOT, "content", "plugins", "marketplace", plugin_id)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    # Import a fresh copy, then put the session's own `backend.*` module
    # objects back: every other test module (and conftest's autouse stubs)
    # is bound to those, and leaving the fresh copies behind would split
    # them apart for the rest of the run.
    original = {k: m for k, m in sys.modules.items() if k == "backend" or k.startswith("backend.")}
    for key in original:
        del sys.modules[key]
    importlib.invalidate_caches()
    try:
        module = importlib.import_module("backend.api")
        return module.router
    finally:
        for key in [k for k in sys.modules if k == "backend" or k.startswith("backend.")]:
            del sys.modules[key]
        sys.modules.update(original)


def _user(account_type: AccountType) -> User:
    return User(
        id="u-1",
        username="u",
        email="u@example.com",
        password_hash="h",
        account_type=account_type,
    )


def _client(router, role: AccountType | None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    if role is not None:
        app.dependency_overrides[get_current_active_user] = lambda: _user(role)
    return TestClient(app)


class TestComfyuiClearVramAdminGate:
    """POST /actions/clear-vram must be admin-only (parity with the ollama sibling)."""

    def setup_method(self):
        self.router = _load_plugin_router("comfyui_backend_api", "comfyui-backend")
        self.path = "/api/plugins/comfyui-backend/actions/clear-vram"
        # Force an unroutable target: the handler's outbound POST /free must
        # never reach a real ComfyUI (a live server on the default
        # 127.0.0.1:8188 would have its VRAM actually cleared by this test,
        # and the success-envelope assertion would flip).
        for route in self.router.routes:
            fn = getattr(route, "endpoint", None)
            if fn is not None and "_get_comfyui_base_url" in getattr(fn, "__globals__", {}):
                fn.__globals__["_get_comfyui_base_url"] = lambda: "http://127.0.0.1:1"

    def test_unauthenticated_rejected(self):
        resp = _client(self.router, role=None).post(self.path)
        assert resp.status_code == 401

    def test_regular_user_forbidden(self):
        resp = _client(self.router, role=AccountType.USER).post(self.path)
        assert resp.status_code == 403

    def test_admin_passes_auth_gate(self):
        # The ComfyUI server is unreachable in tests, so the handler returns its
        # own error envelope with HTTP 200 - reaching it proves the admin gate
        # let the request through (a non-admin would have been 403 before here).
        resp = _client(self.router, role=AccountType.ADMIN).post(self.path)
        assert resp.status_code == 200
        assert resp.json()["success"] is False
