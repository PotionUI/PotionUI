"""GalleryVectorStore: namespacing, upserts, and search-result shaping."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.features.media_index.gallery_vector_store import GalleryVectorStore


def make_store(query_results=None):
    store = GalleryVectorStore.__new__(GalleryVectorStore)
    collection = MagicMock()
    if query_results is not None:
        collection.query.return_value = query_results
    store.get_collection = MagicMock(return_value=collection)
    return store, collection


def make_store_with_client(embedder_slug="slug-b"):
    """A store whose ``client`` property is stubbed directly - for the
    collection-management calls (`list_collections`/`delete_collection`)
    that operate on the client, not a single collection."""
    store = GalleryVectorStore(persist_dir="/tmp/unused", embedder_slug=embedder_slug)
    store._client = MagicMock()
    return store, store._client


def test_collection_name_is_namespaced_by_user_and_embedder():
    store = GalleryVectorStore(persist_dir="/tmp/unused", embedder_slug="local-siglip-base")

    assert store._collection_name("user-1") == "gallery_user-1__local-siglip-base"


def test_different_embedder_slugs_never_collide():
    a = GalleryVectorStore(persist_dir="/tmp/unused", embedder_slug="local-siglip-base")
    b = GalleryVectorStore(persist_dir="/tmp/unused", embedder_slug="local-siglip-so400m")

    assert a._collection_name("user-1") != b._collection_name("user-1")


def test_gallery_and_prompt_namespaces_are_disjoint():
    from src.features.prompt_database.vector_store import PromptVectorStore

    gallery = GalleryVectorStore(persist_dir="/tmp/unused", embedder_slug="slug")
    prompts = PromptVectorStore(persist_dir="/tmp/unused", embedder_slug="slug")

    assert gallery._collection_name("user-1") != prompts._collection_name("user-1")


def test_add_upserts_with_sanitized_metadata():
    store, collection = make_store()

    store.add(
        "user-1",
        "file-1",
        [0.1, 0.2],
        metadata={"generation_id": "gen-1", "nested": {"drop": "me"}},
    )

    collection.upsert.assert_called_once_with(
        ids=["file-1"],
        embeddings=[[0.1, 0.2]],
        metadatas=[{"generation_id": "gen-1"}],
    )


def test_search_returns_ranked_hits_with_similarity():
    store, collection = make_store(
        {
            "ids": [["file-1", "file-2"]],
            "metadatas": [[{"generation_id": "gen-1"}, {"generation_id": "gen-2"}]],
            "distances": [[0.7, 0.8]],
        }
    )

    hits = store.search("user-1", [0.1, 0.2], limit=5)

    assert hits == [
        {"file_id": "file-1", "generation_id": "gen-1", "similarity": 0.30000000000000004},
        {"file_id": "file-2", "generation_id": "gen-2", "similarity": 0.19999999999999996},
    ]
    collection.query.assert_called_once_with(
        query_embeddings=[[0.1, 0.2]],
        n_results=5,
        include=["metadatas", "distances"],
    )


def test_search_tolerates_missing_metadata():
    store, _ = make_store(
        {
            "ids": [["file-1"]],
            "metadatas": [[None]],
            "distances": [[0.9]],
        }
    )

    [hit] = store.search("user-1", [0.1])

    assert hit["file_id"] == "file-1"
    assert hit["generation_id"] is None


def test_search_swallows_backend_errors():
    store, collection = make_store()
    collection.query.side_effect = RuntimeError("boom")

    assert store.search("user-1", [0.1]) == []


def test_is_collection_empty_reflects_count():
    store, collection = make_store()
    collection.count.return_value = 0
    assert store.is_collection_empty("user-1") is True

    collection.count.return_value = 3
    assert store.is_collection_empty("user-1") is False


def test_is_collection_empty_defaults_to_false_on_error():
    store, collection = make_store()
    collection.count.side_effect = RuntimeError("boom")

    assert store.is_collection_empty("user-1") is False


def test_collection_size_reflects_count():
    store, collection = make_store()
    collection.count.return_value = 42

    assert store.collection_size("user-1") == 42


def test_collection_size_defaults_to_zero_on_error():
    store, collection = make_store()
    collection.count.side_effect = RuntimeError("boom")

    assert store.collection_size("user-1") == 0


def test_all_generation_ids_dedupes_and_skips_unmetadataed_rows():
    store, collection = make_store()
    collection.get.return_value = {
        "ids": ["file-1", "file-2", "file-3", "file-4"],
        "metadatas": [
            {"generation_id": "gen-1"},
            {"generation_id": "gen-1"},
            {"generation_id": "gen-2"},
            None,
        ],
    }

    assert store.all_generation_ids("user-1") == ["gen-1", "gen-2"]
    collection.get.assert_called_once_with(include=["metadatas"])


def test_all_generation_ids_defaults_to_empty_on_error():
    store, collection = make_store()
    collection.get.side_effect = RuntimeError("boom")

    assert store.all_generation_ids("user-1") == []


# ---------------------------------------------------------------------------
# Stale-collection pruning: drop a superseded embedder's collection only
# once its rebuild is known to have completed - see
# MediaIndexer._settle_gallery_rebuilds for the "when" half of this.
# ---------------------------------------------------------------------------

def test_delete_collection_defaults_to_the_active_collection():
    store, client = make_store_with_client(embedder_slug="slug-b")

    store.delete_collection("user-1")

    client.delete_collection.assert_called_once_with("gallery_user-1__slug-b")


def test_delete_collection_targets_an_explicit_name():
    store, client = make_store_with_client(embedder_slug="slug-b")

    store.delete_collection("user-1", collection_name="gallery_user-1__slug-a")

    client.delete_collection.assert_called_once_with("gallery_user-1__slug-a")


def test_delete_collection_swallows_backend_errors():
    store, client = make_store_with_client()
    client.delete_collection.side_effect = RuntimeError("boom")

    store.delete_collection("user-1")  # must not raise


def test_stale_collection_names_excludes_current_and_other_users():
    store, client = make_store_with_client(embedder_slug="slug-b")
    client.list_collections.return_value = [
        SimpleNamespace(name="gallery_user-1__slug-a"),
        SimpleNamespace(name="gallery_user-1__slug-b"),  # current - excluded
        SimpleNamespace(name="gallery_user-2__slug-a"),  # different user
        SimpleNamespace(name="some_other_collection"),
    ]

    assert store.stale_collection_names("user-1") == ["gallery_user-1__slug-a"]


def test_stale_collection_names_empty_when_only_current_exists():
    store, client = make_store_with_client(embedder_slug="slug-b")
    client.list_collections.return_value = [SimpleNamespace(name="gallery_user-1__slug-b")]

    assert store.stale_collection_names("user-1") == []


def test_stale_collection_names_defaults_to_empty_on_backend_error():
    store, client = make_store_with_client()
    client.list_collections.side_effect = RuntimeError("boom")

    assert store.stale_collection_names("user-1") == []


def test_prune_stale_collections_deletes_each_stale_one_and_counts_them():
    store, client = make_store_with_client(embedder_slug="slug-b")
    client.list_collections.return_value = [
        SimpleNamespace(name="gallery_user-1__slug-a"),
        SimpleNamespace(name="gallery_user-1__slug-old"),
        SimpleNamespace(name="gallery_user-1__slug-b"),
    ]

    dropped = store.prune_stale_collections("user-1")

    assert dropped == 2
    assert client.delete_collection.call_count == 2
    client.delete_collection.assert_any_call("gallery_user-1__slug-a")
    client.delete_collection.assert_any_call("gallery_user-1__slug-old")
    # The active collection is never touched.
    assert "gallery_user-1__slug-b" not in [c.args[0] for c in client.delete_collection.call_args_list]


def test_prune_stale_collections_is_a_noop_when_nothing_is_stale():
    store, client = make_store_with_client(embedder_slug="slug-b")
    client.list_collections.return_value = [SimpleNamespace(name="gallery_user-1__slug-b")]

    assert store.prune_stale_collections("user-1") == 0
    client.delete_collection.assert_not_called()
