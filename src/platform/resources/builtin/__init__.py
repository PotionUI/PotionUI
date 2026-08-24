"""Builtin @resource providers.

Registered at startup (before plugin sync) so plugin namespace collisions
fail the plugin, not the core.
"""

import logging

from src.platform.resources.builtin.phrasebook_provider import PhrasebookResourceProvider
from src.platform.resources.builtin.form_provider import FormResourceProvider
from src.platform.resources.builtin.generations_provider import GenerationsResourceProvider
from src.platform.resources.builtin.models_provider import ModelsResourceProvider
from src.platform.resources.builtin.presets_provider import PresetsResourceProvider
from src.platform.resources.registry import ResourceRegistry

logger = logging.getLogger(__name__)


def register_builtin_resource_providers(registry: ResourceRegistry) -> None:
    """Register all builtin resource providers."""
    providers = [
        ModelsResourceProvider(),
        PhrasebookResourceProvider(),
        PresetsResourceProvider(),
        GenerationsResourceProvider(),
        FormResourceProvider(),
    ]
    for provider in providers:
        registry.register(provider, source="builtin")
    logger.info(f"Registered {len(providers)} builtin resource providers: {[p.namespace for p in providers]}")


__all__ = [
    "register_builtin_resource_providers",
    "ModelsResourceProvider",
    "PhrasebookResourceProvider",
    "PresetsResourceProvider",
    "GenerationsResourceProvider",
    "FormResourceProvider",
]
