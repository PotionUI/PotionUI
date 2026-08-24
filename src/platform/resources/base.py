"""Base classes and data models for the @resource system.

Resources are addressable pieces of application data (a LoRA's metadata, an
phrasebook category's values, a preset's form options, a past generation)
that users attach to chat messages via ``@namespace.path`` mentions. Providers
resolve dotted paths into LLM-facing content and power the @ dropdown's
suggestions. Providers are registered by the core or by plugins (manifest
``resources:`` section).
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def stem(filename: Optional[str]) -> str:
    """`filename` with its final extension stripped, or `""` for `None`."""
    name = filename or ""
    return name.rsplit(".", 1)[0] if "." in name else name


@dataclass
class ResolvedResource:
    """A resource resolved to its snapshot content.

    Attributes:
        uri: The full mention path as typed (e.g. "models.loras.detailer").
        namespace: The provider namespace (first path segment).
        kind: Provider-specific kind for UI display (e.g. "lora", "preset",
            "error" when resolution failed).
        title: Human-readable title for UI display.
        content: LLM-facing markdown injected into the conversation.
        metadata: UI-facing extras (ids, preview urls, counts...).
    """
    uri: str
    namespace: str
    kind: str
    title: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResourceSuggestion:
    """One entry in the @ dropdown.

    ``has_children=True`` marks a navigable path segment (namespace or
    category); ``False`` marks a directly attachable resource.

    ``attachable`` only matters when ``has_children=True``: it marks a
    navigable node that is ALSO directly resolvable at its own path (e.g. a
    preset, or a phrasebook category), so the frontend can offer both
    "attach this" and "browse into this" instead of forcing navigation.
    Ignored when ``has_children=False`` — leaves are always attachable.
    """
    uri: str
    label: str
    kind: str = "resource"
    description: Optional[str] = None
    has_children: bool = False
    icon: Optional[str] = None
    attachable: bool = False


@dataclass
class ResourceContext:
    """Dependency bundle handed to providers (mirrors ``ToolContext``)."""
    user_id: str
    mode_id: Optional[str] = None
    model_index_manager: Optional[Any] = None
    phrasebook_manager: Optional[Any] = None
    preset_manager: Optional[Any] = None
    generation_repository: Optional[Any] = None
    generation_parameter_repository: Optional[Any] = None
    generation_model_repository: Optional[Any] = None
    # The generate-form snapshot the chat sends alongside a message
    # (``context_metadata.form_state``): ``{"preset", "mode", "form_data"}``.
    # Present when a message is being resolved; absent for autocomplete.
    form_state: Optional[dict] = None


class BaseResourceProvider(ABC):
    """A provider owning one resource namespace.

    ``path`` arguments are the dot-split segments AFTER the namespace, e.g. for
    ``models.loras.detailer`` the models provider receives
    ``path=["loras", "detailer"]``.
    """

    # Chat modes this provider is visible in. None means all modes (subject to
    # the mode's own ``resource_namespaces`` allow-list). Plugin loading may
    # override per instance.
    modes: Optional[List[str]] = None

    icon: Optional[str] = None

    @property
    @abstractmethod
    def namespace(self) -> str:
        """First path segment this provider owns (e.g. "models")."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable namespace label. Defaults to the title-cased namespace."""
        return self.namespace.replace('_', ' ').title()

    @abstractmethod
    async def resolve(self, path: List[str], ctx: ResourceContext) -> Optional[ResolvedResource]:
        """Resolve a full path to its content, or None when it doesn't exist."""
        ...

    @abstractmethod
    async def suggest(
        self,
        path: List[str],
        partial: str,
        ctx: ResourceContext,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        """Suggest children under ``path`` matching the trailing ``partial``."""
        ...
