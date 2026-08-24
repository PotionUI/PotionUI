"""Regression test for the civitai-provider `/export-png` route against a
REAL `GenerationHistoryManager` (not the hand-rolled fake in
`test_civitai_export_endpoint.py`).

Since commit 403261a8 (2026-08-20) removed `GenerationHistoryManager.get_params`
in favor of the `.query` property forwarding to `GenerationHistoryQuery`, the
route's two `history.get_params(...)` calls raised `AttributeError` on every
request - masked as a 404 "Generation not found" by a bare `except Exception`.
`test_civitai_export_endpoint.py`'s fake implemented `get_params` directly on
the manager stand-in, so it exercised the wrong seam and never caught this;
this test drives the real manager the DI container actually hands the plugin.
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from tests.fixtures.persistence_base import PersistenceTestBase
from src.features.generation.history_manager import GenerationHistoryManager
from src.features.generation.repository import GenerationRepository
from src.platform.plugins import runtime_registries
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_PLUGIN_DIR = os.path.join(REPO_ROOT, "content", "plugins", "marketplace", "civitai-provider")
_BACKEND_DIR = os.path.join(_PLUGIN_DIR, "backend")

# Namespaced separately from test_civitai_export_endpoint.py's loader so the
# two test modules never fight over the same sys.modules entry.
_PKG_NAME = "civitai_provider_real_history_test_plugin"
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


class _FakeMediaManager:
    """The route's third collaborator, media serving, is unrelated to the
    get_params regression under test - faked exactly like
    test_civitai_export_endpoint.py fakes it."""

    def __init__(self, file_path):
        self.file_path = file_path

    def get_generation_media(self, generation_id, filename, user_id=None, size=None, animated=False):
        if not os.path.exists(self.file_path):
            raise ValueError("File not found on disk")
        return type("MediaResult", (), {"file_path": self.file_path})()


class _RealHistoryContainer:
    def __init__(self, history_manager, media_manager):
        self.generation_history_manager = history_manager
        self.media_manager = media_manager


class TestExportPngAgainstRealHistoryManager(PersistenceTestBase):

    def setUp(self):
        super().setUp()

        # These repositories bind `db` at import time - redirect each one at
        # the module level, same as test_history_query_provenance.py does.
        import src.features.generation.parameter_repository
        import src.features.generation.model_repository
        import src.features.generation.source_repository
        src.features.generation.parameter_repository.db = self.db
        src.features.generation.model_repository.db = self.db
        src.features.generation.source_repository.db = self.db

        self.history = GenerationHistoryManager(
            generation_repo=GenerationRepository(),
            file_service=None,
            plugin_registry=None,
        )

        self.user_id = self.create_test_user(user_id="u-owner")

    def _user(self) -> User:
        return User(
            id=self.user_id,
            username=self.user_id,
            email=f"{self.user_id}@example.com",
            password_hash="h",
            account_type=AccountType.USER,
        )

    def _client(self):
        router = _load_plugin_router()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_active_user] = lambda: self._user()
        return TestClient(app)

    def _png_path(self, tmpdir):
        path = os.path.join(tmpdir, "image_000.png")
        Image.new("RGB", (4, 4), color=(0, 128, 255)).save(path, format="PNG")
        return path

    def test_export_png_survives_the_real_manager_and_embeds_parameters(self):
        """Pins the actual crash: before the fix, `history.get_params(...)`
        raised `AttributeError` on the real `GenerationHistoryManager` (the
        forwarder was removed) and the route's bare `except Exception`
        turned that into a 404. A regression back to calling `get_params`
        directly on the manager reproduces the same crash here."""
        generation_id = "gen-real-1"
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generations (id, user_id, preset_id, status, form_data) "
                "VALUES (?, ?, ?, ?, ?)",
                (generation_id, self.user_id, "test-preset", "completed", "{}"),
            )
            file_id = "file-real-1"
            cursor.execute(
                "INSERT INTO files (id, file_path, file_type, user_id) VALUES (?, ?, ?, ?)",
                (file_id, f"{generation_id}/image_000.png", "IMAGE", self.user_id),
            )
            cursor.execute(
                "INSERT INTO generation_files (id, generation_id, file_id) VALUES (?, ?, ?)",
                ("gf-real-1", generation_id, file_id),
            )

        from src.features.generation.parameter_repository import GenerationParameterRepository
        GenerationParameterRepository().create_batch(generation_id, "positive_prompt", ["a fox"])
        GenerationParameterRepository().create_batch(generation_id, "steps", [20])

        png_path = self._png_path(tempfile.mkdtemp())
        runtime_registries._container = _RealHistoryContainer(self.history, _FakeMediaManager(png_path))
        try:
            client = self._client()
            resp = client.get(f"/api/plugins/civitai-provider/export-png?generation_id={generation_id}&index=0")
        finally:
            runtime_registries._container = None

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "image/png"

        with Image.open(io.BytesIO(resp.content)) as img:
            parameters_text = img.text["parameters"]
        assert "a fox" in parameters_text
        assert "Steps: 20" in parameters_text
