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

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

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
    source_id: Optional[str] = None,
    source_url: Optional[str] = None,
    source_group_id: Optional[str] = None,
    model_id: Optional[str] = None,
    model_name: Optional[str] = None,
    base_model: Optional[str] = None,
    cfg_scale: Optional[float] = None,
    steps: Optional[int] = None,
    sampler: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    nsfw: bool = False,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create one prompt for `user_id`, the same path manual/chat-created
    prompts use. `source_provider` is required - an imported prompt is never
    filed under the manual bucket. `model_id` is a catalog model id; an
    unknown one raises `ValueError`, which the importer route reports as a
    400 `invalid_model`. `source_group_id` pairs a positive prompt with its
    negative counterpart - give both the same value.
    """
    collaborators = get_container().prompt_database
    prompt = await operations.add_prompt(
        collaborators,
        user_id,
        prompt_text,
        model_id=model_id,
        name=name,
        usage_hint=usage_hint,
        source_provider=source_provider,
        source_id=source_id,
        source_url=source_url,
        source_group_id=source_group_id,
        model_name=model_name,
        base_model=base_model,
        cfg_scale=cfg_scale,
        steps=steps,
        sampler=sampler,
        width=width,
        height=height,
        nsfw=nsfw,
        tags=tags or [],
        metadata=metadata or {},
    )
    return prompt.to_dict()


def find_prompt_source_ids(
    user_id: str, source_provider: str, model_id: Optional[str] = None,
) -> Set[str]:
    """The `source_id`s `user_id` already has on file under `source_provider`
    (optionally narrowed to one catalog `model_id`) - what a bulk importer
    dedupes a freshly fetched batch against before creating anything."""
    collaborators = get_container().prompt_database
    return collaborators.repository.get_source_ids(user_id, source_provider, model_id=model_id)


async def import_prompts_for_user(
    user_id: str,
    entries: Sequence[Dict[str, Any]],
    *,
    source_provider: str,
) -> Dict[str, int]:
    """Create prompt rows for `user_id` from a batch of provider-fetched
    entries, skipping any whose `source_id` this user already has on file
    under `source_provider` (see `find_prompt_source_ids`). An entry is a
    dict with `prompt` and, optionally, `negative_prompt`, `source_id`,
    `source_url`, `model_id`, `model_name`, `base_model`, `cfg_scale`,
    `steps`, `sampler`, `width`, `height`, `nsfw`, `tags`, `metadata` - the
    shape `ProviderPromptItem` describes. A `negative_prompt` becomes its own
    row, paired with the positive one under a fresh `source_group_id`.
    Returns `{"created": <rows written>, "skipped_duplicates": <entries
    skipped>}`.
    """
    if not entries:
        return {"created": 0, "skipped_duplicates": 0}

    model_id = entries[0].get("model_id")
    existing = find_prompt_source_ids(user_id, source_provider, model_id)

    created = 0
    skipped_duplicates = 0
    for entry in entries:
        source_id = entry.get("source_id")
        if source_id and source_id in existing:
            skipped_duplicates += 1
            continue

        negative_text = entry.get("negative_prompt")
        source_group_id = uuid.uuid4().hex if negative_text else None
        shared: Dict[str, Any] = dict(
            source_provider=source_provider,
            source_id=source_id,
            source_url=entry.get("source_url"),
            source_group_id=source_group_id,
            model_id=entry.get("model_id"),
            model_name=entry.get("model_name"),
            base_model=entry.get("base_model"),
            cfg_scale=entry.get("cfg_scale"),
            steps=entry.get("steps"),
            sampler=entry.get("sampler"),
            width=entry.get("width"),
            height=entry.get("height"),
            nsfw=bool(entry.get("nsfw", False)),
            tags=entry.get("tags"),
            metadata=entry.get("metadata"),
        )

        await create_prompt_for_user(
            user_id, entry["prompt"], usage_hint="positive" if source_group_id else None, **shared,
        )
        created += 1

        if negative_text:
            await create_prompt_for_user(
                user_id, negative_text, usage_hint="negative", **shared,
            )
            created += 1

        if source_id:
            existing.add(source_id)

    return {"created": created, "skipped_duplicates": skipped_duplicates}


__all__ = [
    "PromptImportOutcome",
    "PromptImporter",
    "create_prompt_for_user",
    "find_prompt_source_ids",
    "import_prompts_for_user",
]
