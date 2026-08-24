"""Tests for GetModelInfoTool."""

import json
from unittest.mock import MagicMock

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.model_info_tool import GetModelInfoTool


def make_context(model_index_manager=None, user_id="user-1"):
    return ToolContext(user_id=user_id, model_index_manager=model_index_manager)


class TestSchema:
    def test_identity(self):
        tool = GetModelInfoTool()
        assert tool.name == "get_model_info"
        assert tool.parameters["required"] == ["model_id"]

    def test_modes_include_generation_and_models(self):
        assert GetModelInfoTool().modes == ["generation", "models"]

    def test_model_metadata_is_a_valid_field_choice(self):
        fields_schema = GetModelInfoTool().parameters["properties"]["fields"]["items"]
        assert "model_metadata" in fields_schema["enum"]


class TestModelMetadataField:
    """model_metadata (a model's per-type custom attributes, e.g. a LoRA's
    default strength) is opt-in, like description/tags/provider."""

    def _manager(self, model_metadata):
        manager = MagicMock()
        manager.get_model_by_id.return_value = {
            "model": {
                "id": "model-1", "filename": "foo.safetensors", "type": "lora",
                "model_metadata": model_metadata,
            },
        }
        return manager

    @pytest.mark.asyncio
    async def test_model_metadata_omitted_by_default(self):
        manager = self._manager({"default_strength": 0.8})
        result = await GetModelInfoTool().execute(make_context(manager), model_id="model-1")
        payload = json.loads(result.data)
        assert "model_metadata" not in payload

    @pytest.mark.asyncio
    async def test_model_metadata_returned_when_requested(self):
        manager = self._manager({"default_strength": 0.8})
        result = await GetModelInfoTool().execute(
            make_context(manager), model_id="model-1", fields=["model_metadata"],
        )
        payload = json.loads(result.data)
        assert payload["model_metadata"] == {"default_strength": 0.8}

    @pytest.mark.asyncio
    async def test_empty_model_metadata_stays_omitted_even_when_requested(self):
        manager = self._manager({})
        result = await GetModelInfoTool().execute(
            make_context(manager), model_id="model-1", fields=["model_metadata"],
        )
        payload = json.loads(result.data)
        assert "model_metadata" not in payload


class TestExecuteValidation:
    @pytest.mark.asyncio
    async def test_no_manager_errors(self):
        result = await GetModelInfoTool().execute(make_context(None), model_id="model-1")
        assert result.success is False
        assert "not available" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_model_id_errors(self):
        result = await GetModelInfoTool().execute(make_context(MagicMock()))
        assert result.success is False
        assert "model_id" in result.error.lower()
