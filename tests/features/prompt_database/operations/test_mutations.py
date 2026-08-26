"""Unit coverage for create/replace/delete Prompt aggregate operations."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.prompt_database import operations
from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from src.features.prompt_database.dto import PromptRequest
from src.features.segments.dto import RichSegment
from src.features.prompt_database.records import Prompt
from src.features.prompt_database.repository import flatten_segments


def make_prompt(
    prompt_id: str,
    text: str = "a red fox",
    *,
    user_id: str = "user-1",
    **metadata,
) -> Prompt:
    segments = metadata.pop("segments", [RichSegment(content=text)])
    return Prompt(
        id=prompt_id,
        user_id=user_id,
        segments=segments,
        flattened_text=flatten_segments(segments),
        created_at=metadata.pop("created_at", datetime(2026, 1, 1)),
        updated_at=metadata.pop("updated_at", datetime(2026, 1, 1)),
        **metadata,
    )


@pytest.fixture
def dependencies():
    repository = MagicMock()
    vector_store = MagicMock()
    embedding_provider = MagicMock()
    embedding_provider.embed = AsyncMock(return_value=[[0.1, 0.2]])
    embedding_provider.is_available = AsyncMock(return_value=False)
    plugins = MagicMock()
    collaborators = PromptDatabaseCollaborators(
        repository=repository,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        plugin_registry=plugins,
    )
    return collaborators, repository, vector_store, embedding_provider, plugins


def persist_with_flattened_text(candidate: Prompt) -> Prompt:
    candidate.id = candidate.id or "prompt-created"
    candidate.flattened_text = flatten_segments(candidate.segments)
    return candidate


@pytest.mark.asyncio
async def test_create_persists_complete_aggregate_and_refreshes_embedding(dependencies):
    collaborators, repository, vector_store, embedding_provider, plugins = dependencies
    repository.create.side_effect = persist_with_flattened_text
    request = PromptRequest(
        name="Fox study",
        usage_hint="positive",
        segments=[
            RichSegment(content="a fox", name="Subject", color="#aabbcc"),
            RichSegment(type="break"),
            RichSegment(content="watercolor", enabled=False),
        ],
    )

    saved = await operations.create_prompt(collaborators, "user-1", request)

    aggregate = repository.create.call_args.args[0]
    assert aggregate.user_id == "user-1"
    assert [segment.type for segment in aggregate.segments] == ["content", "break", "content"]
    assert aggregate.segments[0].name == "Subject"
    assert aggregate.segments[0].color == "#aabbcc"
    assert saved.flattened_text == "a fox BREAK"
    embedding_provider.embed.assert_awaited_once_with(["a fox BREAK"])
    vector_store.add.assert_called_once()
    repository.mark_embedded.assert_called_once_with("prompt-created")
    assert saved.embedded is True
    assert plugins.execute_hook.call_count == 2


@pytest.mark.asyncio
async def test_create_without_source_provider_defaults_to_manual(dependencies):
    """The Prompt Library's "New prompt" form never sends source_provider - the
    resulting row must land under the same "manual" value the Source filter's
    "Manual" option and sourceLabel()'s badge already assume, not a null the
    filter can never match."""
    collaborators, repository, vector_store, embedding_provider, plugins = dependencies
    repository.create.side_effect = persist_with_flattened_text
    request = PromptRequest(segments=[RichSegment(content="a hand-typed prompt")])

    saved = await operations.create_prompt(collaborators, "user-1", request)

    aggregate = repository.create.call_args.args[0]
    assert aggregate.source_provider == "manual"
    assert saved.source_provider == "manual"


@pytest.mark.asyncio
async def test_create_with_explicit_source_provider_is_not_overridden(dependencies):
    collaborators, repository, vector_store, embedding_provider, plugins = dependencies
    repository.create.side_effect = persist_with_flattened_text
    request = PromptRequest(segments=[RichSegment(content="imported text")], source_provider="text_import")

    saved = await operations.create_prompt(collaborators, "user-1", request)

    assert saved.source_provider == "text_import"


@pytest.mark.asyncio
async def test_replace_atomically_replaces_children_preserves_omitted_metadata_and_reembeds(
    dependencies,
):
    collaborators, repository, vector_store, embedding_provider, _ = dependencies
    existing = make_prompt(
        "prompt-1",
        source_provider="civitai",
        source_id="image-9",
        model_id="model-1",
        tags=["portrait"],
    )
    repository.get_by_id.return_value = existing

    def update(_prompt_id, _user_id, candidate):
        return persist_with_flattened_text(candidate)

    repository.update.side_effect = update
    request = PromptRequest(
        name="Replacement",
        segments=[RichSegment(content="first"), RichSegment(content="second")],
    )

    saved = await operations.replace_prompt(collaborators, "user-1", "prompt-1", request)

    repository.update.assert_called_once()
    replacement = repository.update.call_args.args[2]
    assert [segment.content for segment in replacement.segments] == ["first", "second"]
    assert replacement.source_provider == "civitai"
    assert replacement.source_id == "image-9"
    assert replacement.model_id == "model-1"
    assert replacement.tags == ["portrait"]
    assert saved.flattened_text == "first, second"
    embedding_provider.embed.assert_awaited_once_with(["first, second"])
    vector_store.add.assert_called_once()
    repository.mark_embedded.assert_called_once_with("prompt-1")
    assert saved.embedded is True


def test_delete_removes_prompt_embedding_only_after_owned_record_is_deleted(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    repository.delete.side_effect = [True, False]

    assert operations.delete_prompt(collaborators, "user-1", "prompt-1") is True
    vector_store.delete.assert_called_once_with("user-1", "prompt-1")

    assert operations.delete_prompt(collaborators, "user-1", "missing") is False
    assert vector_store.delete.call_count == 1
