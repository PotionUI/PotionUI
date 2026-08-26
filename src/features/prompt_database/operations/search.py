"""Semantic search and near-duplicate detection over saved Prompts."""
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

import numpy as np

from src.features.prompt_database.collaborators import PromptDatabaseCollaborators

logger = logging.getLogger(__name__)


async def search(
    collaborators: PromptDatabaseCollaborators,
    user_id: str, query: str, limit: int = 5,
    base_model: Optional[str] = None, model_id: Optional[str] = None,
    source_provider: Optional[str] = None,
) -> List[Any]:
    try:
        if await collaborators.embedding_provider.is_available():
            embeddings = await collaborators.embedding_provider.embed([query])
            if embeddings:
                where = {k: v for k, v in {
                    "base_model": base_model, "model_id": model_id,
                    "source_provider": source_provider,
                }.items() if v is not None}
                ids = collaborators.vector_store.search(user_id, embeddings[0], limit, where or None)
                if ids:
                    return collaborators.repository.get_by_ids(ids, user_id)
    except Exception as exc:
        logger.warning("Prompt vector search failed; using text search: %s", exc)
    return collaborators.repository.text_search(
        user_id, query, limit, base_model, model_id, source_provider,
    )


async def find_duplicates(
    collaborators: PromptDatabaseCollaborators,
    user_id: str, threshold: float = 0.1, model_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return duplicate groups as ``{"similarity": float, "prompts": [...]}``.

    ``similarity`` is the worst-case (minimum) pairwise cosine similarity within
    the group when embeddings are available, or ``1.0`` for exact normalized-text
    matches when they aren't.
    """
    embeddings = collaborators.vector_store.get_all_embeddings(
        user_id, where={"model_id": model_id} if model_id else None,
    )
    if embeddings:
        return _find_duplicates_by_embedding(collaborators, embeddings, user_id, threshold)
    return _find_duplicates_by_text(collaborators, user_id, model_id)


def _find_duplicates_by_embedding(collaborators, embeddings, user_id, threshold):
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
            _build_duplicate_group(collaborators, user_id, [ids[i] for i in indices], similarity)
        )
    return sorted(groups, key=lambda group: len(group["prompts"]), reverse=True)


def _find_duplicates_by_text(collaborators, user_id, model_id=None):
    id_groups = defaultdict(list)
    for prompt in collaborators.repository.get_all(user_id, limit=5000, model_id=model_id):
        key = " ".join(prompt.flattened_text.lower().split())
        if key:
            id_groups[key].append(prompt.id)
    groups = [
        _build_duplicate_group(collaborators, user_id, ids, 1.0)
        for ids in id_groups.values()
        if len(ids) >= 2
    ]
    return sorted(groups, key=lambda group: len(group["prompts"]), reverse=True)


def _build_duplicate_group(collaborators, user_id, ids, similarity: float) -> Dict[str, Any]:
    prompts = collaborators.repository.get_by_ids(ids, user_id)
    prompts.sort(key=lambda p: (-(p.heart_count or 0), -(p.like_count or 0), p.created_at))
    return {
        "similarity": round(similarity, 4),
        "prompts": [prompt.to_dict() for prompt in prompts],
    }
