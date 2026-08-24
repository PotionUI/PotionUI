"""
Prompt importer registry.

This module provides the single declaration point for a prompt-library import
source: its id, the label shown in the Import menu, the plugin frontend
component that renders its modal, and the backend object that runs the
import. There are no builtin importers - the registry starts empty, and the
frontend's Import button is only shown once a plugin has registered one.

Plugins extend the import menu by registering additional
`PromptImporterDefinition`s on the shared `prompt_importer_registry` singleton
when they are enabled, and by having them removed via `unregister_source` when
disabled.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class DuplicatePromptImporterError(ValueError):
    """Raised when registering an importer id that is already registered."""


@dataclass(frozen=True)
class PromptImporterDefinition:
    """Declaration for a single prompt importer."""

    importer_id: str
    label: str
    frontend_component: str
    # A `src.plugin_api.prompts.PromptImporter` instance - typed `Any` here
    # because `src.platform` must not import `src.plugin_api`/`src.features`.
    backend: Any
    source: str = "core"


class PromptImporterRegistry:
    """Registry mapping importer id -> `PromptImporterDefinition`."""

    def __init__(self):
        self._by_id: Dict[str, PromptImporterDefinition] = {}

    def register(self, definition: PromptImporterDefinition) -> None:
        """Register a prompt importer. Raises `DuplicatePromptImporterError` on id collision."""
        if definition.importer_id in self._by_id:
            raise DuplicatePromptImporterError(
                f"Prompt importer already registered: '{definition.importer_id}'"
            )
        self._by_id[definition.importer_id] = definition

    def unregister_source(self, source: str) -> None:
        """Remove every importer registered by `source` (e.g. a plugin id)."""
        for importer_id in [
            importer_id for importer_id, defn in self._by_id.items() if defn.source == source
        ]:
            del self._by_id[importer_id]

    def get(self, importer_id: str) -> Optional[PromptImporterDefinition]:
        """Look up an importer by id, or None if it isn't registered."""
        return self._by_id.get(importer_id)

    def all(self) -> List[PromptImporterDefinition]:
        """Return every registered importer definition."""
        return list(self._by_id.values())

    def frontend_manifest(self) -> List[Dict[str, Any]]:
        """Serialize the registry for the `GET /api/prompts/importers` endpoint."""
        return [
            {"id": defn.importer_id, "label": defn.label, "component": defn.frontend_component}
            for defn in self.all()
        ]


# Module-level singleton shared by the plugin enable/disable path and the
# prompt import routes - mirrors `field_type_registry`.
prompt_importer_registry = PromptImporterRegistry()
