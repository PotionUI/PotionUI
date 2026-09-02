"""Tests for the `src.plugin_api.prompts` prompt-importer surface."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.plugin_api.prompts import PromptImportOutcome, create_prompt_for_user


@pytest.mark.asyncio
async def test_create_prompt_for_user_delegates_through_add_prompt_and_sets_source_provider():
    collaborators = SimpleNamespace()
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(
        return_value=SimpleNamespace(to_dict=lambda: {"id": "prompt-1", "source_provider": "fixture-plugin"})
    ))

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await create_prompt_for_user(
            "user-1", "a red fox", source_provider="fixture-plugin", model_name="SDXL",
        )

    assert result == {"id": "prompt-1", "source_provider": "fixture-plugin"}
    mock_operations.add_prompt.assert_awaited_once_with(
        collaborators, "user-1", "a red fox",
        model_id=None, name=None, usage_hint=None, source_provider="fixture-plugin",
        model_name="SDXL", base_model=None, source_url=None,
    )


@pytest.mark.asyncio
async def test_create_prompt_for_user_forwards_model_id_to_add_prompt():
    collaborators = SimpleNamespace()
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(
        return_value=SimpleNamespace(to_dict=lambda: {"id": "prompt-1", "model_id": "model-9"})
    ))

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await create_prompt_for_user(
            "user-1", "a red fox", source_provider="fixture-plugin", model_id="model-9",
        )

    assert result["model_id"] == "model-9"
    assert mock_operations.add_prompt.await_args.kwargs["model_id"] == "model-9"


def test_prompt_import_outcome_defaults_to_no_items_and_no_error():
    outcome = PromptImportOutcome(imported=3, skipped=1, total=4)

    assert outcome.items == []
    assert outcome.error is None
