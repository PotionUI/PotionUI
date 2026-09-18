"""Regression coverage for ChromaDB prompt-vector result normalization."""

from unittest.mock import MagicMock, call

import numpy as np

from src.features.prompt_database.vector_store import PromptVectorStore


def make_store(get_results=None, count=0):
    store = PromptVectorStore.__new__(PromptVectorStore)
    collection = MagicMock()
    if get_results is not None:
        collection.get.side_effect = get_results if isinstance(get_results, list) else [get_results]
    collection.count.return_value = count
    store.get_collection = MagicMock(return_value=collection)
    return store, collection


def test_scan_embeddings_accepts_numpy_array_results():
    store, collection = make_store(
        get_results=[
            {
                "ids": ["prompt-1", "prompt-2"],
                "embeddings": np.asarray([[0.1, 0.2], [0.3, 0.4]]),
            }
        ],
        count=2,
    )

    result = store.scan_embeddings("user-1", where={"model_id": "model-1"}, cap=500, page_size=500)

    assert result.ids == ["prompt-1", "prompt-2"]
    assert result.vectors.tolist() == [[0.1, 0.2], [0.3, 0.4]]
    assert result.scanned == 2
    assert result.total == 2
    assert result.partial is False
    collection.get.assert_called_once_with(
        include=["embeddings"], limit=500, offset=0, where={"model_id": "model-1"}
    )


def test_scan_embeddings_returns_empty_for_no_results():
    store, _ = make_store(get_results=[{"ids": [], "embeddings": np.empty((0, 2))}], count=0)

    result = store.scan_embeddings("user-1")

    assert result.ids == []
    assert result.scanned == 0
    assert result.total == 0
    assert result.partial is False
    assert result.vectors.shape == (0, 0)


def test_scan_embeddings_pages_until_a_short_page_and_is_not_partial():
    store, collection = make_store(
        get_results=[
            {"ids": ["p1", "p2"], "embeddings": [[1.0, 0.0], [0.0, 1.0]]},
            {"ids": ["p3"], "embeddings": [[1.0, 1.0]]},
        ],
        count=3,
    )

    result = store.scan_embeddings("user-1", cap=10, page_size=2)

    assert result.ids == ["p1", "p2", "p3"]
    assert result.scanned == 3
    assert result.total == 3
    assert result.partial is False
    assert result.vectors.shape == (3, 2)
    assert collection.get.call_args_list == [
        call(include=["embeddings"], limit=2, offset=0),
        call(include=["embeddings"], limit=2, offset=2),
    ]


def test_scan_embeddings_respects_the_cap_and_reports_partial():
    store, collection = make_store(
        get_results=[
            {"ids": ["p1", "p2"], "embeddings": [[1.0, 0.0], [0.0, 1.0]]},
            {"ids": ["p3"], "embeddings": [[1.0, 1.0]]},
        ],
        count=500,
    )

    result = store.scan_embeddings("user-1", cap=3, page_size=2)

    assert result.ids == ["p1", "p2", "p3"]
    assert result.scanned == 3
    assert result.total == 500
    assert result.partial is True
    assert collection.get.call_args_list == [
        call(include=["embeddings"], limit=2, offset=0),
        call(include=["embeddings"], limit=1, offset=2),
    ]


def test_scan_embeddings_never_requests_more_than_the_cap():
    store, collection = make_store(
        get_results=[
            {"ids": ["p0", "p1", "p2", "p3", "p4"], "embeddings": np.ones((5, 2))},
            {"ids": ["p5", "p6", "p7", "p8", "p9"], "embeddings": np.ones((5, 2))},
            {"ids": ["p10", "p11"], "embeddings": np.ones((2, 2))},
        ],
        count=1000,
    )

    result = store.scan_embeddings("user-1", cap=12, page_size=5)

    assert result.scanned == 12
    assert result.partial is True
    limits = [entry.kwargs["limit"] for entry in collection.get.call_args_list]
    assert limits == [5, 5, 2]


def test_scan_embeddings_stops_and_logs_on_a_page_failure():
    store, collection = make_store(count=4)
    collection.get.side_effect = RuntimeError("boom")

    result = store.scan_embeddings("user-1")

    assert result.ids == []
    assert result.scanned == 0
    assert result.total == 4
    assert result.partial is False


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
