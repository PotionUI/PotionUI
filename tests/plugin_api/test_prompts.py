"""Tests for the `src.plugin_api.prompts` prompt-importer surface."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.plugin_api.prompts import PromptImportOutcome, create_prompt_for_user


@pytest.mark.asyncio
async def test_create_prompt_for_user_delegates_through_add_prompt_and_sets_source_provider():
    manager = SimpleNamespace(add_prompt=AsyncMock(
        return_value=SimpleNamespace(to_dict=lambda: {"id": "prompt-1", "source_provider": "fixture-plugin"})
    ))
    container = SimpleNamespace(prompt_database_manager=manager)

    with patch("src.plugin_api.prompts.get_container", return_value=container):
        result = await create_prompt_for_user(
            "user-1", "a red fox", source_provider="fixture-plugin", model_name="SDXL",
        )

    assert result == {"id": "prompt-1", "source_provider": "fixture-plugin"}
    manager.add_prompt.assert_awaited_once_with(
        "user-1", "a red fox",
        name=None, usage_hint=None, source_provider="fixture-plugin",
        model_name="SDXL", base_model=None, source_url=None,
    )


def test_prompt_import_outcome_defaults_to_no_items_and_no_error():
    outcome = PromptImportOutcome(imported=3, skipped=1, total=4)

    assert outcome.items == []
    assert outcome.error is None
