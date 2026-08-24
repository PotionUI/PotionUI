"""GalleryPromptVectorStore: namespacing (disjoint from the two sibling
vector spaces) and upserts."""

from unittest.mock import MagicMock

from src.features.media_index.gallery_prompt_vector_store import GalleryPromptVectorStore


def make_store():
    store = GalleryPromptVectorStore.__new__(GalleryPromptVectorStore)
    collection = MagicMock()
    store.get_collection = MagicMock(return_value=collection)
    return store, collection


def test_collection_name_is_namespaced_by_user_and_embedder():
    store = GalleryPromptVectorStore(persist_dir="/tmp/unused", embedder_slug="local-bge-small")

    assert store._collection_name("user-1") == "gallery_prompts_user-1__local-bge-small"


def test_different_embedder_slugs_never_collide():
    a = GalleryPromptVectorStore(persist_dir="/tmp/unused", embedder_slug="local-bge-small")
    b = GalleryPromptVectorStore(persist_dir="/tmp/unused", embedder_slug="ollama-nomic")

    assert a._collection_name("user-1") != b._collection_name("user-1")


def test_namespace_is_disjoint_from_the_image_and_prompt_library_stores():
    from src.features.media_index.gallery_vector_store import GalleryVectorStore
    from src.features.prompt_database.vector_store import PromptVectorStore

    prompt_embed = GalleryPromptVectorStore(persist_dir="/tmp/unused", embedder_slug="slug")
    gallery = GalleryVectorStore(persist_dir="/tmp/unused", embedder_slug="slug")
    prompt_library = PromptVectorStore(persist_dir="/tmp/unused", embedder_slug="slug")

    names = {
        prompt_embed._collection_name("user-1"),
        gallery._collection_name("user-1"),
        prompt_library._collection_name("user-1"),
    }
    assert len(names) == 3


def test_add_upserts_document_and_sanitized_metadata():
    store, collection = make_store()

    store.add(
        "user-1",
        "file-1",
        [0.1, 0.2],
        "a red fox in snow",
        metadata={"generation_id": "gen-1", "nested": {"drop": "me"}},
    )

    collection.upsert.assert_called_once_with(
        ids=["file-1"],
        embeddings=[[0.1, 0.2]],
        documents=["a red fox in snow"],
        metadatas=[{"generation_id": "gen-1"}],
    )


def test_bulk_delete_is_a_noop_for_an_empty_list():
    store, collection = make_store()

    store.bulk_delete("user-1", [])

    collection.delete.assert_not_called()


def test_bulk_delete_swallows_backend_errors():
    store, collection = make_store()
    collection.delete.side_effect = RuntimeError("boom")

    store.bulk_delete("user-1", ["file-1"])  # must not raise
