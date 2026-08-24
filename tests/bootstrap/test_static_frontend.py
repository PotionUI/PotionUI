"""mount_frontend() serves the built SPA without shadowing API 404s.

Uses a bare FastAPI app with one real "API" route (mirroring the real
create_app() ordering: routers first, mount_frontend() last) and a fake
frontend/build directory under tmp_path - no DB, no migrations, no real
frontend build.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bootstrap.static_frontend import mount_frontend


@pytest.fixture
def build_dir(tmp_path: Path) -> Path:
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("<html><body>SPA shell</body></html>")
    assets = build / "_app" / "immutable"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("console.log('hi');")
    return build


def _app_with_api_route() -> FastAPI:
    app = FastAPI()

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    return app


def test_root_serves_index_html(build_dir):
    app = _app_with_api_route()
    mount_frontend(app, build_dir=build_dir)
    client = TestClient(app)

    resp = client.get("/")

    assert resp.status_code == 200
    assert "SPA shell" in resp.text
    assert resp.headers["content-type"].startswith("text/html")


def test_unmatched_api_path_is_a_json_404_not_the_spa_shell(build_dir):
    app = _app_with_api_route()
    mount_frontend(app, build_dir=build_dir)
    client = TestClient(app)

    resp = client.get("/api/nonexistent")

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert "SPA shell" not in resp.text


def test_real_api_route_still_works_once_the_frontend_is_mounted(build_dir):
    app = _app_with_api_route()
    mount_frontend(app, build_dir=build_dir)
    client = TestClient(app)

    resp = client.get("/api/ping")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_client_side_route_falls_back_to_index_html(build_dir):
    app = _app_with_api_route()
    mount_frontend(app, build_dir=build_dir)
    client = TestClient(app)

    resp = client.get("/generate/some-preset")

    assert resp.status_code == 200
    assert "SPA shell" in resp.text


def test_a_real_static_asset_is_served_from_disk(build_dir):
    app = _app_with_api_route()
    mount_frontend(app, build_dir=build_dir)
    client = TestClient(app)

    resp = client.get("/_app/immutable/app.js")

    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_path_traversal_outside_build_dir_falls_back_to_index_html_not_the_real_file(build_dir, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me")
    app = _app_with_api_route()
    mount_frontend(app, build_dir=build_dir)
    client = TestClient(app)

    resp = client.get("/../secret.txt")

    assert resp.status_code == 200
    assert "do not serve me" not in resp.text
    assert "SPA shell" in resp.text


def test_no_build_dir_is_a_no_op(tmp_path):
    app = _app_with_api_route()
    mount_frontend(app, build_dir=tmp_path / "does-not-exist")
    client = TestClient(app)

    resp = client.get("/")

    assert resp.status_code == 404
    assert client.get("/api/ping").status_code == 200
