"""Unit coverage for search and near-duplicate detection."""

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from src.features.prompt_database import operations
from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from src.features.prompt_database.operations.search import _iter_block_distances
from src.features.prompt_database.vector_store import EmbeddingScan
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
        model_repository=MagicMock(),
    )
    return collaborators, repository, vector_store, embedding_provider, plugins


def make_scan(vectors: dict, total=None, partial=False) -> EmbeddingScan:
    ids = list(vectors)
    array = (
        np.asarray([vectors[prompt_id] for prompt_id in ids], dtype=np.float64)
        if ids
        else np.empty((0, 0), dtype=np.float64)
    )
    return EmbeddingScan(
        ids=ids, vectors=array, scanned=len(ids),
        total=total if total is not None else len(ids), partial=partial,
    )


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
    vector_store.scan_embeddings.return_value = make_scan({})
    first = make_prompt("first", "A   RED Fox", heart_count=5)
    second = make_prompt("second", "a red fox", heart_count=1)
    unrelated = make_prompt("other", "blue ocean")
    repository.get_ids_and_text.return_value = [
        (prompt.id, prompt.flattened_text) for prompt in (first, second, unrelated)
    ]
    repository.count.return_value = 3
    by_id = {prompt.id: prompt for prompt in (first, second, unrelated)}
    repository.get_by_ids.side_effect = lambda ids, _user_id: [by_id[item] for item in ids]

    result = await operations.find_duplicates(collaborators, "user-1")
    groups = result["groups"]

    assert len(groups) == 1
    assert groups[0]["similarity"] == 1.0
    assert [item["id"] for item in groups[0]["prompts"]] == ["first", "second"]
    assert all("negative_prompt" not in item for item in groups[0]["prompts"])
    assert result["scanned"] == 3
    assert result["total"] == 3
    assert result["partial"] is False


@pytest.mark.asyncio
async def test_duplicate_detection_by_text_reports_partial_when_capped(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    vector_store.scan_embeddings.return_value = make_scan({})
    repository.get_ids_and_text.return_value = []
    repository.count.return_value = 9001

    result = await operations.find_duplicates(collaborators, "user-1")

    assert result["scanned"] == 0
    assert result["total"] == 9001
    assert result["partial"] is True


@pytest.mark.asyncio
async def test_duplicate_detection_by_embedding_reports_worst_case_group_similarity(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    vector_store.scan_embeddings.return_value = make_scan({
        "close-a": [1.0, 0.0],
        "close-b": [0.99, 0.01],
        "far": [0.0, 1.0],
    })
    close_a = make_prompt("close-a", "a red fox")
    close_b = make_prompt("close-b", "a red foxx")
    far = make_prompt("far", "a blue ocean")
    by_id = {prompt.id: prompt for prompt in (close_a, close_b, far)}
    repository.get_by_ids.side_effect = lambda ids, _user_id: [by_id[item] for item in ids]

    result = await operations.find_duplicates(collaborators, "user-1", threshold=0.05)
    groups = result["groups"]

    assert len(groups) == 1
    assert [item["id"] for item in groups[0]["prompts"]] == ["close-a", "close-b"]
    assert groups[0]["similarity"] == pytest.approx(1.0, abs=0.01)
    assert result["scanned"] == 3
    assert result["partial"] is False


@pytest.mark.asyncio
async def test_duplicate_detection_by_embedding_respects_stricter_threshold(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    vector_store.scan_embeddings.return_value = make_scan({
        "close-a": [1.0, 0.0],
        "close-b": [0.99, 0.01],
    })
    repository.get_by_ids.side_effect = AssertionError(
        "no group should be hydrated when nothing clears the threshold"
    )

    result = await operations.find_duplicates(collaborators, "user-1", threshold=0.00001)

    assert result["groups"] == []


@pytest.mark.asyncio
async def test_duplicate_detection_threshold_boundary_is_strict(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    vector_store.scan_embeddings.return_value = make_scan({
        "a": [1.0, 0.0],
        "b": [0.96, np.sqrt(1 - 0.96 ** 2)],
    })
    repository.get_by_ids.side_effect = AssertionError(
        "a distance equal to the threshold must not form a group"
    )

    result = await operations.find_duplicates(collaborators, "user-1", threshold=0.04)

    assert result["groups"] == []


@pytest.mark.asyncio
async def test_duplicate_detection_threshold_boundary_just_below_groups(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    vector_store.scan_embeddings.return_value = make_scan({
        "a": [1.0, 0.0],
        "b": [0.96, np.sqrt(1 - 0.96 ** 2)],
    })
    a = make_prompt("a", "a red fox")
    b = make_prompt("b", "a red foxx")
    by_id = {"a": a, "b": b}
    repository.get_by_ids.side_effect = lambda ids, _user_id: [by_id[item] for item in ids]

    result = await operations.find_duplicates(collaborators, "user-1", threshold=0.040001)

    assert len(result["groups"]) == 1
    assert [item["id"] for item in result["groups"][0]["prompts"]] == ["a", "b"]


@pytest.mark.asyncio
async def test_duplicate_detection_zero_vector_sits_at_distance_one_from_everything(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    vector_store.scan_embeddings.return_value = make_scan({
        "zero": [0.0, 0.0],
        "unit": [1.0, 0.0],
    })
    repository.get_by_ids.side_effect = AssertionError(
        "a zero vector must never join a group (distance 1 from everything)"
    )

    result = await operations.find_duplicates(collaborators, "user-1", threshold=1.0)

    assert result["groups"] == []


@pytest.mark.asyncio
async def test_duplicate_detection_group_worst_pair_differs_from_best_pair(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    vector_store.scan_embeddings.return_value = make_scan({
        "a": [1.0, 0.0],
        "b": [0.999, np.sqrt(1 - 0.999 ** 2)],
        "c": [0.9, np.sqrt(1 - 0.9 ** 2)],
    })
    a = make_prompt("a", "alpha")
    b = make_prompt("b", "alpha two")
    c = make_prompt("c", "alpha three")
    by_id = {"a": a, "b": b, "c": c}
    repository.get_by_ids.side_effect = lambda ids, _user_id: [by_id[item] for item in ids]

    result = await operations.find_duplicates(collaborators, "user-1", threshold=0.15)

    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert {item["id"] for item in group["prompts"]} == {"a", "b", "c"}
    assert group["similarity"] == pytest.approx(0.9, abs=1e-6)


def test_iter_block_distances_never_materializes_a_full_n_by_n_matrix():
    n = 11
    vectors = np.random.default_rng(0).normal(size=(n, 4))
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors /= norms

    blocks = list(_iter_block_distances(vectors, block_size=3))

    assert len(blocks) == 4
    for start, end, block_distance in blocks:
        assert block_distance.shape == (end - start, n)
        assert block_distance.shape[0] <= 3
        assert block_distance.shape != (n, n)
