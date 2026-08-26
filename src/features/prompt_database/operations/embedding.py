"""Embed a Prompt into the semantic vector store, and catch up unembedded rows."""
import logging

from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from src.features.prompt_database.records import Prompt

logger = logging.getLogger(__name__)


async def embed_prompt(collaborators: PromptDatabaseCollaborators, user_id: str, prompt: Prompt) -> bool:
    if not prompt.id:
        logger.warning("Cannot embed an unsaved Prompt without an id")
        return False
    try:
        embeddings = await collaborators.embedding_provider.embed([prompt.flattened_text])
        if not embeddings:
            return False
        collaborators.vector_store.add(
            user_id, prompt.id, embeddings[0], prompt.flattened_text,
            {
                "source_provider": prompt.source_provider or "",
                "base_model": prompt.base_model or "",
                "model_name": prompt.model_name or "",
                "model_id": prompt.model_id or "",
                "usage_hint": prompt.usage_hint or "",
            },
        )
        collaborators.repository.mark_embedded(prompt.id)
        return True
    except Exception as exc:
        logger.warning("Failed to embed prompt %s: %s", prompt.id, exc)
        return False


async def embed_pending(collaborators: PromptDatabaseCollaborators, user_id: str) -> int:
    """Embed every prompt not yet in the active embedder's vector collection.

    If rows are flagged ``embedded`` but the active-namespace collection
    is empty, the active embedder was switched since those rows were
    embedded; their flag now points at an abandoned collection, so it is
    cleared here and they are re-embedded below.
    """
    if collaborators.repository.has_embedded(user_id) and collaborators.vector_store.is_collection_empty(user_id):
        collaborators.repository.reset_embedded(user_id)
    count = 0
    for prompt in collaborators.repository.get_unembedded(user_id):
        count += int(await embed_prompt(collaborators, user_id, prompt))
    return count
