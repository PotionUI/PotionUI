"""ChromaDB-backed vector storage for gallery image embeddings.

Mirrors ``PromptVectorStore``: lazy client construction (``chromadb`` is heavy
to import and generation never touches this store) and per-user, per-embedder
collection namespacing so switching the vision model - a different,
non-comparable vector space - can never mix vectors across embedders.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    import chromadb

logger = logging.getLogger(__name__)


class GalleryVectorStore:
    """Per-user gallery image embeddings, cosine-ranked."""

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
        return f"gallery_{user_id}__{self._embedder_slug}"

    def get_collection(self, user_id: str) -> "chromadb.Collection":
        return self.client.get_or_create_collection(
            name=self._collection_name(user_id),
            metadata={"hnsw:space": "cosine"},
        )

    def all_generation_ids(self, user_id: str) -> List[str]:
        """Every distinct ``generation_id`` embedded in the user's collection.

        A plain metadata fetch, not a similarity query - Chroma answers it
        without ranking, so its cost doesn't scale with how deep a caller
        needs to page. Lets a caller compute an exact filtered total without
        an ANN query sized to the whole collection.
        """
        collection = self.get_collection(user_id)
        try:
            result = collection.get(include=["metadatas"])
        except Exception as e:
            logger.error(f"ChromaDB gallery all_generation_ids failed: {e}")
            return []
        metadatas = result.get("metadatas") or []
        seen = set()
        ordered: List[str] = []
        for meta in metadatas:
            generation_id = (meta or {}).get("generation_id")
            if generation_id and generation_id not in seen:
                seen.add(generation_id)
                ordered.append(generation_id)
        return ordered

    def collection_size(self, user_id: str) -> int:
        """Vector count in the active-namespace collection, 0 on any failure."""
        try:
            return self.get_collection(user_id).count()
        except Exception as e:
            logger.error(f"ChromaDB gallery collection_size failed: {e}")
            return 0

    def is_collection_empty(self, user_id: str) -> bool:
        """Whether the active-namespace collection holds no vectors yet.

        Used to detect a vision-model switch: the new namespace starts empty
        even though the media index queue may still hold rows flagged done
        from a previous embedder's now-abandoned collection.
        """
        try:
            return self.get_collection(user_id).count() == 0
        except Exception as e:
            logger.error(f"ChromaDB is_collection_empty failed: {e}")
            return False

    def add(
        self,
        user_id: str,
        file_id: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Upsert one file embedding into the user's collection."""
        collection = self.get_collection(user_id)
        meta = metadata or {}
        safe_meta = {
            k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))
        }
        collection.upsert(
            ids=[file_id],
            embeddings=[embedding],
            metadatas=[safe_meta] if safe_meta else None,
        )

    def search(
        self,
        user_id: str,
        query_embedding: List[float],
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """Cosine similarity search.

        Returns ``[{file_id, generation_id, similarity}]`` ranked best-first.
        ``similarity`` is plain cosine (1 - Chroma's cosine distance); with
        SigLIP embeddings matching values are low (~0.0-0.3), so callers rank
        relatively and never threshold on an absolute value.
        """
        collection = self.get_collection(user_id)
        try:
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                include=["metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"ChromaDB gallery search failed: {e}")
            return []

        ids = results.get("ids", [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        hits: List[Dict[str, Any]] = []
        for index, file_id in enumerate(ids):
            meta = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
            distance = float(distances[index]) if index < len(distances) else 1.0
            hits.append(
                {
                    "file_id": file_id,
                    "generation_id": meta.get("generation_id") or None,
                    "similarity": 1.0 - distance,
                }
            )
        return hits

    def bulk_delete(self, user_id: str, file_ids: List[str]) -> None:
        if not file_ids:
            return
        collection = self.get_collection(user_id)
        try:
            collection.delete(ids=file_ids)
        except Exception as e:
            logger.error(f"ChromaDB gallery bulk_delete failed: {e}")

    def delete_collection(self, user_id: str, collection_name: Optional[str] = None) -> None:
        """Drop one of this user's gallery collections - the active
        (current-embedder-namespaced) one by default, or an explicit stale
        one from ``stale_collection_names``."""
        name = collection_name or self._collection_name(user_id)
        try:
            self.client.delete_collection(name)
        except Exception as e:
            logger.error(f"ChromaDB gallery delete_collection failed for '{name}': {e}")

    def stale_collection_names(self, user_id: str) -> List[str]:
        """This user's gallery collections under an embedder identity OTHER
        than the currently configured one.

        Collections are namespaced per (user, embedder_slug) - see the module
        docstring - so switching the vision model leaves the previous
        embedder's collection behind under its own name once the active one
        (``self._embedder_slug``) has been rebuilt. Used to find what a
        completed rebuild is safe to drop; never call ``delete_collection``
        on the result until the rebuild has actually finished (see
        ``MediaIndexer._settle_gallery_rebuilds``).
        """
        prefix = f"gallery_{user_id}__"
        current = self._collection_name(user_id)
        try:
            collections = self.client.list_collections()
        except Exception as e:
            logger.error(f"ChromaDB gallery list_collections failed: {e}")
            return []
        return [c.name for c in collections if c.name.startswith(prefix) and c.name != current]

    def prune_stale_collections(self, user_id: str) -> int:
        """Drop every one of this user's gallery collections left behind by
        a since-rebuilt vision-model switch. Returns how many were dropped."""
        names = self.stale_collection_names(user_id)
        for name in names:
            self.delete_collection(user_id, collection_name=name)
        return len(names)
