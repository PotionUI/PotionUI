from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.prompt_database.operations import search


def collaborators(restricted):
    prompts = [SimpleNamespace(id="clean", nsfw=False), SimpleNamespace(id="explicit", nsfw=True)]
    embedding = MagicMock()
    embedding.is_available = AsyncMock(return_value=False)
    repository = MagicMock()
    repository.text_search.return_value = prompts
    return SimpleNamespace(
        embedding_provider=embedding,
        repository=repository,
        vector_store=MagicMock(),
        content_safety=SimpleNamespace(is_restricted=lambda user_id: restricted),
    )


@pytest.mark.asyncio
async def test_restricted_search_never_returns_nsfw_prompts_for_any_caller():
    found = await search(collaborators(True), "kid", "fox")

    assert [prompt.id for prompt in found] == ["clean"]


@pytest.mark.asyncio
async def test_unrestricted_search_is_unchanged():
    found = await search(collaborators(False), "adult", "fox")

    assert [prompt.id for prompt in found] == ["clean", "explicit"]


@pytest.mark.asyncio
async def test_search_without_a_content_safety_manager_is_unchanged():
    bundle = collaborators(True)
    bundle.content_safety = None

    assert len(await search(bundle, "u", "fox")) == 2
