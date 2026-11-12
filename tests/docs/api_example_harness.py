from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "docs" / "api" / "examples"
PLUGINS = ROOT / "tests" / "e2e" / "plugins"
PLUGIN_ID = "e2e-cloud-fake"
PRESET_ID = "01M3SVA35XT0E7CG5JZTS9P21S"
MODEL_SLUG = "fake~fake~image-1"
BACKEND_NAME = "E2E Fake Cloud"
OWNER = {"username": "owner", "email": "owner@example.com", "password": "pw-123456-Aa"}
PUBLIC_URL = "http://localhost:26730"
KEY_SCOPES = {
    "first_call": ["read"],
    "generate_and_wait": ["read", "generate"],
    "follow_websocket": ["read", "generate"],
    "generate_from_session": ["read", "generate"],
    "upload_and_use": ["read", "generate"],
    "download_history": ["read"],
    "make_kit": ["read", "content"],
}
ORDER = [
    "first_call",
    "generate_and_wait",
    "follow_websocket",
    "upload_and_use",
    "download_history",
    "make_kit",
    "generate_from_session",
]


def configure(scratch: Path) -> None:
    for sub in ("storage", "models", "recipes", "logs"):
        (scratch / sub).mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "POTIONUI_DB_PATH": str(scratch / "db.sqlite"),
        "POTIONUI_STORAGE_PATH": str(scratch / "storage"),
        "POTIONUI_MODELS_DIR": str(scratch / "models"),
        "POTIONUI_RECIPES_DIR": str(scratch / "recipes"),
        "POTIONUI_LOG_DIR": str(scratch / "logs"),
        "POTIONUI_AUTH_SECRET_KEY": "api-example-secret-key-api-example-secret-key",
        "POTIONUI_EXTRA_PLUGIN_DIRS": str(PLUGINS),
        "HOME": str(scratch),
        "USERPROFILE": str(scratch),
        "DEBUG": "false",
    })
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)


class Loopback:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": ("127.0.0.1", 50000)}
        await self.app(scope, receive, send)


def boot():
    from fastapi.testclient import TestClient

    from src.bootstrap.app import create_app

    return TestClient(Loopback(create_app()))


def prepare(scratch: Path) -> None:
    configure(scratch)
    with boot() as client:
        registered = client.post("/api/auth/register", json=OWNER)
        registered.raise_for_status()
        token = registered.json()["data"]["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        client.post("/api/plugins/scan").raise_for_status()
        client.post(f"/api/plugins/{PLUGIN_ID}/enable").raise_for_status()


def wait_until_ready(client, seconds: float = 60.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if client.get("/health").json().get("status") != "starting":
            return
        time.sleep(0.5)


def login(client) -> Dict[str, str]:
    response = client.post("/api/auth/login", data={"username": OWNER["username"], "password": OWNER["password"]})
    response.raise_for_status()
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def set_up_generation(client, session: Dict[str, str]) -> str:
    created = client.post("/api/backends", headers=session, json={
        "name": BACKEND_NAME, "engine": "cloud", "driver": "cloud.fake", "enabled": True, "timeout_seconds": 300,
        "mode": "async", "queue_seconds": 0.0, "duration_seconds": 0.4, "poll_seconds": 0.1, "fail_kind": "", "cost_usd": 0.04,
    })
    created.raise_for_status()
    backend_id = created.json()["data"]["id"]
    client.post(f"/api/cloud/backends/{backend_id}/catalog/refresh", headers=session).raise_for_status()
    client.post(f"/api/cloud/backends/{backend_id}/catalog/enable", headers=session, json={"slugs": [MODEL_SLUG]}).raise_for_status()
    catalog = client.get(f"/api/cloud/backends/{backend_id}/catalog?enabled=true", headers=session).json()
    model = next(item for item in catalog["data"]["items"] if item["slug"] == MODEL_SLUG)
    client.post(f"/api/presets/{PRESET_ID}/install", headers=session).raise_for_status()
    me = client.get("/api/auth/me", headers=session).json()["data"]
    client.post(f"/api/presets/{PRESET_ID}/assign", headers=session, json={"user_ids": [me["id"]]}).raise_for_status()
    return f"model:{model['model_id']}"


def mint_key(client, session: Dict[str, str], scopes: List[str]) -> str:
    created = client.post("/api/keys", headers=session, json={"name": "example", "scopes": scopes, "expires_in_days": None})
    created.raise_for_status()
    return created.json()["data"]["secret"]


class ClientProxy:
    def __init__(self, outer, base_url: str = "", headers: Optional[Dict[str, str]] = None, **_ignored: Any):
        self.outer = outer
        self.base_url = str(base_url).rstrip("/")
        self.headers = dict(headers or {})

    def request(self, method: str, url: str, **kwargs):
        merged = {**self.headers, **(kwargs.pop("headers", None) or {})}
        target = url if url.startswith("http") else self.base_url + url
        return self.outer.request(method, target, headers=merged, **kwargs)

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request("DELETE", url, **kwargs)


class SocketShim:
    outer = None

    def __init__(self, url: str, additional_headers: Optional[Dict[str, str]] = None, **_ignored: Any):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        self.context = SocketShim.outer.websocket_connect(path, headers=dict(additional_headers or {}))
        self.session = None

    def __enter__(self):
        self.session = self.context.__enter__()
        return self

    def __exit__(self, *details):
        return self.context.__exit__(*details)

    def send(self, data: str) -> None:
        self.session.send_text(data)

    def recv(self) -> str:
        return self.session.receive_text()

    def __iter__(self):
        from starlette.websockets import WebSocketDisconnect

        while True:
            try:
                yield self.recv()
            except WebSocketDisconnect:
                return


@contextlib.contextmanager
def relative_paths_under(workdir: Path):
    names = ("write_bytes", "mkdir", "exists", "open")
    originals = {name: getattr(Path, name) for name in names}
    caller = threading.get_ident()

    def anchored(name: str):
        original = originals[name]

        def method(self, *args, **kwargs):
            if threading.get_ident() == caller and not self.is_absolute():
                self = workdir / self
            return original(self, *args, **kwargs)

        return method

    for name in names:
        setattr(Path, name, anchored(name))
    try:
        yield
    finally:
        for name, original in originals.items():
            setattr(Path, name, original)


def run_example(name: str, env: Dict[str, str], argv: List[str], workdir: Path) -> Dict[str, Any]:
    source = EXAMPLES / f"{name}.py"
    saved_env, saved_argv = dict(os.environ), list(sys.argv)
    os.environ.update(env)
    sys.argv = [str(source), *argv]
    output = io.StringIO()
    ok, error = True, ""
    try:
        with contextlib.redirect_stdout(output), relative_paths_under(workdir):
            runpy.run_path(str(source), run_name="__main__")
    except SystemExit as stop:
        ok = stop.code in (None, 0)
        error = "" if ok else f"exit {stop.code}"
    except BaseException:
        ok, error = False, traceback.format_exc()
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        sys.argv = saved_argv
    return {"ok": ok, "output": output.getvalue(), "error": error, "files": sorted(p.name for p in workdir.rglob("*") if p.is_file())}


def write_image(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (64, 64), (200, 30, 30)).save(path, "PNG")


def run(scratch: Path, target: Path) -> None:
    configure(scratch)
    import httpx
    import websockets.sync.client as websocket_client

    results: Dict[str, Any] = {}
    with boot() as client:
        wait_until_ready(client)
        schema = client.get("/openapi.json").json()
        results["documentation"] = {
            "status": {path: client.get(path).status_code for path in ("/docs", "/redoc", "/openapi.json")},
            "schemes": sorted(schema["components"]["securitySchemes"]),
            "start_security": schema["paths"]["/api/generations/start"]["post"]["security"],
        }
        session = login(client)
        model = set_up_generation(client, session)
        form = json.dumps({"model": model, "count": 1, "aspect_ratio": "1:1"})
        sessions_available = "/api/sessions/{session_id}/generate" in client.get("/openapi.json").json()["paths"]
        saved = client.post("/api/sessions/save", headers=session, json={
            "preset_id": PRESET_ID, "name": "Portrait",
            "data": {"txt2img": {"prompt": "a lighthouse", "formData": {"model": model, "count": 1, "aspect_ratio": "1:1"}}},
        })
        session_id = saved.json()["data"]["id"] if saved.status_code == 200 else ""
        image = scratch / "red.png"
        write_image(image)

        original_client, original_connect = httpx.Client, websocket_client.connect
        SocketShim.outer = client

        def make_client(*args, **kwargs):
            return ClientProxy(client, *args, **kwargs)

        httpx.Client = make_client
        websocket_client.connect = SocketShim
        try:
            for name in ORDER:
                if name == "generate_from_session" and not sessions_available:
                    results[name] = {"skipped": "POST /api/sessions/{session_id}/generate is not in this build"}
                    continue
                secret = mint_key(client, session, KEY_SCOPES[name])
                workdir = Path(tempfile.mkdtemp(prefix=f"example-{name}-", dir=scratch))
                env = {
                    "POTIONUI_URL": PUBLIC_URL,
                    "POTIONUI_KEY": secret,
                    "POTIONUI_PRESET": PRESET_ID,
                    "POTIONUI_FORM": form,
                    "POTIONUI_SESSION": session_id,
                    "POTIONUI_INPUT_FIELD": "references",
                    "POTIONUI_MODE": "edit",
                }
                argv = [str(image)] if name == "upload_and_use" else []
                results[name] = run_example(name, env, argv, workdir)
        finally:
            httpx.Client, websocket_client.connect = original_client, original_connect
    target.write_text(json.dumps(results), encoding="utf-8")


def main(argv: List[str]) -> int:
    phase, scratch = argv[0], Path(argv[1])
    if phase == "prepare":
        prepare(scratch)
    else:
        run(scratch, Path(argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
