"""Contributing a prompt importer.

A prompt importer is a source the prompt library's Import menu can pull from -
a marketplace provider, a text/file format, anything that produces prompts for
the calling user. Declare it in `manifest.yml` under `prompt_importers:`,
pointing `backend` at a `PromptImporter` subclass and `component` at the
plugin frontend asset that renders its modal.

Subclass `PromptImporter` and implement `run()`: read whatever your modal
posted in `payload`, create prompts for `user_id` via `create_prompt_for_user`,
and return a `PromptImportOutcome` summarizing what happened.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.features.prompt_database import operations
from src.platform.plugins.runtime_registries import get_container


@dataclass
class PromptImportOutcome:
    """What an import run produced, shown by the calling modal."""

    imported: int
    skipped: int
    total: int
    items: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


class PromptImporter(ABC):
    """A single prompt-library import source."""

    @abstractmethod
    async def run(self, payload: Dict[str, Any], user_id: str) -> PromptImportOutcome:
        """Run one import for `user_id`. `payload` is the request body posted
        by this importer's frontend modal, as-is."""
        raise NotImplementedError


async def create_prompt_for_user(
    user_id: str,
    prompt_text: str,
    *,
    name: Optional[str] = None,
    usage_hint: Optional[str] = None,
    source_provider: str,
    model_name: Optional[str] = None,
    base_model: Optional[str] = None,
    source_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Create one prompt for `user_id`, the same path manual/chat-created
    prompts use. `source_provider` is required - an imported prompt is never
    filed under the manual bucket.
    """
    collaborators = get_container().prompt_database
    prompt = await operations.add_prompt(
        collaborators,
        user_id,
        prompt_text,
        name=name,
        usage_hint=usage_hint,
        source_provider=source_provider,
        model_name=model_name,
        base_model=base_model,
        source_url=source_url,
    )
    return prompt.to_dict()


__all__ = [
    "PromptImportOutcome",
    "PromptImporter",
    "create_prompt_for_user",
]
