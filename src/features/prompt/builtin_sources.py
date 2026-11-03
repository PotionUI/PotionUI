from typing import Any, Dict, List

from src.features.models.form_refs import is_model_ref, model_id_of
from src.features.models.locator import ModelFileUnavailable
from src.pipelines.pipes._shared.generation.refmod_header import read_refmod_members
from src.platform.plugins.prompt_resource_sources import (
    PromptResourceCaller,
    PromptResourceSource,
    PromptResourceSourceRegistry,
)


def _refmod_members(item_key: str, caller: PromptResourceCaller) -> List[Dict[str, Any]]:
    path = item_key
    if is_model_ref(item_key):
        try:
            path = str(caller.model_file(model_id_of(item_key)))
        except ModelFileUnavailable as error:
            raise ValueError(f"the RefMod {item_key} is not available: {error}") from error
    return [{"kind": member.kind, "name": member.name} for member in read_refmod_members(path)]


BUILTIN_SOURCES = (
    PromptResourceSource(
        source_id="refmod",
        field_type="lora_picker",
        label="RefMod",
        badge="RefMod",
        members=_refmod_members,
    ),
)


def register_builtin_prompt_resource_sources(registry: PromptResourceSourceRegistry) -> None:
    for definition in BUILTIN_SOURCES:
        if registry.get(definition.source_id) is None:
            registry.register(definition)
