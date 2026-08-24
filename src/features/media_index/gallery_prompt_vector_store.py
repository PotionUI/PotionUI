"""ChromaDB-backed vector storage for gallery generation-prompt embeddings.

A third vector space alongside `GalleryVectorStore` (SigLIP image-content
embeddings) and `PromptVectorStore` (the user-authored prompt library):
this one embeds the actual text prompt a file was generated from, using the
same text embedder that backs prompt-database search
(`src.features.prompt_database.embedding`). Namespaced under its own
``gallery_prompts_`` collection prefix - distinct from both
``gallery_`` (image embeddings) and ``rich_prompts_`` (the prompt library) -
so neither an embedder switch nor either sibling store's stale-collection
pruning can ever touch or mix into this one.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    import chromadb

logger = logging.getLogger(__name__)


class GalleryPromptVectorStore:
    """Per-user, per-file generation-prompt text embeddings, cosine-ranked."""

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
        return f"gallery_prompts_{user_id}__{self._embedder_slug}"

    def get_collection(self, user_id: str) -> "chromadb.Collection":
        return self.client.get_or_create_collection(
            name=self._collection_name(user_id),
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        user_id: str,
        file_id: str,
        embedding: List[float],
        prompt_text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Upsert one file's prompt-text embedding into the user's collection."""
        collection = self.get_collection(user_id)
        meta = metadata or {}
        safe_meta = {
            k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))
        }
        collection.upsert(
            ids=[file_id],
            embeddings=[embedding],
            documents=[prompt_text],
            metadatas=[safe_meta] if safe_meta else None,
        )

    def bulk_delete(self, user_id: str, file_ids: List[str]) -> None:
        if not file_ids:
            return
        collection = self.get_collection(user_id)
        try:
            collection.delete(ids=file_ids)
        except Exception as e:
            logger.error(f"ChromaDB gallery-prompt bulk_delete failed: {e}")
