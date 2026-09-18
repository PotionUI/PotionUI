"""ChromaDB-backed vector storage for prompt embeddings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np

from src.platform.vector.chroma_client import ChromaClientProvider

if TYPE_CHECKING:
    import chromadb

logger = logging.getLogger(__name__)

DUPLICATE_SCAN_CAP = 5000
DUPLICATE_SCAN_PAGE_SIZE = 500


@dataclass
class EmbeddingScan:
    ids: List[str]
    vectors: np.ndarray
    scanned: int
    total: int
    partial: bool


class PromptVectorStore:
    """ChromaDB-backed vector storage for prompt embeddings.

    ``chromadb`` and its ``PersistentClient`` are heavy to import (telemetry,
    OpenTelemetry exporters, ...) and generation never touches this store, so
    construction is deferred to the first real use rather than paid at process
    boot - via a ``ChromaClientProvider`` (see
    ``src.platform.vector.chroma_client``), shared with the gallery vector
    stores when they persist to the same directory. ``self.client`` stays a
    normal attribute access via a property so every caller keeps working
    unchanged.
    """

    def __init__(
        self,
        persist_dir: str = "storage/chromadb",
        embedder_slug: str = "default",
        client_provider: Optional[ChromaClientProvider] = None,
    ):
        self._persist_dir = persist_dir
        self._embedder_slug = embedder_slug
        # Test-injection override only (see the `client` property) - stays
        # None in production, where the provider is the sole cache.
        self._client: Optional["chromadb.ClientAPI"] = None
        self._client_provider = client_provider or ChromaClientProvider(persist_dir)

    @property
    def client(self) -> "chromadb.ClientAPI":
        # `self._client` is a test-injection override, never a production
        # cache: production reads delegate to the provider's cache on every
        # access, so a store never keeps serving a client the provider has
        # since closed via ChromaClientProvider.close().
        if self._client is not None:
            return self._client
        return self._client_provider.get()

    def _collection_name(self, user_id: str) -> str:
        return f"rich_prompts_{user_id}__{self._embedder_slug}"

    def get_collection(self, user_id: str) -> "chromadb.Collection":
        """Get or create the per-user, per-embedder collection.

        The embedder identity is part of the namespace so switching providers
        or models (different vector spaces, non-comparable) can never mix
        their vectors together or with vectors left behind by a prior
        embedder - the same reasoning that put the post-reset per-user
        namespace here for the retired paired-prompt schema.
        """
        return self.client.get_or_create_collection(
            name=self._collection_name(user_id),
            metadata={"hnsw:space": "cosine"},
        )

    def is_collection_empty(self, user_id: str) -> bool:
        """Whether the active-namespace collection holds no vectors yet.

        Used to detect a provider/model switch: the new namespace starts
        empty even though the repository may still have rows flagged
        ``embedded`` from a previous embedder's now-abandoned collection.
        """
        try:
            return self.get_collection(user_id).count() == 0
        except Exception as e:
            logger.error(f"ChromaDB is_collection_empty failed: {e}")
            return False

    def add(
        self,
        user_id: str,
        prompt_id: str,
        embedding: List[float],
        prompt_text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a prompt embedding to the user's collection."""
        collection = self.get_collection(user_id)
        meta = metadata or {}
        # ChromaDB metadata values must be str, int, float, or bool
        safe_meta = {
            k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))
        }
        collection.upsert(
            ids=[prompt_id],
            embeddings=[embedding],
            documents=[prompt_text],
            metadatas=[safe_meta] if safe_meta else None,
        )

    def search(
        self,
        user_id: str,
        query_embedding: List[float],
        limit: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Similarity search. Returns list of prompt IDs ranked by relevance."""
        collection = self.get_collection(user_id)
        kwargs: Dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": limit,
        }
        if where:
            kwargs["where"] = where
        try:
            results = collection.query(**kwargs)
            return results.get("ids", [[]])[0]
        except Exception as e:
            logger.error(f"ChromaDB search failed: {e}")
            return []

    def scan_embeddings(
        self,
        user_id: str,
        where: Optional[Dict[str, Any]] = None,
        cap: int = DUPLICATE_SCAN_CAP,
        page_size: int = DUPLICATE_SCAN_PAGE_SIZE,
    ) -> EmbeddingScan:
        collection = self.get_collection(user_id)
        try:
            total = collection.count()
        except Exception as e:
            logger.error(f"ChromaDB count failed: {e}")
            total = 0

        ids: List[str] = []
        vector_pages: List[np.ndarray] = []
        offset = 0
        partial = False
        while len(ids) < cap:
            limit = min(page_size, cap - len(ids))
            kwargs: Dict[str, Any] = {"include": ["embeddings"], "limit": limit, "offset": offset}
            if where:
                kwargs["where"] = where
            try:
                results = collection.get(**kwargs)
            except Exception as e:
                logger.error(f"ChromaDB scan_embeddings failed: {e}")
                break
            page_ids = list(results.get("ids") or [])
            page_embeddings = results.get("embeddings")
            if not page_ids or page_embeddings is None or len(page_embeddings) == 0:
                break
            ids.extend(page_ids)
            vector_pages.append(np.asarray(page_embeddings, dtype=np.float64))
            offset += len(page_ids)
            if len(page_ids) < limit:
                break
        else:
            partial = True

        vectors = (
            np.concatenate(vector_pages, axis=0)
            if vector_pages
            else np.empty((0, 0), dtype=np.float64)
        )
        return EmbeddingScan(ids=ids, vectors=vectors, scanned=len(ids), total=total, partial=partial)

    def bulk_delete(self, user_id: str, prompt_ids: List[str]) -> None:
        """Remove multiple prompt embeddings at once."""
        if not prompt_ids:
            return
        collection = self.get_collection(user_id)
        try:
            collection.delete(ids=prompt_ids)
        except Exception as e:
            logger.error(f"ChromaDB bulk_delete failed: {e}")

    def delete(self, user_id: str, prompt_id: str) -> None:
        """Remove a prompt embedding."""
        collection = self.get_collection(user_id)
        try:
            collection.delete(ids=[prompt_id])
        except Exception as e:
            logger.error(f"ChromaDB delete failed: {e}")

    def delete_collection(self, user_id: str) -> None:
        """Delete entire user collection."""
        try:
            self.client.delete_collection(self._collection_name(user_id))
        except Exception as e:
            logger.error(f"ChromaDB delete_collection failed: {e}")
