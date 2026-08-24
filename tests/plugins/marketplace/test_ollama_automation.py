"""Ollama plugin: shared unload client + "Unload Ollama model(s)" automation node.

Exercises the real unload logic against a mocked aiohttp client.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import aiohttp
import pytest

# Load the plugin's backend modules under a unique package so their
# `from .client import ...` relative import resolves, without colliding with the
# generic `backend` package name other plugins also use.
_backend_dir = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "ollama" / "backend"
_PKG = "ollama_backend_under_test"
if _PKG not in sys.modules:
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [str(_backend_dir)]
    sys.modules[_PKG] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{_PKG}.{name}", _backend_dir / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[f"{_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


client = _load("client")       # register before automation, whose `.client` import finds it
automation = _load("automation")


class _FakeResp:
    def __init__(self, status=200, json_data=None, text_data="", raise_connect=False):
        self.status = status
        self._json = json_data or {}
        self._text = text_data
        self._raise_connect = raise_connect

    async def __aenter__(self):
        if self._raise_connect:
            raise aiohttp.ClientConnectorError(types.SimpleNamespace(ssl=None, host="h", port=1), OSError("refused"))
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._json

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, ps_models, post_status=200, ps_status=200, connect_error=False):
        self._ps_models = ps_models
        self._post_status = post_status
        self._ps_status = ps_status
        self._connect_error = connect_error
        self.posted = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url):
        if self._connect_error:
            return _FakeResp(raise_connect=True)
        return _FakeResp(status=self._ps_status, json_data={"models": self._ps_models}, text_data="err")

    def post(self, url, json=None):
        self.posted.append(json)
        return _FakeResp(status=self._post_status)


def _patch_session(session):
    return patch.object(client.aiohttp, "ClientSession", return_value=session)


@pytest.mark.asyncio
async def test_unload_all_reports_names_and_freed_vram():
    session = _FakeSession([
        {"name": "llama3:8b", "size_vram": 5_000},
        {"name": "qwen:7b", "size_vram": 3_000},
    ])
    with _patch_session(session):
        result = await client.unload_models("http://x", 5, None)

    assert result["connected"] is True
    assert sorted(result["unloaded"]) == ["llama3:8b", "qwen:7b"]
    assert result["errors"] == []
    assert result["freed_vram_bytes"] == 8_000
    assert {p["keep_alive"] for p in session.posted} == {0}


@pytest.mark.asyncio
async def test_unload_single_named_model_only():
    session = _FakeSession([
        {"name": "llama3:8b", "size_vram": 5_000},
        {"name": "qwen:7b", "size_vram": 3_000},
    ])
    with _patch_session(session):
        result = await client.unload_models("http://x", 5, "qwen:7b")

    assert result["unloaded"] == ["qwen:7b"]
    assert [p["model"] for p in session.posted] == ["qwen:7b"]
    assert result["freed_vram_bytes"] == 3_000


@pytest.mark.asyncio
async def test_unload_named_model_not_loaded_is_soft_noop():
    session = _FakeSession([{"name": "llama3:8b", "size_vram": 5_000}])
    with _patch_session(session):
        result = await client.unload_models("http://x", 5, "absent:1b")

    assert result["connected"] is True
    assert result["unloaded"] == []
    assert session.posted == []  # nothing evicted


@pytest.mark.asyncio
async def test_connection_failure_fails_soft():
    session = _FakeSession([], connect_error=True)
    with _patch_session(session):
        result = await client.unload_models("http://x", 5, None)

    assert result["connected"] is False
    assert result["unloaded"] == []
    assert result["errors"]  # carries a message, does not raise


@pytest.mark.asyncio
async def test_per_model_post_error_is_collected():
    session = _FakeSession([{"name": "llama3:8b", "size_vram": 5_000}], post_status=500)
    with _patch_session(session):
        result = await client.unload_models("http://x", 5, None)

    assert result["connected"] is True
    assert result["unloaded"] == []
    assert result["errors"] == ["llama3:8b: HTTP 500"]


@pytest.mark.asyncio
async def test_node_handler_success_output_contract():
    session = _FakeSession([{"name": "llama3:8b", "size_vram": 5_000}])
    ctx = types.SimpleNamespace(config={"model": ""})
    with patch.object(automation, "get_settings", return_value=("http://x", 5)), _patch_session(session):
        result = await automation.unload_ollama_models(ctx)

    out = result.output
    assert set(out) == {"unloaded", "count", "freed_vram_bytes", "errors", "connected", "success"}
    assert out["success"] is True
    assert out["count"] == 1
    assert out["unloaded"] == ["llama3:8b"]


@pytest.mark.asyncio
async def test_node_handler_unreachable_returns_soft_error_not_exception():
    session = _FakeSession([], connect_error=True)
    ctx = types.SimpleNamespace(config={"model": "llama3:8b"})
    with patch.object(automation, "get_settings", return_value=("http://x", 5)), _patch_session(session):
        result = await automation.unload_ollama_models(ctx)

    assert result.output["connected"] is False
    assert result.output["success"] is False
    assert result.branch == "out"  # never vetoes / crashes the run
