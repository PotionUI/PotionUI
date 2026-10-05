"""Tests for approval-gated detached rich Prompt tools.

The tools call `src.features.prompt_database.operations` functions directly
(module-level, no injected manager) against `context.prompt_database` (a
`PromptDatabaseCollaborators` stand-in - a plain MagicMock here). `mock_operations`
patches the `operations` module as imported into `manage_prompts_tool.py`, so
tests assert against it exactly like the previous manager mock. Reads
(`_existing`) go straight to `context.prompt_database.repository.get_by_id`.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.features.segments.dto import RichSegment
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin import manage_prompts_tool as manage_prompts_tool_module
from src.features.llm.tools.builtin.manage_prompts_tool import (
    AddPromptTool,
    DeletePromptTool,
    EditPromptTool,
    GetPromptTool,
    ListPromptsTool,
)
from src.features.prompt_database.dto import PromptRequest
from src.features.prompt_database.repository import PromptRepository
from tests.conftest import TestDatabase
from src.features.prompt_database.records import Prompt


@pytest.fixture
def mock_operations(monkeypatch):
    """Patch the `operations` module as seen by manage_prompts_tool.py."""
    mock = Mock()
    monkeypatch.setattr(manage_prompts_tool_module, "operations", mock)
    return mock


def make_context(prompt_database=None, user_id: str = "user-1") -> ToolContext:
    return ToolContext(user_id=user_id, prompt_database=prompt_database)


def make_prompt(prompt_id: str = "prompt-1") -> Prompt:
    return Prompt(
        id=prompt_id,
        user_id="user-1",
        name="Study",
        usage_hint="positive",
        segments=[
            RichSegment(content="a fox", name="Subject", color="#d97706"),
            RichSegment(content="a hound"),
        ],
        flattened_text="a fox a hound",
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
async def test_add_proposal_round_trips_rich_ordered_segments_without_mutating(mock_operations):
    prompt_database = MagicMock()
    result = await AddPromptTool().execute(
        make_context(prompt_database),
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
            {"type": "content", "content": "sharp focus"},
        ],
        tags=["quality"],
    )

    assert result.success is True
    proposal = json.loads(result.data)["proposal"]
    assert proposal["usage_hint"] == "negative"
    assert [segment["type"] for segment in proposal["segments"]] == ["content", "content"]
    assert proposal["segments"][0]["enabled"] is False
    assert proposal["segments"][0]["name"] == "Quality"
    assert proposal["segments"][0]["color"] == "#ef4444"
    assert "negative_prompt" not in proposal
    mock_operations.create_prompt.assert_not_called()


@pytest.mark.asyncio
async def test_add_preview_carries_text_edit_shape():
    result = await AddPromptTool().execute(
        make_context(MagicMock()),
        name="Fox study",
        usage_hint="negative",
        segments=[{"content": "blurry"}],
        tags=["quality"],
    )

    assert result.preview.kind == "text_edit"
    assert result.preview.target == "Fox study"
    assert result.preview.text_blocks == [{"label": "Prompt", "text": "blurry"}]
    fields = {f["label"]: f["value"] for f in result.preview.fields}
    assert fields["Usage"] == "negative"
    assert fields["Tags"] == "quality"


@pytest.mark.asyncio
async def test_add_rejects_an_empty_aggregate():
    result = await AddPromptTool().execute(make_context(MagicMock()), segments=[])

    assert result.success is False
    assert result.error is not None
    assert "segment" in result.error.lower()


@pytest.mark.asyncio
async def test_add_confirmed_calls_aggregate_manager(mock_operations):
    prompt_database = MagicMock()
    mock_operations.create_prompt = AsyncMock(return_value=make_prompt("saved-1"))

    result = await AddPromptTool().execute_confirmed(
        make_context(prompt_database, "owner-1"),
        name="Saved composition",
        segments=[{"content": "first"}, {"content": "second"}],
    )

    assert result.success is True
    assert json.loads(result.data)["prompt_id"] == "saved-1"
    mock_operations.create_prompt.assert_awaited_once()
    collab_arg, user_id, request = mock_operations.create_prompt.await_args.args
    assert collab_arg is prompt_database
    assert user_id == "owner-1"
    assert [segment.content for segment in request.segments] == ["first", "second"]
    assert not hasattr(request, "negative_prompt")


@pytest.mark.asyncio
async def test_add_confirmed_with_variables_creates_the_prompt(mock_operations):
    prompt_database = MagicMock()
    mock_operations.create_prompt = AsyncMock(return_value=make_prompt("saved-1"))
    variables = {"mood": {"type": "text", "value": "noir"}}

    result = await AddPromptTool().execute_confirmed(
        make_context(prompt_database, "owner-1"),
        segments=[{"content": "a fox"}],
        variables=variables,
    )

    assert result.success is True
    mock_operations.create_prompt.assert_awaited_once()
    _, _, request = mock_operations.create_prompt.await_args.args
    assert request.variables == variables


@pytest.mark.asyncio
async def test_add_rejects_a_variables_cycle():
    result = await AddPromptTool().execute(
        make_context(MagicMock()),
        segments=[{"content": "a fox"}],
        variables={
            "a": {
                "type": "choice", "mode": "shuffle", "pinnedIndex": None,
                "options": [{"text": "x", "when": {"var": "b", "values": ["y"]}}],
            },
            "b": {
                "type": "choice", "mode": "shuffle", "pinnedIndex": None,
                "options": [{"text": "y", "when": {"var": "a", "values": ["x"]}}],
            },
        },
    )

    assert result.success is False
    assert "cycle" in result.error


@pytest.mark.asyncio
async def test_edit_proposal_shows_old_and_new_ordered_aggregates():
    prompt_database = MagicMock()
    prompt_database.repository.get_by_id.return_value = make_prompt()

    result = await EditPromptTool().execute(
        make_context(prompt_database),
        prompt_id="prompt-1",
        name="Reworked",
        usage_hint="negative",
        segments=[{"content": "low quality", "name": "Avoid"}],
    )

    assert result.success is True
    proposal = json.loads(result.data)["proposal"]
    assert [item["type"] for item in proposal["old"]["segments"]] == [
        "content",
        "content",
    ]
    assert proposal["new"]["segments"][0]["content"] == "low quality"
    assert proposal["new"]["usage_hint"] == "negative"
    assert "negative_prompt" not in json.dumps(proposal)


@pytest.mark.asyncio
async def test_edit_preview_carries_old_new_text_and_flags_changed_name():
    prompt_database = MagicMock()
    prompt_database.repository.get_by_id.return_value = make_prompt()

    result = await EditPromptTool().execute(
        make_context(prompt_database),
        prompt_id="prompt-1",
        name="Reworked",
        segments=[{"content": "low quality"}],
    )

    assert result.preview.kind == "text_edit"
    assert result.preview.target == "Study"  # existing.display_name
    text_block = result.preview.text_blocks[0]
    assert text_block["old_text"] == "a fox a hound"
    assert text_block["text"] == "low quality"
    name_field = next(f for f in result.preview.fields if f["label"] == "Name")
    assert name_field == {"label": "Name", "value": "Reworked", "old": "Study"}


@pytest.mark.asyncio
async def test_edit_preview_omits_old_text_when_prompt_text_unchanged():
    prompt_database = MagicMock()
    existing = make_prompt()
    prompt_database.repository.get_by_id.return_value = existing

    result = await EditPromptTool().execute(
        make_context(prompt_database),
        prompt_id="prompt-1",
        name="Renamed only",
    )

    text_block = result.preview.text_blocks[0]
    assert "old_text" not in text_block


@pytest.mark.asyncio
async def test_edit_confirmed_delegates_atomic_replacement(mock_operations):
    existing = make_prompt()
    prompt_database = MagicMock()
    prompt_database.repository.get_by_id.return_value = existing
    mock_operations.replace_prompt = AsyncMock(return_value=existing)

    result = await EditPromptTool().execute_confirmed(
        make_context(prompt_database),
        prompt_id="prompt-1",
        segments=[{"content": "replacement"}],
    )

    assert result.success is True
    mock_operations.replace_prompt.assert_awaited_once()
    collab_arg, user_id, prompt_id, request = mock_operations.replace_prompt.await_args.args
    assert collab_arg is prompt_database
    assert (user_id, prompt_id) == ("user-1", "prompt-1")
    assert [segment.content for segment in request.segments] == ["replacement"]


@pytest.mark.asyncio
async def test_edit_requires_an_existing_prompt_and_at_least_one_change():
    prompt_database = MagicMock()
    prompt_database.repository.get_by_id.return_value = None
    missing = await EditPromptTool().execute(
        make_context(prompt_database), prompt_id="missing", segments=[{"content": "x"}]
    )
    assert missing.success is False
    assert missing.error is not None
    assert "not found" in missing.error

    prompt_database.repository.get_by_id.return_value = make_prompt()
    unchanged = await EditPromptTool().execute(make_context(prompt_database), prompt_id="prompt-1")
    assert unchanged.success is False
    assert unchanged.error is not None
    assert "No Prompt fields" in unchanged.error


@pytest.mark.asyncio
async def test_edit_accepts_a_variables_only_change(mock_operations):
    prompt_database = MagicMock()
    prompt_database.repository.get_by_id.return_value = make_prompt()
    variables = {"mood": {"type": "text", "value": "noir"}}

    result = await EditPromptTool().execute(
        make_context(prompt_database), prompt_id="prompt-1", variables=variables,
    )

    assert result.success is True
    proposal = json.loads(result.data)["proposal"]
    assert proposal["new"]["variables"] == variables
    fields = {f["label"]: f["value"] for f in result.preview.fields}
    assert fields["Variables"] == "1 variables"


@pytest.mark.asyncio
async def test_delete_proposal_and_confirmation_operate_on_one_detached_prompt(mock_operations):
    prompt_database = MagicMock()
    prompt_database.repository.get_by_id.return_value = make_prompt()
    mock_operations.delete_prompt.return_value = True
    context = make_context(prompt_database)

    proposal = await DeletePromptTool().execute(context, prompt_id="prompt-1")
    applied = await DeletePromptTool().execute_confirmed(context, prompt_id="prompt-1")

    assert proposal.success is True
    assert json.loads(proposal.data)["proposal"] == {
        "prompt_id": "prompt-1",
        "name": "Study",
        "preview": "a fox a hound",
    }
    assert applied.success is True
    mock_operations.delete_prompt.assert_called_once_with(prompt_database, "user-1", "prompt-1")


@pytest.mark.asyncio
async def test_delete_preview_is_legacy_action_target_and_summary(mock_operations):
    prompt_database = MagicMock()
    prompt_database.repository.get_by_id.return_value = make_prompt()

    result = await DeletePromptTool().execute(make_context(prompt_database), prompt_id="prompt-1")

    assert result.preview.action == "Delete prompt"
    assert result.preview.target == "Study"
    assert result.preview.summary == "a fox a hound"
    assert result.preview.kind is None
    assert result.preview.fields is None


@pytest.fixture
def library():
    test_database = TestDatabase.from_template()
    with test_database.get_cursor() as cursor:
        for user_id in ("user-1", "user-2"):
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES (?, ?, ?, ?)",
                (user_id, user_id, f"{user_id}@example.com", "x"),
            )
    with patch("src.platform.database.database.db", test_database):
        yield SimpleNamespace(repository=PromptRepository())
    test_database.close()


def save(library, user_id, name, text, **extra):
    return library.repository.create(Prompt(
        user_id=user_id, name=name, segments=[RichSegment(content=text)], **extra,
    ))


@pytest.mark.asyncio
async def test_get_prompt_output_round_trips_into_edit_arguments(library):
    variables = {
        "mood": {
            "type": "choice", "mode": "pin", "pinnedIndex": 1,
            "options": ["calm", {"text": "stormy", "when": {"var": "sky", "values": ["dark"]}}],
        },
        "sky": {"type": "choice", "mode": "shuffle", "options": ["dark", "clear"]},
    }
    saved = library.repository.create(Prompt(
        user_id="user-1", name="Study", usage_hint="negative", variables=variables,
        segments=[RichSegment(content="a fox", name="Subject", color="#d97706", enabled=False)],
    ))
    context = make_context(SimpleNamespace(repository=library.repository))

    result = await GetPromptTool().execute(context, prompt_id=saved.id)

    assert result.success is True
    payload = json.loads(result.data)
    assert payload["prompt_id"] == saved.id
    assert payload["usage_hint"] == "negative"
    assert payload["variables"] == variables
    assert payload["segments"][0]["enabled"] is False
    edit_arguments = {key: payload[key] for key in ("prompt_id", "name", "usage_hint", "segments", "variables")}
    PromptRequest(**{key: value for key, value in edit_arguments.items() if key != "prompt_id"})
    edit = await EditPromptTool().execute(context, **edit_arguments)
    assert edit.success is True
    assert json.loads(edit.data)["proposal"]["new"]["variables"] == variables


def test_read_tools_need_no_approval():
    assert GetPromptTool().requires_approval is False
    assert ListPromptsTool().requires_approval is False


@pytest.mark.asyncio
async def test_get_prompt_hides_another_users_prompt(library):
    saved = save(library, "user-2", "Theirs", "secret")
    context = make_context(SimpleNamespace(repository=library.repository), user_id="user-1")

    result = await GetPromptTool().execute(context, prompt_id=saved.id)

    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_get_prompt_unknown_id_is_not_found(library):
    context = make_context(SimpleNamespace(repository=library.repository))

    result = await GetPromptTool().execute(context, prompt_id="missing")

    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_list_prompts_returns_only_own_prompts_with_preview(library):
    mine = save(library, "user-1", "Mine", "a fox " * 40)
    save(library, "user-2", "Theirs", "a hound")
    context = make_context(SimpleNamespace(repository=library.repository))

    result = await ListPromptsTool().execute(context)

    items = json.loads(result.data)["prompts"]
    assert [item["id"] for item in items] == [mine.id]
    assert items[0]["name"] == "Mine"
    assert len(items[0]["preview"]) <= 123
    assert items[0]["updated_at"]


@pytest.mark.asyncio
async def test_list_prompts_searches_and_pages(library):
    for index in range(5):
        save(library, "user-1", f"Fox {index}", "a fox")
    save(library, "user-1", "Hound", "a hound")
    context = make_context(SimpleNamespace(repository=library.repository))

    found = json.loads((await ListPromptsTool().execute(context, search="fox")).data)["prompts"]
    first = json.loads((await ListPromptsTool().execute(context, search="fox", limit=2)).data)["prompts"]
    second = json.loads((await ListPromptsTool().execute(context, search="fox", limit=2, offset=2)).data)["prompts"]

    assert len(found) == 5
    assert len(first) == 2 and len(second) == 2
    assert not {item["id"] for item in first} & {item["id"] for item in second}
