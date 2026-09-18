"""Semantic search and near-duplicate detection over saved Prompts."""
import asyncio
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

import numpy as np

from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from src.features.prompt_database.vector_store import DUPLICATE_SCAN_CAP, EmbeddingScan

logger = logging.getLogger(__name__)

DUPLICATE_MATCH_BLOCK_SIZE = 32


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
) -> Dict[str, Any]:
    return await asyncio.to_thread(_find_duplicates_sync, collaborators, user_id, threshold, model_id)


def _find_duplicates_sync(collaborators, user_id, threshold, model_id):
    where = {"model_id": model_id} if model_id else None
    scan = collaborators.vector_store.scan_embeddings(user_id, where=where, cap=DUPLICATE_SCAN_CAP)
    if scan.scanned >= 2:
        groups = _find_duplicates_by_embedding(collaborators, scan, user_id, threshold)
        return {
            "groups": groups,
            "scanned": scan.scanned,
            "total": scan.total,
            "partial": scan.partial,
        }

    rows = collaborators.repository.get_ids_and_text(user_id, limit=DUPLICATE_SCAN_CAP, model_id=model_id)
    groups = _group_duplicates_by_text(collaborators, user_id, rows)
    total = collaborators.repository.count(user_id, model_id=model_id)
    return {
        "groups": groups,
        "scanned": len(rows),
        "total": total,
        "partial": total > len(rows),
    }


def _iter_block_distances(vectors: np.ndarray, block_size: int):
    n = len(vectors)
    for start in range(0, n, block_size):
        end = min(start + block_size, n)
        yield start, end, 1.0 - np.clip(vectors[start:end] @ vectors.T, -1.0, 1.0)


def _find_duplicates_by_embedding(collaborators, scan: EmbeddingScan, user_id, threshold):
    ids = scan.ids
    n = len(ids)
    if n < 2:
        return []
    vectors = scan.vectors.astype(np.float64, copy=True)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors /= np.where(norms == 0, 1, norms)
    parent = list(range(n))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for start, end, block_distance in _iter_block_distances(vectors, DUPLICATE_MATCH_BLOCK_SIZE):
        for local_index in range(end - start):
            global_left = start + local_index
            row = block_distance[local_index]
            for right in np.flatnonzero(row[global_left + 1:] < threshold) + global_left + 1:
                a, b = find(global_left), find(int(right))
                if a != b:
                    parent[a] = b

    index_groups = defaultdict(list)
    for index in range(n):
        index_groups[find(index)].append(index)

    groups = []
    for indices in index_groups.values():
        if len(indices) < 2:
            continue
        group_vectors = vectors[indices]
        group_distance = 1.0 - np.clip(group_vectors @ group_vectors.T, -1.0, 1.0)
        worst_distance = max(
            group_distance[a, b]
            for a in range(len(indices))
            for b in range(a + 1, len(indices))
        )
        similarity = 1.0 - worst_distance
        groups.append(
            _build_duplicate_group(collaborators, user_id, [ids[i] for i in indices], similarity)
        )
    return sorted(groups, key=lambda group: len(group["prompts"]), reverse=True)


def _group_duplicates_by_text(collaborators, user_id, rows):
    id_groups = defaultdict(list)
    for prompt_id, flattened_text in rows:
        key = " ".join(flattened_text.lower().split())
        if key:
            id_groups[key].append(prompt_id)
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
