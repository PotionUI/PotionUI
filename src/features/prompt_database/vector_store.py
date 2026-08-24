"""ChromaDB-backed vector storage for prompt embeddings."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    import chromadb

logger = logging.getLogger(__name__)


class PromptVectorStore:
    """ChromaDB-backed vector storage for prompt embeddings.

    ``chromadb`` and its ``PersistentClient`` are heavy to import (telemetry,
    OpenTelemetry exporters, ...) and generation never touches this store, so
    construction is deferred to the first real use rather than paid at process
    boot. ``self.client`` stays a normal attribute access via a property so
    every caller keeps working unchanged.
    """

    def __init__(self, persist_dir: str = "storage/chromadb", embedder_slug: str = "default"):
        self._persist_dir = persist_dir
        self._embedder_slug = embedder_slug
        self._client: Optional["chromadb.ClientAPI"] = None
        self._client_lock = threading.Lock()

    @property
    def client(self) -> "chromadb.ClientAPI":
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import chromadb

                    self._client = chromadb.PersistentClient(path=self._persist_dir)
        return self._client

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

    def get_all_embeddings(
        self,
        user_id: str,
        where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[float]]:
        """Retrieve all prompt IDs and their embedding vectors from the user's collection.

        Args:
            user_id: The user whose collection to query.
            where: Optional ChromaDB metadata filter.

        Returns:
            Dict mapping prompt_id to embedding vector.
        """
        collection = self.get_collection(user_id)
        try:
            kwargs: Dict[str, Any] = {"include": ["embeddings"]}
            if where:
                kwargs["where"] = where
            results = collection.get(**kwargs)
            ids = results.get("ids", [])
            embeddings = results.get("embeddings", [])
            # Chroma may return embeddings as a NumPy array. Array truth-value
            # checks (``if not embeddings``) raise when it contains multiple
            # values, so test absence/emptiness explicitly.
            if ids is None or embeddings is None:
                return {}
            if len(ids) == 0 or len(embeddings) == 0:
                return {}

            # Keep the vector-store boundary stable regardless of whether the
            # Chroma client returns Python lists or NumPy arrays.
            return {
                prompt_id: embedding.tolist()
                if hasattr(embedding, "tolist")
                else list(embedding)
                for prompt_id, embedding in zip(ids, embeddings)
            }
        except Exception as e:
            logger.error(f"ChromaDB get_all_embeddings failed: {e}")
            return {}

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
