from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from src.platform.plugins.sourced_registry import DuplicateRegistrationError, SourcedRegistry


class DuplicatePromptResourceSourceError(DuplicateRegistrationError):
    pass


@dataclass(frozen=True)
class PromptResourceCaller:
    user_id: Optional[str]
    is_admin: bool
    model_file: Callable[[str], Any]


PromptResourceMembers = Callable[[str, PromptResourceCaller], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class PromptResourceSource:
    source_id: str
    field_type: str
    label: str
    badge: str
    members: PromptResourceMembers
    source: str = "core"

    def spec(self) -> Dict[str, str]:
        return {"id": self.source_id, "field_type": self.field_type, "label": self.label, "badge": self.badge}


class PromptResourceSourceRegistry(SourcedRegistry[PromptResourceSource]):
    key_attr = "source_id"
    label = "Prompt resource source"
    duplicate_error = DuplicatePromptResourceSourceError


prompt_resource_source_registry = PromptResourceSourceRegistry()
