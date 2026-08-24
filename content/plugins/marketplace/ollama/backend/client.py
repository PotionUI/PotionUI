"""Shared Ollama daemon client.

Used by both the Clear-VRAM quick-action route (`api.py`) and the
"Unload Ollama model(s)" automation node (`automation.py`) so they share one
copy of the host/timeout plumbing and the unload strategy.
"""

import logging
from typing import Any, Dict, List, Optional

import aiohttp

from src.plugin_api import PluginRepository

logger = logging.getLogger(__name__)

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 10


class OllamaError(Exception):
    """Ollama answered but not with what we asked for (non-200 from /api/ps)."""


def get_settings() -> tuple[str, int]:
    """(host, timeout) from the plugin's saved settings, falling back to defaults."""
    repo = PluginRepository()
    settings = repo.get_plugin_settings("ollama")
    host = DEFAULT_HOST
    timeout = DEFAULT_TIMEOUT
    for s in settings:
        if s.setting_key == "ollama_host" and s.setting_value:
            host = s.setting_value.rstrip("/")
        elif s.setting_key == "connection_timeout" and s.setting_value:
            try:
                timeout = int(s.setting_value)
            except ValueError:
                pass
    return host, timeout


async def _list_loaded(session: aiohttp.ClientSession, host: str) -> List[Dict[str, Any]]:
    async with session.get(f"{host}/api/ps") as resp:
        if resp.status != 200:
            text = await resp.text()
            raise OllamaError(f"Ollama /api/ps returned {resp.status}: {text}")
        data = await resp.json()
        return [m for m in data.get("models", []) if m.get("name")]


async def unload_models(host: str, timeout: int, model: Optional[str] = None) -> Dict[str, Any]:
    """Evict loaded Ollama models by re-issuing a generate with keep_alive=0.

    `model=None` unloads every currently loaded model; a name unloads just that
    one (a no-op, reported as success, if it wasn't loaded). Fails soft: a
    connection failure or a bad /api/ps response comes back as
    `connected`/`errors`, never an exception, so a caller (e.g. an automation
    run gating a generation) is never killed by Ollama being down.

    Returns `{unloaded, errors, freed_vram_bytes, connected}`.
    """
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    unloaded: List[str] = []
    errors: List[str] = []
    freed = 0

    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            loaded = await _list_loaded(session, host)
            by_name = {m["name"]: m for m in loaded}

            if model:
                if model not in by_name:
                    return {"unloaded": [], "errors": [], "freed_vram_bytes": 0, "connected": True}
                targets = [model]
            else:
                targets = list(by_name.keys())

            for name in targets:
                try:
                    async with session.post(
                        f"{host}/api/generate", json={"model": name, "keep_alive": 0}
                    ) as r:
                        if r.status == 200:
                            unloaded.append(name)
                            freed += int(by_name.get(name, {}).get("size_vram") or 0)
                        else:
                            errors.append(f"{name}: HTTP {r.status}")
                except Exception as e:
                    errors.append(f"{name}: {e}")

    except aiohttp.ClientConnectorError:
        return {"unloaded": [], "errors": [f"Cannot connect to Ollama at {host}"],
                "freed_vram_bytes": 0, "connected": False}
    except OllamaError as e:
        return {"unloaded": [], "errors": [str(e)], "freed_vram_bytes": 0, "connected": True}
    except Exception as e:
        logger.error("Failed to unload Ollama models: %s", e)
        return {"unloaded": unloaded, "errors": errors + [str(e)],
                "freed_vram_bytes": freed, "connected": True}

    return {"unloaded": unloaded, "errors": errors, "freed_vram_bytes": freed, "connected": True}
