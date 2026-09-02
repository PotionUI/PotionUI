"""Unit coverage for embed_pending."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.prompt_database import operations
from src.features.prompt_database.collaborators import PromptDatabaseCollaborators
from tests.features.prompt_database.operations.test_mutations import make_prompt


@pytest.fixture
def dependencies():
    repository = MagicMock()
    vector_store = MagicMock()
    embedding_provider = MagicMock()
    embedding_provider.embed = AsyncMock(return_value=[[0.1, 0.2]])
    embedding_provider.is_available = AsyncMock(return_value=False)
    plugins = MagicMock()
    collaborators = PromptDatabaseCollaborators(
        repository=repository,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        plugin_registry=plugins,
        model_repository=MagicMock(),
    )
    return collaborators, repository, vector_store, embedding_provider, plugins


@pytest.mark.asyncio
async def test_embed_pending_resets_stale_flags_after_an_embedder_switch(dependencies):
    collaborators, repository, vector_store, embedding_provider, _ = dependencies
    repository.has_embedded.return_value = True
    vector_store.is_collection_empty.return_value = True
    repository.get_unembedded.return_value = [
        make_prompt("prompt-1"), make_prompt("prompt-2"),
    ]

    count = await operations.embed_pending(collaborators, "user-1")

    repository.reset_embedded.assert_called_once_with("user-1")
    assert count == 2


@pytest.mark.asyncio
async def test_embed_pending_leaves_flags_alone_when_active_namespace_has_vectors(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    repository.has_embedded.return_value = True
    vector_store.is_collection_empty.return_value = False
    repository.get_unembedded.return_value = []

    count = await operations.embed_pending(collaborators, "user-1")

    repository.reset_embedded.assert_not_called()
    assert count == 0


@pytest.mark.asyncio
async def test_embed_pending_skips_reset_check_when_nothing_was_ever_embedded(dependencies):
    collaborators, repository, vector_store, _, _ = dependencies
    repository.has_embedded.return_value = False
    repository.get_unembedded.return_value = []

    await operations.embed_pending(collaborators, "user-1")

    vector_store.is_collection_empty.assert_not_called()
    repository.reset_embedded.assert_not_called()
