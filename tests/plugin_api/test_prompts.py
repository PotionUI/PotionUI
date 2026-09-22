"""Tests for the `src.plugin_api.prompts` prompt-importer surface."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.plugin_api.prompts import (
    PromptImportOutcome,
    create_prompt_for_user,
    find_prompt_source_ids,
    import_prompts_for_user,
)


def _fake_prompt(**fields):
    return SimpleNamespace(to_dict=lambda: dict(fields))


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
        source_id=None, source_url=None, source_group_id=None,
        model_name="SDXL", base_model=None, cfg_scale=None, steps=None, sampler=None,
        width=None, height=None, nsfw=False, tags=[], metadata={},
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


def test_find_prompt_source_ids_delegates_to_repository():
    repository = Mock(get_source_ids=Mock(return_value={"123", "456"}))
    collaborators = SimpleNamespace(repository=repository)
    container = SimpleNamespace(prompt_database=collaborators)

    with patch("src.plugin_api.prompts.get_container", return_value=container):
        result = find_prompt_source_ids("user-1", "civitai-provider", model_id="model-9")

    assert result == {"123", "456"}
    repository.get_source_ids.assert_called_once_with("user-1", "civitai-provider", model_id="model-9")


@pytest.mark.asyncio
async def test_import_prompts_for_user_skips_known_source_ids():
    repository = Mock(get_source_ids=Mock(return_value={"1"}))
    collaborators = SimpleNamespace(repository=repository)
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(side_effect=lambda *a, **k: _fake_prompt(id="p", **k)))

    entries = [
        {"prompt": "a fox", "source_id": "1", "model_id": "model-9"},
        {"prompt": "a wolf", "source_id": "2", "model_id": "model-9"},
    ]

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await import_prompts_for_user(
            "user-1", entries, source_provider="civitai-provider",
        )

    assert result == {"created": 1, "skipped_duplicates": 1}
    mock_operations.add_prompt.assert_awaited_once()
    assert mock_operations.add_prompt.await_args.kwargs["source_id"] == "2"


@pytest.mark.asyncio
async def test_import_prompts_for_user_pairs_negative_under_shared_group_id():
    repository = Mock(get_source_ids=Mock(return_value=set()))
    collaborators = SimpleNamespace(repository=repository)
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(side_effect=lambda *a, **k: _fake_prompt(id="p", **k)))

    entries = [
        {"prompt": "a fox", "negative_prompt": "blurry", "source_id": "10", "model_id": "model-9"},
    ]

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await import_prompts_for_user(
            "user-1", entries, source_provider="civitai-provider",
        )

    assert result == {"created": 2, "skipped_duplicates": 0}
    assert mock_operations.add_prompt.await_count == 2

    positive_call, negative_call = mock_operations.add_prompt.await_args_list
    assert positive_call.kwargs["usage_hint"] == "positive"
    assert negative_call.kwargs["usage_hint"] == "negative"
    assert positive_call.kwargs["source_group_id"] is not None
    assert positive_call.kwargs["source_group_id"] == negative_call.kwargs["source_group_id"]
    assert positive_call.args[2] == "a fox"
    assert negative_call.args[2] == "blurry"


@pytest.mark.asyncio
async def test_import_prompts_for_user_without_negative_has_no_group_id():
    repository = Mock(get_source_ids=Mock(return_value=set()))
    collaborators = SimpleNamespace(repository=repository)
    container = SimpleNamespace(prompt_database=collaborators)
    mock_operations = Mock(add_prompt=AsyncMock(side_effect=lambda *a, **k: _fake_prompt(id="p", **k)))

    entries = [{"prompt": "a fox", "source_id": "10", "model_id": "model-9"}]

    with patch("src.plugin_api.prompts.get_container", return_value=container), \
         patch("src.plugin_api.prompts.operations", mock_operations):
        result = await import_prompts_for_user(
            "user-1", entries, source_provider="civitai-provider",
        )

    assert result == {"created": 1, "skipped_duplicates": 0}
    call = mock_operations.add_prompt.await_args
    assert call.kwargs["source_group_id"] is None
    assert call.kwargs["usage_hint"] is None


@pytest.mark.asyncio
async def test_import_prompts_for_user_empty_entries_is_a_no_op():
    with patch("src.plugin_api.prompts.get_container") as mock_get_container:
        result = await import_prompts_for_user("user-1", [], source_provider="civitai-provider")

    assert result == {"created": 0, "skipped_duplicates": 0}
    mock_get_container.assert_not_called()
