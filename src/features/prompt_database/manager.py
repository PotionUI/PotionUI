"""Prompt aggregate CRUD, imports, duplicate detection, and embeddings."""

from collections import defaultdict
import logging
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from src.features.prompt_database.dto import PromptRequest
from src.features.segments.dto import RichSegment
from src.platform.plugins.hooks import HookContext
from src.platform.plugins.registry import PluginRegistry
from src.features.prompt_database.embedding import EmbeddingProvider
from src.features.prompt_database.hooks import PROMPT_DATABASE_HOOKS
from src.features.prompt_database.vector_store import PromptVectorStore
from src.features.prompt_database.records import Prompt
from src.features.prompt_database.repository import PromptRepository

logger = logging.getLogger(__name__)

# The source_provider value every hand-authored prompt is filed under - what
# the "Manual" browse filter matches against and what sourceLabel() in the
# frontend already displays as a fallback for a falsy source_provider.
MANUAL_SOURCE_PROVIDER = "manual"


class PromptDatabaseManager:
    """Owns normalized prompt aggregates.

    A prompt is one channel-agnostic ordered list.  Positive/negative usage is a
    browsing hint only and never a coupled pair or generation configuration.
    """

    def __init__(
        self,
        repository: PromptRepository,
        vector_store: PromptVectorStore,
        embedding_provider: EmbeddingProvider,
        plugin_registry: PluginRegistry,
    ):
        self.repository = repository
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.plugins = plugin_registry

    def _fire_hook(self, hook: str, prompt: Prompt, provider_id: Optional[str] = None) -> None:
        context = HookContext(
            hook_name=hook,
            plugin_id="system",
            data={
                "prompt": prompt,
                "segments": prompt.segments,
                "user_id": prompt.user_id,
                "provider_id": provider_id,
            },
        )
        self.plugins.execute_hook(hook, context=context)

    @staticmethod
    def _from_request(user_id: str, request: PromptRequest, prompt_id: Optional[str] = None) -> Prompt:
        values = request.model_dump()
        segments = values.pop("segments")
        return Prompt(
            id=prompt_id,
            user_id=user_id,
            segments=[s if isinstance(s, RichSegment) else RichSegment(**s) for s in segments],
            **values,
        )

    async def create_prompt(self, user_id: str, request: PromptRequest) -> Prompt:
        candidate = self._from_request(user_id, request)
        # A request with no source_provider (the /api/prompts create form the
        # Prompt Library's "New prompt" flow sends) is a hand-authored prompt,
        # never an unset field - default it so the "Manual" source filter and
        # sourceLabel()'s badge agree with what actually got persisted.
        if not candidate.source_provider:
            candidate.source_provider = MANUAL_SOURCE_PROVIDER
        self._fire_hook(PROMPT_DATABASE_HOOKS.before_save, candidate, candidate.source_provider)
        saved = self.repository.create(candidate)
        self._fire_hook(PROMPT_DATABASE_HOOKS.after_save, saved, saved.source_provider)
        saved.embedded = await self._embed_prompt(user_id, saved)
        return saved

    async def replace_prompt(self, user_id: str, prompt_id: str, request: PromptRequest) -> Optional[Prompt]:
        existing = self.repository.get_by_id(prompt_id, user_id)
        if existing is None:
            return None
        values = request.model_dump()
        for field in PromptRequest.model_fields:
            if field in {"segments"} or field in request.model_fields_set:
                continue
            if hasattr(existing, field):
                values[field] = getattr(existing, field)
        candidate = self._from_request(user_id, PromptRequest(**values), prompt_id)
        self._fire_hook(PROMPT_DATABASE_HOOKS.before_save, candidate, candidate.source_provider)
        saved = self.repository.update(prompt_id, user_id, candidate)
        if saved is None:
            return None
        self._fire_hook(PROMPT_DATABASE_HOOKS.after_save, saved, saved.source_provider)
        saved.embedded = await self._embed_prompt(user_id, saved)
        return saved

    async def add_prompt(
        self,
        user_id: str,
        prompt_text: str,
        model_id: Optional[str] = None,
        source_provider: str = MANUAL_SOURCE_PROVIDER,
        name: Optional[str] = None,
        usage_hint: Optional[str] = None,
        **metadata: Any,
    ) -> Prompt:
        """Convenience used by chat tools; creates exactly one detached prompt."""
        allowed = set(PromptRequest.model_fields) - {"segments", "name", "usage_hint", "source_provider"}
        values = {key: value for key, value in metadata.items() if key in allowed}
        request = PromptRequest(
            name=name,
            usage_hint=usage_hint,
            segments=[RichSegment(content=prompt_text)],
            source_provider=source_provider,
            model_id=model_id,
            **values,
        )
        return await self.create_prompt(user_id, request)

    async def search(
        self, user_id: str, query: str, limit: int = 5,
        base_model: Optional[str] = None, model_id: Optional[str] = None,
        source_provider: Optional[str] = None,
    ) -> List[Prompt]:
        try:
            if await self.embedding_provider.is_available():
                embeddings = await self.embedding_provider.embed([query])
                if embeddings:
                    where = {k: v for k, v in {
                        "base_model": base_model, "model_id": model_id,
                        "source_provider": source_provider,
                    }.items() if v is not None}
                    ids = self.vector_store.search(user_id, embeddings[0], limit, where or None)
                    if ids:
                        return self.repository.get_by_ids(ids, user_id)
        except Exception as exc:
            logger.warning("Prompt vector search failed; using text search: %s", exc)
        return self.repository.text_search(
            user_id, query, limit, base_model, model_id, source_provider,
        )

    def list_prompts(self, user_id: str, **filters: Any) -> Dict[str, Any]:
        items = self.repository.get_all(user_id=user_id, **filters)
        total = self.repository.count(
            user_id, filters.get("source_provider"), filters.get("model_id"),
            filters.get("base_model"), filters.get("usage_hint"), filters.get("collection_id"),
        )
        return {
            "items": [item.to_dict() for item in items], "total": total,
            "limit": filters.get("limit", 20), "offset": filters.get("offset", 0),
        }

    def get_prompt(self, user_id: str, prompt_id: str) -> Optional[Prompt]:
        return self.repository.get_by_id(prompt_id, user_id)

    def delete_prompt(self, user_id: str, prompt_id: str) -> bool:
        deleted = self.repository.delete(prompt_id, user_id)
        if deleted:
            self.vector_store.delete(user_id, prompt_id)
        return deleted

    def bulk_delete_prompts(self, user_id: str, prompt_ids: Sequence[str]) -> int:
        count = self.repository.bulk_delete(prompt_ids, user_id)
        if count:
            self.vector_store.bulk_delete(user_id, list(prompt_ids))
        return count

    def purge_model_prompts(self, user_id: str, model_id: str) -> int:
        count, ids = self.repository.delete_by_model(model_id, user_id)
        if ids:
            self.vector_store.bulk_delete(user_id, ids)
        return count

    async def embed_pending(self, user_id: str) -> int:
        """Embed every prompt not yet in the active embedder's vector collection.

        If rows are flagged ``embedded`` but the active-namespace collection
        is empty, the active embedder was switched since those rows were
        embedded; their flag now points at an abandoned collection, so it is
        cleared here and they are re-embedded below.
        """
        if self.repository.has_embedded(user_id) and self.vector_store.is_collection_empty(user_id):
            self.repository.reset_embedded(user_id)
        count = 0
        for prompt in self.repository.get_unembedded(user_id):
            count += int(await self._embed_prompt(user_id, prompt))
        return count

    async def _embed_prompt(self, user_id: str, prompt: Prompt) -> bool:
        if not prompt.id:
            logger.warning("Cannot embed an unsaved Prompt without an id")
            return False
        try:
            embeddings = await self.embedding_provider.embed([prompt.flattened_text])
            if not embeddings:
                return False
            self.vector_store.add(
                user_id, prompt.id, embeddings[0], prompt.flattened_text,
                {
                    "source_provider": prompt.source_provider or "",
                    "base_model": prompt.base_model or "",
                    "model_name": prompt.model_name or "",
                    "model_id": prompt.model_id or "",
                    "usage_hint": prompt.usage_hint or "",
                },
            )
            self.repository.mark_embedded(prompt.id)
            return True
        except Exception as exc:
            logger.warning("Failed to embed prompt %s: %s", prompt.id, exc)
            return False

    async def find_duplicates(
        self, user_id: str, threshold: float = 0.1, model_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return duplicate groups as ``{"similarity": float, "prompts": [...]}``.

        ``similarity`` is the worst-case (minimum) pairwise cosine similarity within
        the group when embeddings are available, or ``1.0`` for exact normalized-text
        matches when they aren't.
        """
        embeddings = self.vector_store.get_all_embeddings(
            user_id, where={"model_id": model_id} if model_id else None,
        )
        if embeddings:
            return self._find_duplicates_by_embedding(embeddings, user_id, threshold)
        return self._find_duplicates_by_text(user_id, model_id)

    def _find_duplicates_by_embedding(self, embeddings, user_id, threshold):
        ids = list(embeddings)[:5000]
        if len(ids) < 2:
            return []
        vectors = np.asarray([embeddings[prompt_id] for prompt_id in ids], dtype=np.float64)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors /= np.where(norms == 0, 1, norms)
        distance = 1.0 - np.clip(vectors @ vectors.T, -1.0, 1.0)
        parent = list(range(len(ids)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        for left in range(len(ids)):
            for right in range(left + 1, len(ids)):
                if distance[left, right] < threshold:
                    a, b = find(left), find(right)
                    if a != b:
                        parent[a] = b

        index_groups = defaultdict(list)
        for index in range(len(ids)):
            index_groups[find(index)].append(index)

        groups = []
        for indices in index_groups.values():
            if len(indices) < 2:
                continue
            worst_distance = max(
                distance[indices[a], indices[b]]
                for a in range(len(indices))
                for b in range(a + 1, len(indices))
            )
            similarity = 1.0 - worst_distance
            groups.append(
                self._build_duplicate_group(user_id, [ids[i] for i in indices], similarity)
            )
        return sorted(groups, key=lambda group: len(group["prompts"]), reverse=True)

    def _find_duplicates_by_text(self, user_id, model_id=None):
        id_groups = defaultdict(list)
        for prompt in self.repository.get_all(user_id, limit=5000, model_id=model_id):
            key = " ".join(prompt.flattened_text.lower().split())
            if key:
                id_groups[key].append(prompt.id)
        groups = [
            self._build_duplicate_group(user_id, ids, 1.0)
            for ids in id_groups.values()
            if len(ids) >= 2
        ]
        return sorted(groups, key=lambda group: len(group["prompts"]), reverse=True)

    def _build_duplicate_group(self, user_id, ids, similarity: float) -> Dict[str, Any]:
        prompts = self.repository.get_by_ids(ids, user_id)
        prompts.sort(key=lambda p: (-(p.heart_count or 0), -(p.like_count or 0), p.created_at))
        return {
            "similarity": round(similarity, 4),
            "prompts": [prompt.to_dict() for prompt in prompts],
        }


# A clearer name for new integrations while retaining the existing injected
# context attribute (`prompt_database_manager`) used by chat modes.
PromptManager = PromptDatabaseManager
