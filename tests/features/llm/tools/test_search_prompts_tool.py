"""Tests for aggregate Prompt search exposed to built-in LLM tools."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.segments.dto import RichSegment
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.search_prompts_tool import SearchModelPromptsTool
from src.features.prompt_database.records import Prompt


def make_context(manager=None, **kwargs) -> ToolContext:
    return ToolContext(
        user_id=kwargs.pop("user_id", "user-1"),
        prompt_database_manager=manager,
        session_metadata=kwargs.pop("session_metadata", {}),
        model_index_manager=kwargs.pop("model_index_manager", None),
    )


def make_prompt(
    prompt_id: str = "prompt-1",
    text: str = "a detailed fox",
    *,
    usage_hint: str = "positive",
    source_url: str = "https://example.test/prompt-1",
) -> Prompt:
    return Prompt(
        id=prompt_id,
        user_id="user-1",
        name="Fox study",
        usage_hint=usage_hint,
        segments=[
            RichSegment(content=text, name="Subject", color="#f59e0b"),
            RichSegment(type="break", enabled=True),
        ],
        flattened_text=f"{text} BREAK",
        model_name="Example XL",
        base_model="SDXL",
        cfg_scale=7.0,
        steps=30,
        sampler="Euler",
        heart_count=8,
        like_count=3,
        source_provider="community",
        source_url=source_url,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


def test_schema_requests_atomic_queries_and_optional_model_filter():
    schema = SearchModelPromptsTool().parameters

    assert schema["required"] == ["queries"]
    assert schema["properties"]["queries"]["items"] == {"type": "string"}
    assert "model_id" in schema["properties"]
    assert "base_model" not in schema["properties"]


@pytest.mark.asyncio
async def test_missing_manager_or_queries_returns_clear_failure():
    unavailable = await SearchModelPromptsTool().execute(
        make_context(), queries=["fox"]
    )
    assert unavailable.success is False
    assert unavailable.error is not None
    assert "not available" in unavailable.error

    missing = await SearchModelPromptsTool().execute(make_context(MagicMock()), queries=[])
    assert missing.success is False
    assert missing.error is not None
    assert "queries" in missing.error


@pytest.mark.asyncio
async def test_search_returns_rich_aggregate_fields_without_negative_pair():
    manager = MagicMock()
    manager.search = AsyncMock(return_value=[make_prompt()])

    result = await SearchModelPromptsTool().execute(
        make_context(manager), queries=["fox"], model_id="model-1", limit=2
    )

    assert result.success is True
    entry = json.loads(result.data)["results"][0]["prompts"][0]
    assert entry["prompt_id"] == "prompt-1"
    assert entry["name"] == "Fox study"
    assert entry["flattened_text"] == "a detailed fox BREAK"
    assert entry["usage_hint"] == "positive"
    assert [segment["type"] for segment in entry["segments"]] == ["content", "break"]
    assert entry["segments"][0]["name"] == "Subject"
    assert entry["segments"][0]["color"] == "#f59e0b"
    assert "negative_prompt" not in entry
    manager.search.assert_awaited_once_with(
        user_id="user-1", query="fox", limit=2, model_id="model-1"
    )
    assert result.sources is not None
    assert result.sources[0].title == "Fox study"


@pytest.mark.asyncio
async def test_negative_usage_hint_is_an_independent_search_record():
    manager = MagicMock()
    manager.search = AsyncMock(
        return_value=[
            make_prompt(
                "prompt-negative",
                "blurry, low quality",
                usage_hint="negative",
                source_url="https://example.test/negative",
            )
        ]
    )

    result = await SearchModelPromptsTool().execute(
        make_context(manager), queries=["quality exclusions"]
    )

    entry = json.loads(result.data)["results"][0]["prompts"][0]
    assert entry["prompt_id"] == "prompt-negative"
    assert entry["usage_hint"] == "negative"
    assert entry["flattened_text"] == "blurry, low quality BREAK"
    assert "negative_prompt" not in entry


@pytest.mark.asyncio
async def test_results_are_deduplicated_across_query_groups():
    manager = MagicMock()
    shared = make_prompt()
    manager.search = AsyncMock(side_effect=[[shared], [shared]])

    result = await SearchModelPromptsTool().execute(
        make_context(manager), queries=["fox", "wildlife"]
    )

    groups = json.loads(result.data)["results"]
    assert len(groups[0]["prompts"]) == 1
    assert groups[1]["prompts"] == []


@pytest.mark.asyncio
async def test_legacy_single_query_is_normalized_and_query_count_is_bounded():
    manager = MagicMock()
    manager.search = AsyncMock(return_value=[])
    tool = SearchModelPromptsTool()

    single = await tool.execute(make_context(manager), query="portrait")
    assert single.success is True
    manager.search.assert_awaited_once_with(
        user_id="user-1", query="portrait", limit=3
    )

    manager.search.reset_mock()
    many = await tool.execute(
        make_context(manager), queries=[f"concept-{index}" for index in range(8)]
    )
    assert manager.search.await_count == tool.MAX_QUERIES
    assert "truncated" in json.loads(many.data)
