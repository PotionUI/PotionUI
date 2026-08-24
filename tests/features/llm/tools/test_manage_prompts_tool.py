"""Tests for approval-gated detached rich Prompt tools."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.segments.dto import RichSegment
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.manage_prompts_tool import (
    AddPromptTool,
    DeletePromptTool,
    EditPromptTool,
)
from src.features.prompt_database.records import Prompt


def make_context(manager=None, user_id: str = "user-1") -> ToolContext:
    return ToolContext(user_id=user_id, prompt_database_manager=manager)


def make_prompt(prompt_id: str = "prompt-1") -> Prompt:
    return Prompt(
        id=prompt_id,
        user_id="user-1",
        name="Study",
        usage_hint="positive",
        segments=[
            RichSegment(content="a fox", name="Subject", color="#d97706"),
            RichSegment(type="break"),
        ],
        flattened_text="a fox BREAK",
        source_provider="llm_tool",
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


def test_add_schema_requires_complete_segment_array_not_paired_text_fields():
    schema = AddPromptTool().parameters

    assert schema["required"] == ["segments"]
    assert schema["properties"]["segments"]["minItems"] == 1
    assert "prompt" not in schema["properties"]
    assert "negative_prompt" not in schema["properties"]
    assert schema["properties"]["usage_hint"]["enum"] == ["positive", "negative"]


def test_edit_schema_replaces_aggregate_without_negative_pair_contract():
    schema = EditPromptTool().parameters

    assert schema["required"] == ["prompt_id"]
    assert "segments" in schema["properties"]
    assert "prompt" not in schema["properties"]
    assert "negative_prompt" not in schema["properties"]


@pytest.mark.asyncio
async def test_add_proposal_round_trips_rich_ordered_segments_without_mutating():
    manager = MagicMock()
    result = await AddPromptTool().execute(
        make_context(manager),
        name="Fox study",
        usage_hint="negative",
        segments=[
            {
                "type": "content",
                "content": "blurry",
                "enabled": False,
                "name": "Quality",
                "color": "#ef4444",
                "description": "Avoid this",
            },
            {"type": "break", "content": ""},
        ],
        tags=["quality"],
    )

    assert result.success is True
    proposal = json.loads(result.data)["proposal"]
    assert proposal["usage_hint"] == "negative"
    assert [segment["type"] for segment in proposal["segments"]] == ["content", "break"]
    assert proposal["segments"][0]["enabled"] is False
    assert proposal["segments"][0]["name"] == "Quality"
    assert proposal["segments"][0]["color"] == "#ef4444"
    assert "negative_prompt" not in proposal
    manager.create_prompt.assert_not_called()


@pytest.mark.asyncio
async def test_add_rejects_an_empty_aggregate():
    result = await AddPromptTool().execute(make_context(MagicMock()), segments=[])

    assert result.success is False
    assert result.error is not None
    assert "segment" in result.error.lower()


@pytest.mark.asyncio
async def test_add_confirmed_calls_aggregate_manager():
    manager = MagicMock()
    manager.create_prompt = AsyncMock(return_value=make_prompt("saved-1"))

    result = await AddPromptTool().execute_confirmed(
        make_context(manager, "owner-1"),
        name="Saved composition",
        segments=[{"content": "first"}, {"content": "second"}],
    )

    assert result.success is True
    assert json.loads(result.data)["prompt_id"] == "saved-1"
    manager.create_prompt.assert_awaited_once()
    user_id, request = manager.create_prompt.await_args.args
    assert user_id == "owner-1"
    assert [segment.content for segment in request.segments] == ["first", "second"]
    assert not hasattr(request, "negative_prompt")


@pytest.mark.asyncio
async def test_edit_proposal_shows_old_and_new_ordered_aggregates():
    manager = MagicMock()
    manager.get_prompt.return_value = make_prompt()

    result = await EditPromptTool().execute(
        make_context(manager),
        prompt_id="prompt-1",
        name="Reworked",
        usage_hint="negative",
        segments=[{"content": "low quality", "name": "Avoid"}],
    )

    assert result.success is True
    proposal = json.loads(result.data)["proposal"]
    assert [item["type"] for item in proposal["old"]["segments"]] == [
        "content",
        "break",
    ]
    assert proposal["new"]["segments"][0]["content"] == "low quality"
    assert proposal["new"]["usage_hint"] == "negative"
    assert "negative_prompt" not in json.dumps(proposal)


@pytest.mark.asyncio
async def test_edit_confirmed_delegates_atomic_replacement():
    existing = make_prompt()
    manager = MagicMock()
    manager.get_prompt.return_value = existing
    manager.replace_prompt = AsyncMock(return_value=existing)

    result = await EditPromptTool().execute_confirmed(
        make_context(manager),
        prompt_id="prompt-1",
        segments=[{"content": "replacement"}],
    )

    assert result.success is True
    manager.replace_prompt.assert_awaited_once()
    user_id, prompt_id, request = manager.replace_prompt.await_args.args
    assert (user_id, prompt_id) == ("user-1", "prompt-1")
    assert [segment.content for segment in request.segments] == ["replacement"]


@pytest.mark.asyncio
async def test_edit_requires_an_existing_prompt_and_at_least_one_change():
    manager = MagicMock()
    manager.get_prompt.return_value = None
    missing = await EditPromptTool().execute(
        make_context(manager), prompt_id="missing", segments=[{"content": "x"}]
    )
    assert missing.success is False
    assert missing.error is not None
    assert "not found" in missing.error

    manager.get_prompt.return_value = make_prompt()
    unchanged = await EditPromptTool().execute(make_context(manager), prompt_id="prompt-1")
    assert unchanged.success is False
    assert unchanged.error is not None
    assert "No Prompt fields" in unchanged.error


@pytest.mark.asyncio
async def test_delete_proposal_and_confirmation_operate_on_one_detached_prompt():
    manager = MagicMock()
    manager.get_prompt.return_value = make_prompt()
    manager.delete_prompt.return_value = True
    context = make_context(manager)

    proposal = await DeletePromptTool().execute(context, prompt_id="prompt-1")
    applied = await DeletePromptTool().execute_confirmed(context, prompt_id="prompt-1")

    assert proposal.success is True
    assert json.loads(proposal.data)["proposal"] == {
        "prompt_id": "prompt-1",
        "name": "Study",
        "preview": "a fox BREAK",
    }
    assert applied.success is True
    manager.delete_prompt.assert_called_once_with("user-1", "prompt-1")
