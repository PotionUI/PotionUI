from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.features.content_safety.errors import BannedPromptRefused, ContentCheckUnavailable
from src.features.generation.routes import GenerationController


def controller(error):
    orchestrator = MagicMock()
    orchestrator.start_generation = AsyncMock(side_effect=error)
    return GenerationController(orchestrator, MagicMock(), MagicMock(), MagicMock())


@pytest.mark.asyncio
async def test_banned_prompt_is_a_422_with_its_own_error_code_and_no_word():
    with pytest.raises(HTTPException) as raised:
        await controller(BannedPromptRefused("This prompt was refused by this server's content policy.")).start_generation(
            MagicMock(), SimpleNamespace(id="u1")
        )

    assert raised.value.status_code == 422
    assert raised.value.detail["error"] == "banned_prompt"


@pytest.mark.asyncio
async def test_missing_content_check_is_a_503_with_its_own_error_code():
    with pytest.raises(HTTPException) as raised:
        await controller(ContentCheckUnavailable("Content check unavailable.")).start_generation(
            MagicMock(), SimpleNamespace(id="u1")
        )

    assert raised.value.status_code == 503
    assert raised.value.detail["error"] == "content_check_unavailable"
