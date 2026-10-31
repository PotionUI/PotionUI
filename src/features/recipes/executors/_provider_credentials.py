from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from src.features.recipes.executors._async_bridge import run_sync


def resolve_provider_registry(factory=None):
    """Best-effort provider registry resolution - `None` on any failure (no
    provider plugin installed, discovery not wired, ...) so a caller can
    degrade gracefully rather than crash a recipe step over an optional
    lookup. `factory` is the injectable seam tests use; defaults to the real
    module-level registry's async discovery."""
    try:
        if factory is not None:
            return run_sync(factory())
        from src.features.providers.registry import ensure_providers_discovered

        return run_sync(ensure_providers_discovered())
    except Exception:
        return None


def credential_prompt_for_provider(registry: Any, provider_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if registry is None or not provider_id:
        return None
    metadata = registry.get_provider_metadata(provider_id)
    if metadata is None:
        return None
    secret_keys = registry.get_provider_secret_keys(provider_id)
    if not secret_keys:
        return None
    field_name = secret_keys[0]
    current = registry.get_provider_current_settings(provider_id) or {}
    return {
        "id": provider_id,
        "name": metadata.name,
        "website": metadata.website,
        "field_name": field_name,
        "configured": bool(current.get(field_name)),
    }


def _sources(artifacts: List[Any]) -> List[str]:
    return sorted({(a.provider_hint or {}).get("source") for a in artifacts if (a.provider_hint or {}).get("source")})


def credential_prompts(get_registry: Callable[[], Any], artifacts: List[Any]) -> List[Dict[str, Any]]:
    sources = _sources(artifacts)
    if not sources:
        return []
    registry = get_registry()
    if registry is None:
        return []
    prompts = []
    for source in sources:
        info = credential_prompt_for_provider(registry, source)
        if info and not info["configured"]:
            prompts.append(info)
    return prompts


def gated_warnings(get_registry: Callable[[], Any], artifacts: List[Any]) -> List[str]:
    gated = [a for a in artifacts if a.gated]
    if not gated:
        return []
    registry = get_registry()
    if registry is None:
        return []
    warnings: List[str] = []
    for artifact in gated:
        info = credential_prompt_for_provider(registry, (artifact.provider_hint or {}).get("source"))
        if not info or info["configured"]:
            continue
        sentence = f"{artifact.display_name or artifact.filename} is gated on {info['name']}: accept the licence"
        if artifact.license_url:
            sentence += f" at {artifact.license_url}"
        warnings.append(sentence + " and add your access token in Admin -> Plugins.")
    return warnings
