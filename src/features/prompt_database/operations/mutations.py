"""Create, replace, and delete a Prompt aggregate.

Module-level functions, `PromptDatabaseCollaborators` as the explicit leading
arg - no class holds them together. `add_prompt` is the convenience chat
tools use; it creates exactly one detached prompt from raw text.
"""
import logging
from typing import Any, List, Optional, Sequence

from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from src.features.prompt_database.dto import PromptRequest
from src.features.prompt_database.hooks import PROMPT_DATABASE_HOOKS
from src.features.prompt_database.operations.embedding import embed_prompt
from src.features.prompt_database.records import Prompt
from src.features.segments.dto import RichSegment
from src.platform.plugins.hooks import HookContext

logger = logging.getLogger(__name__)

# The source_provider value every hand-authored prompt is filed under - what
# the "Manual" browse filter matches against and what sourceLabel() in the
# frontend already displays as a fallback for a falsy source_provider.
MANUAL_SOURCE_PROVIDER = "manual"


def _fire_hook(collaborators: PromptDatabaseCollaborators, hook: str, prompt: Prompt, provider_id: Optional[str] = None) -> None:
    context = HookContext(
        hook_name=hook,
        plugin_id="system",
        data={
            "prompt": prompt,
            "segments": prompt.segments,
            "user_id": prompt.user_id,
            "provider_id": provider_id,
        },
    )
    collaborators.plugin_registry.execute_hook(hook, context=context)


def _from_request(user_id: str, request: PromptRequest, prompt_id: Optional[str] = None) -> Prompt:
    values = request.model_dump()
    segments = values.pop("segments")
    return Prompt(
        id=prompt_id,
        user_id=user_id,
        segments=[s if isinstance(s, RichSegment) else RichSegment(**s) for s in segments],
        **values,
    )


async def create_prompt(collaborators: PromptDatabaseCollaborators, user_id: str, request: PromptRequest) -> Prompt:
    candidate = _from_request(user_id, request)
    # A request with no source_provider (the /api/prompts create form the
    # Prompt Library's "New prompt" flow sends) is a hand-authored prompt,
    # never an unset field - default it so the "Manual" source filter and
    # sourceLabel()'s badge agree with what actually got persisted.
    if not candidate.source_provider:
        candidate.source_provider = MANUAL_SOURCE_PROVIDER
    _fire_hook(collaborators, PROMPT_DATABASE_HOOKS.before_save, candidate, candidate.source_provider)
    saved = collaborators.repository.create(candidate)
    _fire_hook(collaborators, PROMPT_DATABASE_HOOKS.after_save, saved, saved.source_provider)
    saved.embedded = await embed_prompt(collaborators, user_id, saved)
    return saved


async def replace_prompt(
    collaborators: PromptDatabaseCollaborators, user_id: str, prompt_id: str, request: PromptRequest
) -> Optional[Prompt]:
    existing = collaborators.repository.get_by_id(prompt_id, user_id)
    if existing is None:
        return None
    values = request.model_dump()
    for field in PromptRequest.model_fields:
        if field in {"segments"} or field in request.model_fields_set:
            continue
        if hasattr(existing, field):
            values[field] = getattr(existing, field)
    candidate = _from_request(user_id, PromptRequest(**values), prompt_id)
    _fire_hook(collaborators, PROMPT_DATABASE_HOOKS.before_save, candidate, candidate.source_provider)
    saved = collaborators.repository.update(prompt_id, user_id, candidate)
    if saved is None:
        return None
    _fire_hook(collaborators, PROMPT_DATABASE_HOOKS.after_save, saved, saved.source_provider)
    saved.embedded = await embed_prompt(collaborators, user_id, saved)
    return saved


async def add_prompt(
    collaborators: PromptDatabaseCollaborators,
    user_id: str,
    prompt_text: str,
    model_id: Optional[str] = None,
    source_provider: str = MANUAL_SOURCE_PROVIDER,
    name: Optional[str] = None,
    usage_hint: Optional[str] = None,
    **metadata: Any,
) -> Prompt:
    """Convenience used by chat tools; creates exactly one detached prompt."""
    allowed = set(PromptRequest.model_fields) - {"segments", "name", "usage_hint", "source_provider"}
    values = {key: value for key, value in metadata.items() if key in allowed}
    request = PromptRequest(
        name=name,
        usage_hint=usage_hint,
        segments=[RichSegment(content=prompt_text)],
        source_provider=source_provider,
        model_id=model_id,
        **values,
    )
    return await create_prompt(collaborators, user_id, request)


def delete_prompt(collaborators: PromptDatabaseCollaborators, user_id: str, prompt_id: str) -> bool:
    deleted = collaborators.repository.delete(prompt_id, user_id)
    if deleted:
        collaborators.vector_store.delete(user_id, prompt_id)
    return deleted


def bulk_delete_prompts(collaborators: PromptDatabaseCollaborators, user_id: str, prompt_ids: Sequence[str]) -> int:
    count = collaborators.repository.bulk_delete(prompt_ids, user_id)
    if count:
        collaborators.vector_store.bulk_delete(user_id, list(prompt_ids))
    return count


def purge_model_prompts(collaborators: PromptDatabaseCollaborators, user_id: str, model_id: str) -> int:
    count, ids = collaborators.repository.delete_by_model(model_id, user_id)
    if ids:
        collaborators.vector_store.bulk_delete(user_id, ids)
    return count
