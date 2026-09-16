from dataclasses import dataclass
from typing import Any, Dict, List, Optional


PLUGIN_ROUTE_PREFIX = "/api/plugins/"


class DuplicateLoginProviderError(ValueError):
    pass


class InvalidLoginProviderError(ValueError):
    pass


def source_from_start_path(start_path: str) -> Optional[str]:
    if not start_path.startswith(PLUGIN_ROUTE_PREFIX):
        return None
    remainder = start_path[len(PLUGIN_ROUTE_PREFIX):]
    plugin_id = remainder.split("/", 1)[0]
    return plugin_id or None


@dataclass(frozen=True)
class LoginProviderDefinition:
    id: str
    label: str
    start_path: str
    source: str = "core"

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "label": self.label, "start_path": self.start_path}


class LoginProviderRegistry:
    def __init__(self):
        self._by_id: Dict[str, LoginProviderDefinition] = {}

    def register(self, definition: LoginProviderDefinition) -> None:
        if not definition.id:
            raise InvalidLoginProviderError("Login provider id is required")
        if not definition.label:
            raise InvalidLoginProviderError(
                f"Login provider '{definition.id}' is missing a label"
            )
        if not definition.start_path.startswith("/"):
            raise InvalidLoginProviderError(
                f"Login provider '{definition.id}' needs an absolute start_path"
            )
        if definition.id in self._by_id:
            raise DuplicateLoginProviderError(
                f"Login provider already registered: '{definition.id}'"
            )
        self._by_id[definition.id] = definition

    def unregister(self, provider_id: str) -> None:
        self._by_id.pop(provider_id, None)

    def unregister_source(self, source: str) -> None:
        for provider_id in [
            provider_id
            for provider_id, defn in self._by_id.items()
            if defn.source == source
        ]:
            del self._by_id[provider_id]

    def get(self, provider_id: str) -> Optional[LoginProviderDefinition]:
        return self._by_id.get(provider_id)

    def all(self) -> List[LoginProviderDefinition]:
        return list(self._by_id.values())

    def manifest(self) -> List[Dict[str, Any]]:
        return [defn.to_dict() for defn in self.all()]


login_provider_registry = LoginProviderRegistry()
