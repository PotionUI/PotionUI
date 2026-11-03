import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

from src.platform.plugins.prompt_resource_sources import (
    PromptResourceCaller,
    PromptResourceSourceRegistry,
    prompt_resource_source_registry,
)

logger = logging.getLogger(__name__)

BundleMembersResolver = Callable[[str, str], Sequence[str]]

_declared_sources: ContextVar[Mapping[str, str]] = ContextVar("declared_prompt_resource_sources", default={})


def prompt_resource_sources() -> PromptResourceSourceRegistry:
    from src.features.prompt.builtin_sources import register_builtin_prompt_resource_sources

    register_builtin_prompt_resource_sources(prompt_resource_source_registry)
    return prompt_resource_source_registry


@contextmanager
def declared_prompt_resource_sources(manifests: Iterable[Any]) -> Iterator[None]:
    declared = {
        str(entry["id"]): str(entry.get("field_type") or "")
        for manifest in manifests or []
        for entry in getattr(manifest, "prompt_resource_sources", None) or []
        if isinstance(entry, Mapping) and entry.get("id")
    }
    token = _declared_sources.set({**_declared_sources.get(), **declared})
    try:
        yield
    finally:
        _declared_sources.reset(token)


def source_field_type(source_id: str) -> Optional[str]:
    definition = prompt_resource_sources().get(source_id)
    if definition is not None:
        return definition.field_type
    return _declared_sources.get().get(source_id)


def entry_source(entry: Any) -> Optional[str]:
    source = entry.get("source") if isinstance(entry, Mapping) else None
    return source if isinstance(source, str) and source else None


def source_label(source_id: str, registry: Optional[PromptResourceSourceRegistry] = None) -> str:
    definition = (registry or prompt_resource_sources()).get(source_id)
    return definition.label if definition is not None else source_id


def with_source_specs(
    entries: Iterable[Mapping[str, Any]], registry: Optional[PromptResourceSourceRegistry] = None,
) -> List[Dict[str, Any]]:
    registry = registry or prompt_resource_sources()
    served: List[Dict[str, Any]] = []
    for entry in entries or []:
        source = entry_source(entry)
        definition = registry.get(source) if source else None
        if definition is None:
            served.append(dict(entry))
        else:
            served.append({**entry, "source_label": definition.label, "source_badge": definition.badge})
    return served


def read_source_members(
    registry: PromptResourceSourceRegistry, source_id: str, item_key: str, caller: PromptResourceCaller,
) -> List[Dict[str, Any]]:
    definition = registry.get(source_id)
    if definition is None:
        raise ValueError(f"unknown prompt resource source '{source_id}'")
    try:
        raw = definition.members(item_key, caller)
    except ValueError:
        raise
    except Exception as error:
        logger.warning("Prompt resource source %s failed for %s: %s", source_id, item_key, error, exc_info=True)
        raise ValueError(f"the {definition.label} {item_key} cannot be read") from error
    members: List[Dict[str, Any]] = []
    for member in raw or []:
        kind = member.get("kind") if isinstance(member, Mapping) else None
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"the {definition.label} {item_key} has a member with no kind")
        name = member.get("name")
        members.append({"kind": kind, "name": name if isinstance(name, str) else None})
    return members


def member_kinds_resolver(
    caller: PromptResourceCaller, registry: Optional[PromptResourceSourceRegistry] = None,
) -> BundleMembersResolver:
    registry = registry or prompt_resource_sources()

    def resolve(source_id: str, item_key: str) -> List[str]:
        return [member["kind"] for member in read_source_members(registry, source_id, item_key, caller)]

    return resolve
