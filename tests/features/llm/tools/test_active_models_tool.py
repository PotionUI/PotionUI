"""Tests for GetActiveModelsTool, particularly prompting_guidance surfacing."""

import json
from typing import Any, Dict, List, Optional

import pytest

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.active_models_tool import GetActiveModelsTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeModelRepo:
    def __init__(self, models_by_id: Dict[str, Any]):
        self._models_by_id = models_by_id

    def get_by_id(self, model_id: str, include_providers: bool = False, include_tags: bool = False):
        model = self._models_by_id.get(model_id)
        if model is None:
            return None

        class _Model:
            def to_dict(self, **kwargs):
                return dict(model)

        return _Model()

    def get_by_file_path(self, path: str):
        return None


class FakeModelIndexManager:
    def __init__(self, models_by_id: Dict[str, Any]):
        self.model_repo = FakeModelRepo(models_by_id)


def make_context(
    form_data: Optional[Dict[str, Any]] = None,
    models_by_id: Optional[Dict[str, Any]] = None,
) -> ToolContext:
    form_state = {"form_data": form_data or {}}
    return ToolContext(
        user_id="user-1",
        model_index_manager=FakeModelIndexManager(models_by_id or {}),
        session_metadata={"form_state": form_state},
        preset_manager=None,
    )


def model_row(
    model_id: str,
    filename: str,
    model_type: str,
    prompting_guidance: Optional[str] = None,
    triggers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": model_id,
        "filename": filename,
        "model_type": model_type,
    }
    if prompting_guidance is not None:
        row["prompting_guidance"] = prompting_guidance
    if triggers is not None:
        row["triggers"] = triggers
    return row


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------

class TestGetActiveModelsToolPromptingGuidance:
    @pytest.mark.asyncio
    async def test_includes_prompting_guidance_for_active_checkpoint(self):
        models_by_id = {
            "m-1": model_row(
                "m-1",
                "dreamshaper.safetensors",
                "checkpoint",
                prompting_guidance="Prefer short, comma-separated tags.",
            ),
        }
        ctx = make_context(
            form_data={"checkpoint": "model:m-1"},
            models_by_id=models_by_id,
        )
        result = await GetActiveModelsTool().execute(ctx)
        assert result.success is True
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert entry["prompting_guidance"] == "Prefer short, comma-separated tags."

    @pytest.mark.asyncio
    async def test_includes_prompting_guidance_for_selected_loras(self):
        models_by_id = {
            "lora-1": model_row(
                "lora-1",
                "anime-style.safetensors",
                "lora",
                prompting_guidance="Use the trigger word 'animestyle' near the front.",
                triggers=["animestyle"],
            ),
        }
        ctx = make_context(
            form_data={"loras": [{"model": "model:lora-1", "strength": 0.8}]},
            models_by_id=models_by_id,
        )
        result = await GetActiveModelsTool().execute(ctx)
        assert result.success is True
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert entry["prompting_guidance"] == "Use the trigger word 'animestyle' near the front."
        assert entry["trigger_words"] == ["animestyle"]

    @pytest.mark.asyncio
    async def test_omits_prompting_guidance_when_absent(self):
        models_by_id = {
            "m-2": model_row("m-2", "vanilla.safetensors", "checkpoint"),
        }
        ctx = make_context(
            form_data={"checkpoint": "model:m-2"},
            models_by_id=models_by_id,
        )
        result = await GetActiveModelsTool().execute(ctx)
        payload = json.loads(result.data)
        entry = payload["models"][0]
        assert "prompting_guidance" not in entry

    @pytest.mark.asyncio
    async def test_no_model_index_manager_returns_error(self):
        ctx = ToolContext(user_id="user-1", model_index_manager=None)
        result = await GetActiveModelsTool().execute(ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_hint_mentions_prompting_guidance_consultation(self):
        hint = GetActiveModelsTool().hint
        assert "prompting_guidance" in hint or "prompt" in hint.lower()
