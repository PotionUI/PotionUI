"""Automation node: unload Ollama model(s) to free VRAM for a generation."""

import logging

from src.plugin_api import NodeResult

from .client import get_settings, unload_models

logger = logging.getLogger(__name__)


async def unload_ollama_models(ctx) -> NodeResult:
    """Evict Ollama models. An empty `model` config unloads every loaded model;
    a name unloads just that one. Fails soft - Ollama being unreachable is
    reported in the output (`connected`/`errors`), it does not raise."""
    model = (ctx.config.get("model") or "").strip()
    host, timeout = get_settings()

    result = await unload_models(host, timeout, model or None)

    unloaded = result.get("unloaded", [])
    errors = result.get("errors", [])
    connected = result.get("connected", False)
    logger.info("[OLLAMA] automation unload: unloaded=%s errors=%s connected=%s",
                unloaded, errors, connected)

    return NodeResult(output={
        "unloaded": unloaded,
        "count": len(unloaded),
        "freed_vram_bytes": result.get("freed_vram_bytes", 0),
        "errors": errors,
        "connected": connected,
        "success": connected and not errors,
    })
