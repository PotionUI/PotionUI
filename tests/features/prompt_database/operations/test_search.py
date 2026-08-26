"""Unit coverage for search and near-duplicate detection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.prompt_database import operations
from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from tests.features.prompt_database.operations.test_mutations import make_prompt


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


@pytest.mark.asyncio
async def test_search_falls_back_to_filtered_text_search(dependencies):
    collaborators, repository, _, embedding_provider, _ = dependencies
    embedding_provider.is_available.return_value = False
    repository.text_search.return_value = [make_prompt("prompt-1")]

    result = await operations.search(
        collaborators,
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
    collaborators, repository, vector_store, _, _ = dependencies
    vector_store.get_all_embeddings.return_value = {}
    first = make_prompt("first", "A   RED Fox", heart_count=5)
    second = make_prompt("second", "a red fox", heart_count=1)
    unrelated = make_prompt("other", "blue ocean")
    repository.get_all.return_value = [first, second, unrelated]
    by_id = {prompt.id: prompt for prompt in (first, second, unrelated)}
    repository.get_by_ids.side_effect = lambda ids, _user_id: [by_id[item] for item in ids]

    groups = await operations.find_duplicates(collaborators, "user-1")

    assert len(groups) == 1
    assert groups[0]["similarity"] == 1.0
    assert [item["id"] for item in groups[0]["prompts"]] == ["first", "second"]
    assert all("negative_prompt" not in item for item in groups[0]["prompts"])


@pytest.mark.asyncio
async def test_duplicate_detection_by_embedding_reports_worst_case_group_similarity(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
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

    groups = await operations.find_duplicates(collaborators, "user-1", threshold=0.05)

    assert len(groups) == 1
    assert [item["id"] for item in groups[0]["prompts"]] == ["close-a", "close-b"]
    assert groups[0]["similarity"] == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio
async def test_duplicate_detection_by_embedding_respects_stricter_threshold(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    vector_store.get_all_embeddings.return_value = {
        "close-a": [1.0, 0.0],
        "close-b": [0.99, 0.01],
    }
    repository.get_by_ids.side_effect = AssertionError(
        "no group should be hydrated when nothing clears the threshold"
    )

    groups = await operations.find_duplicates(collaborators, "user-1", threshold=0.00001)

    assert groups == []
