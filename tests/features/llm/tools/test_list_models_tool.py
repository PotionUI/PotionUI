"""Tests for ListModelsTool."""

import json
import pytest
from unittest.mock import MagicMock
from typing import Any, List

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.list_models_tool import ListModelsTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_context(
    user_id: str = "user-1",
    model_index_manager: Any = None,
) -> ToolContext:
    return ToolContext(
        user_id=user_id,
        model_index_manager=model_index_manager,
    )


def make_model(
    model_id: str = "m-1",
    filename: str = "model.safetensors",
    model_type: str = "checkpoint",
    tags: List[Any] = None,
    description: str = "",
) -> MagicMock:
    """Build a mock model object with a to_dict() method."""
    if tags is None:
        tags = []
    m = MagicMock()
    m.id = model_id
    m.to_dict.return_value = {
        "id": model_id,
        "filename": filename,
        "model_type": model_type,
        "tags": tags,
        "description": description,
    }
    return m


def make_model_index_manager(models: List[Any] = None) -> MagicMock:
    """Build a mock model_index_manager whose model_repo.get_all returns the given list."""
    manager = MagicMock()
    manager.model_repo.get_all.return_value = models if models is not None else []
    return manager


# ---------------------------------------------------------------------------
# Schema / metadata
# ---------------------------------------------------------------------------

class TestListModelsToolSchema:
    def test_name(self):
        assert ListModelsTool().name == "list_models"

    def test_hint_is_nonempty(self):
        assert len(ListModelsTool().hint) > 0

    def test_description_is_nonempty(self):
        assert len(ListModelsTool().description) > 0

    def test_requires_approval_is_false(self):
        assert ListModelsTool().requires_approval is False

    def test_parameters_has_no_required_fields(self):
        schema = ListModelsTool().parameters
        assert schema.get("required", []) == []

    def test_parameters_has_model_type_filter(self):
        schema = ListModelsTool().parameters
        assert "model_type" in schema["properties"]

    def test_parameters_has_query_filter(self):
        schema = ListModelsTool().parameters
        assert "query" in schema["properties"]

    def test_parameters_has_limit(self):
        schema = ListModelsTool().parameters
        assert "limit" in schema["properties"]

    def test_to_schema_structure(self):
        schema = ListModelsTool().to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "list_models"
        assert "parameters" in schema["function"]


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------

class TestListModelsToolExecute:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_model_index_manager(self):
        ctx = make_context()
        result = await ListModelsTool().execute(ctx)
        assert result.success is False
        assert "not available" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_all_models_when_no_filters(self):
        models = [
            make_model("m-1", "a.safetensors", "checkpoint"),
            make_model("m-2", "b.safetensors", "lora"),
        ]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["count"] == 2
        assert len(payload["models"]) == 2

    @pytest.mark.asyncio
    async def test_calls_get_all_with_model_type_filter(self):
        manager = make_model_index_manager()
        ctx = make_context(model_index_manager=manager)
        await ListModelsTool().execute(ctx, model_type="lora")
        manager.model_repo.get_all.assert_called_once()
        call_kwargs = manager.model_repo.get_all.call_args[1]
        assert call_kwargs["model_type"] == "lora"

    @pytest.mark.asyncio
    async def test_calls_get_all_with_query_filter(self):
        manager = make_model_index_manager()
        ctx = make_context(model_index_manager=manager)
        await ListModelsTool().execute(ctx, query="dreamshaper")
        manager.model_repo.get_all.assert_called_once()
        call_kwargs = manager.model_repo.get_all.call_args[1]
        assert call_kwargs["search"] == "dreamshaper"

    @pytest.mark.asyncio
    async def test_calls_get_all_with_default_limit_20(self):
        manager = make_model_index_manager()
        ctx = make_context(model_index_manager=manager)
        await ListModelsTool().execute(ctx)
        call_kwargs = manager.model_repo.get_all.call_args[1]
        assert call_kwargs["limit"] == 20

    @pytest.mark.asyncio
    async def test_calls_get_all_with_custom_limit(self):
        manager = make_model_index_manager()
        ctx = make_context(model_index_manager=manager)
        await ListModelsTool().execute(ctx, limit=5)
        call_kwargs = manager.model_repo.get_all.call_args[1]
        assert call_kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_get_all_called_with_include_tags_true(self):
        manager = make_model_index_manager()
        ctx = make_context(model_index_manager=manager)
        await ListModelsTool().execute(ctx)
        call_kwargs = manager.model_repo.get_all.call_args[1]
        assert call_kwargs.get("include_tags") is True
        assert call_kwargs.get("include_providers") is False

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty_list(self):
        manager = make_model_index_manager(models=[])
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["models"] == []
        assert payload["count"] == 0

    @pytest.mark.asyncio
    async def test_result_includes_id_filename_and_type(self):
        models = [make_model("m-99", "flux.safetensors", "checkpoint")]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert entry["id"] == "m-99"
        assert entry["filename"] == "flux.safetensors"
        assert entry["type"] == "checkpoint"

    @pytest.mark.asyncio
    async def test_tags_as_dicts_are_normalized_to_names(self):
        models = [make_model(tags=[{"name": "anime"}, {"name": "portrait"}])]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert entry["tags"] == ["anime", "portrait"]

    @pytest.mark.asyncio
    async def test_tags_as_plain_strings_are_handled(self):
        models = [make_model(tags=["anime", "realistic"])]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert entry["tags"] == ["anime", "realistic"]

    @pytest.mark.asyncio
    async def test_tags_key_omitted_when_no_tags(self):
        models = [make_model(tags=[])]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert "tags" not in entry

    @pytest.mark.asyncio
    async def test_description_included_when_present(self):
        models = [make_model(description="A great model for portraits.")]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert "description" in entry
        assert "great model" in entry["description"]

    @pytest.mark.asyncio
    async def test_description_omitted_when_empty(self):
        models = [make_model(description="")]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert "description" not in entry

    @pytest.mark.asyncio
    async def test_long_description_truncated_at_100_chars(self):
        long_desc = "b" * 200
        models = [make_model(description=long_desc)]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert entry["description"].endswith("...")
        assert len(entry["description"]) == 103  # 100 + "..."

    @pytest.mark.asyncio
    async def test_short_description_not_truncated(self):
        models = [make_model(description="short desc")]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert entry["description"] == "short desc"

    @pytest.mark.asyncio
    async def test_get_all_exception_returns_failure(self):
        manager = MagicMock()
        manager.model_repo.get_all.side_effect = RuntimeError("db connection lost")
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        assert result.success is False
        assert "db connection lost" in result.error or "failed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_all_filters_combined(self):
        manager = make_model_index_manager()
        ctx = make_context(model_index_manager=manager)
        await ListModelsTool().execute(ctx, model_type="checkpoint", query="flux", limit=10)
        call_kwargs = manager.model_repo.get_all.call_args[1]
        assert call_kwargs["model_type"] == "checkpoint"
        assert call_kwargs["search"] == "flux"
        assert call_kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_multiple_models_all_present_in_results(self):
        models = [
            make_model("m-1", "a.safetensors", "checkpoint"),
            make_model("m-2", "b.safetensors", "lora"),
            make_model("m-3", "c.safetensors", "vae"),
        ]
        manager = make_model_index_manager(models=models)
        ctx = make_context(model_index_manager=manager)
        result = await ListModelsTool().execute(ctx)
        payload = json.loads(result.data)
        ids = [e["id"] for e in payload["models"]]
        assert "m-1" in ids
        assert "m-2" in ids
        assert "m-3" in ids
        assert payload["count"] == 3
