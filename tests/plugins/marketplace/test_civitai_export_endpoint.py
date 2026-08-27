"""Endpoint-level tests for the civitai-provider plugin's `/export-png` route.

Mirrors `tests/plugins/test_plugin_route_authz.py`'s recipe: load the real
`router` from disk, mount it on a bare FastAPI app, and override the auth
dependency. The route resolves its collaborators through `get_container()`
(`src.platform.plugins.runtime_registries`), a module-level global normally
set once by `create_app()` - here it's pointed at a fake container for the
duration of each test instead of building the real app/DB.
"""

import importlib.util
import io
import os
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src.features.generation.exceptions import GenerationNotFoundException
from src.platform.plugins import runtime_registries
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_PLUGIN_DIR = os.path.join(REPO_ROOT, "content", "plugins", "marketplace", "civitai-provider")
_BACKEND_DIR = os.path.join(_PLUGIN_DIR, "backend")

# `backend/api.py` does `from .a1111 import ...`, which needs a real parent
# package in `sys.modules` to resolve - a bare `spec_from_file_location` (no
# package) 500s with "attempted relative import with no known parent
# package". Namespaced under a name unlikely to collide with sibling plugins'
# own `backend` packages (see test_civitai_export_a1111.py's docstring).
_PKG_NAME = "civitai_provider_export_test_plugin"
_BACKEND_PKG = f"{_PKG_NAME}.backend"


def _make_package(name, path):
    pkg = types.ModuleType(name)
    pkg.__path__ = [path]
    pkg.__package__ = name
    sys.modules[name] = pkg
    return pkg


def _load_plugin_router():
    if _PKG_NAME not in sys.modules:
        _make_package(_PKG_NAME, _PLUGIN_DIR)
        _make_package(_BACKEND_PKG, _BACKEND_DIR)

    full_name = f"{_BACKEND_PKG}.api"
    spec = importlib.util.spec_from_file_location(full_name, os.path.join(_BACKEND_DIR, "api.py"))
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _BACKEND_PKG
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module.router


def _user(user_id: str = "u-owner") -> User:
    return User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        password_hash="h",
        account_type=AccountType.USER,
    )


class _FakeGenerationHistoryFacade:
    """Fake standing in for `GenerationHistoryFacade`: one generation, owned
    by `owner_id`, raising like the real one on any ownership mismatch. Also
    exposes `.query`, mirroring the real manager's `query` property
    (`src/features/generation/history_facade.py`) - `export_png` calls
    `get_params` through that seam, not as a direct forwarder."""

    def __init__(self, owner_id, files, parameters, models, quantity=None, parameters_by_index=None):
        self.owner_id = owner_id
        self.files = files
        self.parameters = parameters
        self.models = models
        self.quantity = quantity
        self.parameters_by_index = parameters_by_index
        self.query = self

    def _check_ownership(self, user_id):
        if user_id != self.owner_id:
            raise GenerationNotFoundException("Generation not found")

    def get_by_id(self, generation_id, user_id, include_files=True):
        self._check_ownership(user_id)
        # Mirrors the real `history_query.get_by_id()` shape: there is no
        # top-level "quantity" key on the generation dict (see
        # `Generation.to_dict()`) - the submitted quantity only lives nested
        # under "form_data". A fake that puts "quantity" at the top level
        # (as this one used to) hides the exact bug this endpoint had: it
        # read `generation.get("quantity")` and always got None.
        gen = {"id": generation_id, "files": self.files, "form_data": {}}
        if self.quantity is not None:
            gen["form_data"]["quantity"] = self.quantity
        return gen

    def get_params(self, generation_id, index, user_id):
        self._check_ownership(user_id)
        parameters = self.parameters
        if self.parameters_by_index is not None:
            parameters = self.parameters_by_index.get(index, {})
        return {
            "generation_id": generation_id,
            "index": index,
            "parameters": parameters,
            "models": self.models,
        }


class _FakeMediaStore:
    def __init__(self, file_path):
        self.file_path = file_path

    def get_generation_media(self, generation_id, filename, user_id=None, size=None, animated=False):
        if not os.path.exists(self.file_path):
            raise ValueError("File not found on disk")
        return type("MediaResult", (), {"file_path": self.file_path})()


class _FakeContainer:
    def __init__(self, history_facade, media_store):
        self.generation_history_facade = history_facade
        self.media_store = media_store


@pytest.fixture
def png_path(tmp_path):
    path = tmp_path / "image_000.png"
    Image.new("RGB", (4, 4), color=(0, 128, 255)).save(path, format="PNG")
    return str(path)


@pytest.fixture
def install_container(monkeypatch):
    """Point the module-global container at a fake, and guarantee it's
    cleared afterward regardless of test outcome."""

    def _install(container):
        monkeypatch.setattr(runtime_registries, "_container", container)

    yield _install


@pytest.fixture
def client():
    router = _load_plugin_router()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_active_user] = lambda: _user()
    return TestClient(app)


class TestExportPngEndpoint:
    def test_success_returns_png_with_embedded_parameters(self, client, png_path, install_container):
        history = _FakeGenerationHistoryFacade(
            owner_id="u-owner",
            files=[{"file_path": f"gen-1/{os.path.basename(png_path)}", "file_type": "IMAGE"}],
            parameters={"positive_prompt": "a fox", "steps": 20},
            models=[],
        )
        install_container(_FakeContainer(history, _FakeMediaStore(png_path)))

        resp = client.get("/api/plugins/civitai-provider/export-png?generation_id=gen-1&index=0")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert "attachment" in resp.headers["content-disposition"]

        with Image.open(io.BytesIO(resp.content)) as img:
            assert img.text["parameters"] == "a fox\nSteps: 20, Size: 4x4"

    def test_derived_file_falls_back_to_source_params_with_real_size(
        self, client, png_path, install_container
    ):
        base = os.path.basename(png_path)
        history = _FakeGenerationHistoryFacade(
            owner_id="u-owner",
            files=[
                {"file_path": f"gen-1/{base}", "file_type": "IMAGE"},
                {"file_path": f"gen-1/{base}", "file_type": "IMAGE"},
            ],
            parameters={},
            models=[],
            quantity=1,
            parameters_by_index={
                0: {"positive_prompt": "a fox", "steps": 20, "resolution": "1080x1920"},
                1: {},
            },
        )
        install_container(_FakeContainer(history, _FakeMediaStore(png_path)))

        resp = client.get("/api/plugins/civitai-provider/export-png?generation_id=gen-1&index=1")

        assert resp.status_code == 200
        with Image.open(io.BytesIO(resp.content)) as img:
            assert img.text["parameters"] == "a fox\nSteps: 20, Size: 4x4"

    def test_derived_file_fallback_reads_quantity_from_form_data_not_top_level(
        self, client, png_path, install_container
    ):
        """Pins the actual production bug: `history_query.get_by_id()` never
        sets a top-level "quantity" key (see `Generation.to_dict()` -
        `src/features/generation/records.py`) - quantity only lives nested
        under `form_data["quantity"]`, exactly as this fake now models it.
        A regression back to `generation.get("quantity")` makes this 200 with
        an empty `parameters` dict instead of falling back to the source
        image's params."""
        base = os.path.basename(png_path)
        quantity = 2
        history = _FakeGenerationHistoryFacade(
            owner_id="u-owner",
            files=[{"file_path": f"gen-1/{base}", "file_type": "IMAGE"} for _ in range(2 * quantity)],
            parameters={},
            models=[],
            quantity=quantity,
            parameters_by_index={
                0: {"positive_prompt": "a fox", "steps": 20, "resolution": "1080x1920"},
                1: {"positive_prompt": "a wolf", "steps": 20, "resolution": "1080x1920"},
                2: {},
                3: {},
            },
        )
        install_container(_FakeContainer(history, _FakeMediaStore(png_path)))

        # index 3 (second file of the derived/enhanced batch) -> source index
        # 3 - quantity(2) = 1 ("a wolf"), not index 0's "a fox".
        resp = client.get("/api/plugins/civitai-provider/export-png?generation_id=gen-1&index=3")

        assert resp.status_code == 200
        with Image.open(io.BytesIO(resp.content)) as img:
            assert img.text["parameters"] == "a wolf\nSteps: 20, Size: 4x4"

    def test_other_users_generation_is_404_not_403(self, client, png_path, install_container):
        history = _FakeGenerationHistoryFacade(
            owner_id="someone-else",
            files=[{"file_path": f"gen-1/{os.path.basename(png_path)}", "file_type": "IMAGE"}],
            parameters={"positive_prompt": "a fox"},
            models=[],
        )
        install_container(_FakeContainer(history, _FakeMediaStore(png_path)))

        resp = client.get("/api/plugins/civitai-provider/export-png?generation_id=gen-1&index=0")

        assert resp.status_code == 404

    def test_missing_generation_is_404(self, client, png_path, install_container):
        history = _FakeGenerationHistoryFacade(
            owner_id="u-owner", files=[], parameters={}, models=[]
        )
        install_container(_FakeContainer(history, _FakeMediaStore(png_path)))

        resp = client.get("/api/plugins/civitai-provider/export-png?generation_id=missing&index=0")

        assert resp.status_code == 404

    def test_index_out_of_range_is_404(self, client, png_path, install_container):
        history = _FakeGenerationHistoryFacade(
            owner_id="u-owner",
            files=[{"file_path": f"gen-1/{os.path.basename(png_path)}", "file_type": "IMAGE"}],
            parameters={"positive_prompt": "a fox"},
            models=[],
        )
        install_container(_FakeContainer(history, _FakeMediaStore(png_path)))

        resp = client.get("/api/plugins/civitai-provider/export-png?generation_id=gen-1&index=5")

        assert resp.status_code == 404

    def test_video_file_is_rejected(self, client, png_path, install_container):
        history = _FakeGenerationHistoryFacade(
            owner_id="u-owner",
            files=[{"file_path": "gen-1/clip.mp4", "file_type": "VIDEO"}],
            parameters={"positive_prompt": "a fox"},
            models=[],
        )
        install_container(_FakeContainer(history, _FakeMediaStore(png_path)))

        resp = client.get("/api/plugins/civitai-provider/export-png?generation_id=gen-1&index=0")

        assert resp.status_code == 400

    def test_unauthenticated_rejected(self, png_path, install_container):
        history = _FakeGenerationHistoryFacade(
            owner_id="u-owner",
            files=[{"file_path": f"gen-1/{os.path.basename(png_path)}", "file_type": "IMAGE"}],
            parameters={"positive_prompt": "a fox"},
            models=[],
        )
        install_container(_FakeContainer(history, _FakeMediaStore(png_path)))

        router = _load_plugin_router()
        app = FastAPI()
        app.include_router(router)
        resp = TestClient(app).get("/api/plugins/civitai-provider/export-png?generation_id=gen-1&index=0")

        assert resp.status_code == 401

    def test_missing_file_on_disk_is_404(self, client, tmp_path, install_container):
        missing_path = str(tmp_path / "gone.png")
        history = _FakeGenerationHistoryFacade(
            owner_id="u-owner",
            files=[{"file_path": "gen-1/gone.png", "file_type": "IMAGE"}],
            parameters={"positive_prompt": "a fox"},
            models=[],
        )
        install_container(_FakeContainer(history, _FakeMediaStore(missing_path)))

        resp = client.get("/api/plugins/civitai-provider/export-png?generation_id=gen-1&index=0")

        assert resp.status_code == 404
