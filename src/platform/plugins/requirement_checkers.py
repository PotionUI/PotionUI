"""Preset requirement checker registry.

A preset declares what it needs to run (`requirements:` in `preset.yml` - see
docs/presets.md "Requirements"): a binary on PATH, a Python package, a
specific model present in the depot, a VRAM floor, a supported OS. A
"requirement checker" is the code that evaluates one `type:` of entry against
this instance. Core ships five (`src.features.presets.requirements.builtin`);
a plugin can add engine-specific ones (e.g. a ComfyUI custom-node check)
without touching core, by declaring `requirement_checkers:` in its
`manifest.yml`.

This module holds only the registration bookkeeping - the checker contract
itself (`RequirementChecker`, `RequirementResult`, `RequirementContext`) lives
in `src.features.presets.requirements.contracts` (re-exported by
`src.plugin_api.presets`), because it carries feature-shaped context (the
models catalog, the resolved backend) that `src.platform` must not import.
Checker objects are stored here `Any`-typed for the same reason - mirrors
`FieldTypeRegistry`/`PromptImporterRegistry`.

Plugins extend the system by registering additional checkers on the shared
`requirement_checker_registry` singleton when they are enabled, and by having
them removed via `unregister_source` when disabled.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class DuplicateRequirementCheckerError(ValueError):
    """Raised when registering a requirement type name that is already registered."""


@dataclass(frozen=True)
class RequirementCheckerRegistration:
    """One registered requirement checker."""

    type_name: str
    # A `src.plugin_api.presets.RequirementChecker` instance - typed `Any`
    # here because `src.platform` must not import `src.features`/`src.plugin_api`.
    checker: Any
    source: str = "core"


class RequirementCheckerRegistry:
    """Registry mapping requirement `type:` name -> `RequirementCheckerRegistration`."""

    def __init__(self):
        self._by_type: Dict[str, RequirementCheckerRegistration] = {}

    def register(self, registration: RequirementCheckerRegistration) -> None:
        """Register a checker. Raises `DuplicateRequirementCheckerError` on name collision."""
        if registration.type_name in self._by_type:
            raise DuplicateRequirementCheckerError(
                f"Requirement checker already registered: '{registration.type_name}'"
            )
        self._by_type[registration.type_name] = registration

    def unregister_source(self, source: str) -> None:
        """Remove every checker registered by `source` (e.g. a plugin id)."""
        for type_name in [
            type_name for type_name, reg in self._by_type.items() if reg.source == source
        ]:
            del self._by_type[type_name]

    def get(self, type_name: str) -> Optional[RequirementCheckerRegistration]:
        """Look up a checker by its requirement `type:` name, or `None` if
        nothing is registered for it - an unregistered type is not an error
        here (see `evaluate_preset_requirements`'s "unknown" handling), only
        the linter treats it as one."""
        return self._by_type.get(type_name)

    def all(self) -> List[RequirementCheckerRegistration]:
        """Return every registered checker."""
        return list(self._by_type.values())


# Module-level singleton shared by the plugin enable/disable path and preset
# requirements evaluation - mirrors `field_type_registry`.
requirement_checker_registry = RequirementCheckerRegistry()
