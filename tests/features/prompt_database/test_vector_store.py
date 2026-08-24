"""Regression coverage for ChromaDB prompt-vector result normalization."""

from unittest.mock import MagicMock

import numpy as np

from src.features.prompt_database.vector_store import PromptVectorStore


def make_store(results):
    store = PromptVectorStore.__new__(PromptVectorStore)
    collection = MagicMock()
    collection.get.return_value = results
    store.get_collection = MagicMock(return_value=collection)
    return store, collection


def test_get_all_embeddings_accepts_numpy_array_results():
    store, collection = make_store(
        {
            "ids": ["prompt-1", "prompt-2"],
            "embeddings": np.asarray([[0.1, 0.2], [0.3, 0.4]]),
        }
    )

    result = store.get_all_embeddings("user-1", where={"model_id": "model-1"})

    assert result == {
        "prompt-1": [0.1, 0.2],
        "prompt-2": [0.3, 0.4],
    }
    collection.get.assert_called_once_with(
        include=["embeddings"], where={"model_id": "model-1"}
    )


def test_get_all_embeddings_returns_empty_for_empty_numpy_array():
    store, _ = make_store(
        {
            "ids": [],
            "embeddings": np.empty((0, 2)),
        }
    )

    assert store.get_all_embeddings("user-1") == {}


def test_collection_name_is_namespaced_by_embedder_slug():
    store = PromptVectorStore(persist_dir="/tmp/unused", embedder_slug="local-bge-small-en-v1-5")

    assert store._collection_name("user-1") == "rich_prompts_user-1__local-bge-small-en-v1-5"


def test_different_embedder_slugs_never_collide():
    a = PromptVectorStore(persist_dir="/tmp/unused", embedder_slug="local-bge-small-en-v1-5")
    b = PromptVectorStore(persist_dir="/tmp/unused", embedder_slug="ollama-nomic-embed-text")

    assert a._collection_name("user-1") != b._collection_name("user-1")


def test_is_collection_empty_true_when_vector_count_is_zero():
    store = PromptVectorStore.__new__(PromptVectorStore)
    store.get_collection = MagicMock(return_value=MagicMock(count=MagicMock(return_value=0)))

    assert store.is_collection_empty("user-1") is True


def test_is_collection_empty_false_when_vectors_present():
    store = PromptVectorStore.__new__(PromptVectorStore)
    store.get_collection = MagicMock(return_value=MagicMock(count=MagicMock(return_value=3)))

    assert store.is_collection_empty("user-1") is False


def test_is_collection_empty_defaults_to_false_on_error():
    store = PromptVectorStore.__new__(PromptVectorStore)
    collection = MagicMock()
    collection.count.side_effect = RuntimeError("boom")
    store.get_collection = MagicMock(return_value=collection)

    assert store.is_collection_empty("user-1") is False
