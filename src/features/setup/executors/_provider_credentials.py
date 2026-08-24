"""Shared provider-registry helpers for setup executors that need to reason
about which provider fetches a recipe artifact and whether it has usable
credentials configured.

Everything here is resolved through the provider's own metadata/settings-
schema surface (`src.features.providers.registry.ProviderRegistry`) - never by
hardcoding a provider id. "Does this provider take a credential" is read off
whichever setting its own `get_settings_schema()` marks `format: "password"`
(the same signal `ProviderRegistry.update_provider_settings`/
`get_provider_current_settings` already use to know which settings are
secrets), and "is one configured" off whether that setting currently has a
value.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.features.setup.executors._async_bridge import run_sync


def resolve_provider_registry(factory=None):
    """Best-effort provider registry resolution - `None` on any failure (no
    provider plugin installed, discovery not wired, ...) so a caller can
    degrade gracefully rather than crash a setup step over an optional
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
    """`{"id", "name", "website", "field_name", "configured"}` for
    `provider_id`, when it declares a password-format setting - `None` when
    the provider is unknown, takes no credential at all, or `registry` itself
    is unavailable. Reads whether a credential is *configured*, never its
    value."""
    if registry is None or not provider_id:
        return None
    metadata = registry.get_provider_metadata(provider_id)
    if metadata is None:
        return None
    schema = registry.get_provider_settings_schema(provider_id) or {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return None
    field_name = next(
        (
            key
            for key, prop in properties.items()
            if isinstance(prop, dict) and prop.get("format") == "password"
        ),
        None,
    )
    if field_name is None:
        return None
    current = registry.get_provider_current_settings(provider_id) or {}
    return {
        "id": provider_id,
        "name": metadata.name,
        "website": metadata.website,
        "field_name": field_name,
        "configured": bool(current.get(field_name)),
    }
