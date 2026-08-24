"""Unit coverage for the detached rich Prompt aggregate manager."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.prompt_database.dto import PromptRequest
from src.features.segments.dto import RichSegment
from src.features.prompt_database.manager import PromptDatabaseManager
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
    manager = PromptDatabaseManager(
        repository=repository,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        plugin_registry=plugins,
    )
    return manager, repository, vector_store, embedding_provider, plugins


def persist_with_flattened_text(candidate: Prompt) -> Prompt:
    candidate.id = candidate.id or "prompt-created"
    candidate.flattened_text = flatten_segments(candidate.segments)
    return candidate


@pytest.mark.asyncio
async def test_create_persists_complete_aggregate_and_refreshes_embedding(dependencies):
    manager, repository, vector_store, embedding_provider, plugins = dependencies
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

    saved = await manager.create_prompt("user-1", request)

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
    manager, repository, vector_store, embedding_provider, plugins = dependencies
    repository.create.side_effect = persist_with_flattened_text
    request = PromptRequest(segments=[RichSegment(content="a hand-typed prompt")])

    saved = await manager.create_prompt("user-1", request)

    aggregate = repository.create.call_args.args[0]
    assert aggregate.source_provider == "manual"
    assert saved.source_provider == "manual"


@pytest.mark.asyncio
async def test_create_with_explicit_source_provider_is_not_overridden(dependencies):
    manager, repository, vector_store, embedding_provider, plugins = dependencies
    repository.create.side_effect = persist_with_flattened_text
    request = PromptRequest(segments=[RichSegment(content="imported text")], source_provider="text_import")

    saved = await manager.create_prompt("user-1", request)

    assert saved.source_provider == "text_import"


@pytest.mark.asyncio
async def test_replace_atomically_replaces_children_preserves_omitted_metadata_and_reembeds(
    dependencies,
):
    manager, repository, vector_store, embedding_provider, _ = dependencies
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

    saved = await manager.replace_prompt("user-1", "prompt-1", request)

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
    manager, repository, vector_store, _, _ = dependencies
    repository.delete.side_effect = [True, False]

    assert manager.delete_prompt("user-1", "prompt-1") is True
    vector_store.delete.assert_called_once_with("user-1", "prompt-1")

    assert manager.delete_prompt("user-1", "missing") is False
    assert vector_store.delete.call_count == 1


@pytest.mark.asyncio
async def test_embed_pending_resets_stale_flags_after_an_embedder_switch(dependencies):
    manager, repository, vector_store, embedding_provider, _ = dependencies
    repository.has_embedded.return_value = True
    vector_store.is_collection_empty.return_value = True
    repository.get_unembedded.return_value = [
        make_prompt("prompt-1"), make_prompt("prompt-2"),
    ]

    count = await manager.embed_pending("user-1")

    repository.reset_embedded.assert_called_once_with("user-1")
    assert count == 2


@pytest.mark.asyncio
async def test_embed_pending_leaves_flags_alone_when_active_namespace_has_vectors(dependencies):
    manager, repository, vector_store, _, _ = dependencies
    repository.has_embedded.return_value = True
    vector_store.is_collection_empty.return_value = False
    repository.get_unembedded.return_value = []

    count = await manager.embed_pending("user-1")

    repository.reset_embedded.assert_not_called()
    assert count == 0


@pytest.mark.asyncio
async def test_embed_pending_skips_reset_check_when_nothing_was_ever_embedded(dependencies):
    manager, repository, vector_store, _, _ = dependencies
    repository.has_embedded.return_value = False
    repository.get_unembedded.return_value = []

    await manager.embed_pending("user-1")

    vector_store.is_collection_empty.assert_not_called()
    repository.reset_embedded.assert_not_called()


@pytest.mark.asyncio
async def test_search_falls_back_to_filtered_text_search(dependencies):
    manager, repository, _, embedding_provider, _ = dependencies
    embedding_provider.is_available.return_value = False
    repository.text_search.return_value = [make_prompt("prompt-1")]

    result = await manager.search(
        "user-1",
        "fox",
        limit=7,
        base_model="SDXL",
        model_id="model-1",
        source_provider="civitai",
    )

    assert [prompt.id for prompt in result] == ["prompt-1"]
    repository.text_search.assert_called_once_with(
        "user-1", "fox", 7, "SDXL", "model-1", "civitai"
    )


@pytest.mark.asyncio
async def test_duplicate_detection_uses_normalized_flattened_text_without_pair_semantics(
    dependencies,
):
    manager, repository, vector_store, _, _ = dependencies
    vector_store.get_all_embeddings.return_value = {}
    first = make_prompt("first", "A   RED Fox", heart_count=5)
    second = make_prompt("second", "a red fox", heart_count=1)
    unrelated = make_prompt("other", "blue ocean")
    repository.get_all.return_value = [first, second, unrelated]
    by_id = {prompt.id: prompt for prompt in (first, second, unrelated)}
    repository.get_by_ids.side_effect = lambda ids, _user_id: [by_id[item] for item in ids]

    groups = await manager.find_duplicates("user-1")

    assert len(groups) == 1
    assert groups[0]["similarity"] == 1.0
    assert [item["id"] for item in groups[0]["prompts"]] == ["first", "second"]
    assert all("negative_prompt" not in item for item in groups[0]["prompts"])


@pytest.mark.asyncio
async def test_duplicate_detection_by_embedding_reports_worst_case_group_similarity(dependencies):
    manager, repository, vector_store, _, _ = dependencies
    vector_store.get_all_embeddings.return_value = {
        "close-a": [1.0, 0.0],
        "close-b": [0.99, 0.01],
        "far": [0.0, 1.0],
    }
    close_a = make_prompt("close-a", "a red fox")
    close_b = make_prompt("close-b", "a red foxx")
    far = make_prompt("far", "a blue ocean")
    by_id = {prompt.id: prompt for prompt in (close_a, close_b, far)}
    repository.get_by_ids.side_effect = lambda ids, _user_id: [by_id[item] for item in ids]

    groups = await manager.find_duplicates("user-1", threshold=0.05)

    assert len(groups) == 1
    assert [item["id"] for item in groups[0]["prompts"]] == ["close-a", "close-b"]
    assert groups[0]["similarity"] == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio
async def test_duplicate_detection_by_embedding_respects_stricter_threshold(dependencies):
    manager, repository, vector_store, _, _ = dependencies
    vector_store.get_all_embeddings.return_value = {
        "close-a": [1.0, 0.0],
        "close-b": [0.99, 0.01],
    }
    repository.get_by_ids.side_effect = AssertionError(
        "no group should be hydrated when nothing clears the threshold"
    )

    groups = await manager.find_duplicates("user-1", threshold=0.00001)

    assert groups == []


