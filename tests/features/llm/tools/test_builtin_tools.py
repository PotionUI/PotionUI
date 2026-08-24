"""Tests for built-in LLM tools."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin import (
    ListSegmentCategoriesTool,
    GetSavedSegmentsTool,
    GetSegmentTemplatesTool,
    GetModelInfoTool,
    GetPresetInfoTool,
    ListPhrasebookCategoriesTool,
    GetPhrasebookValuesTool,
    ListPhrasebookValuesTool,
    CreatePhrasebookCategoryTool,
    CreatePhrasebookValuesTool,
    RemovePhrasebookValuesTool,
    UpdatePhrasebookValuesTool,
    EnhancePromptTool,
    GetCurrentSegmentsTool,
    UpdateSegmentTool,
    GetFormStateTool,
    GetActiveModelsTool,
    register_builtin_tools,
)
from src.features.llm.tools.registry import ToolRegistry
from src.features.segments.dto import RichSegment, SavedSegment, SegmentTemplate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_context(**kwargs) -> ToolContext:
    return ToolContext(user_id="user-test", **kwargs)


def make_category(id_="cat-1", name="Style", **extra):
    cat = MagicMock()
    cat.id = id_
    cat.name = name
    for k, v in extra.items():
        setattr(cat, k, v)
    return cat


def make_template(id_="tmpl-1", name="Cinematic", content="cinematic shot", **extra):
    return SegmentTemplate(
        id=id_,
        user_id="user-test",
        name=name,
        description=extra.get("description", ""),
        tags=extra.get("tags", []),
        segments=extra.get("segments", [RichSegment(content=content)]),
    )


def make_saved_segment(id_="segment-1", name="Cinematic", content="cinematic shot", **extra):
    return SavedSegment(
        id=id_,
        user_id="user-test",
        name=name,
        category_id=extra.get("category_id", "category-1"),
        content=content,
        effective_color=extra.get("effective_color", "#123456"),
        tags=extra.get("tags", []),
    )


def make_phrasebook_value(id_="val-1", label="Wide angle", value="wide angle lens"):
    val = MagicMock()
    val.id = id_
    val.label = label
    val.value = value
    val.sort_order = 0
    return val


# ---------------------------------------------------------------------------
# register_builtin_tools
# ---------------------------------------------------------------------------

class TestRegisterBuiltinTools:
    def test_registers_all_tools(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)
        names = {t.name for t in registry.get_all()}
        assert names == {
            "list_segment_categories",
            "get_saved_segments",
            "get_segment_templates",
            "get_model_info",
            "get_preset_info",
            "list_phrasebook_categories",
            "get_phrasebook_values",
            "list_phrasebook_values",
            "create_phrasebook_category",
            "create_phrasebook_values",
            "remove_phrasebook_values",
            "update_phrasebook_values",
            "enhance_prompt",
            "get_current_segments",
            "update_segment",
            "get_form_state",
            "get_active_models",
            "update_form_settings",
            "search_model_prompts",
            "search_gallery",
            "add_prompt",
            "edit_prompt",
            "delete_prompt",
            "run_generation",
            "list_models",
            "write_memory",
            "read_memory",
            "update_memory",
            "delete_memory",
            "set_prompt_relay_timeline",
            "get_video_director",
            "update_video_director",
            "get_music_director",
            "update_music_director",
            "manage_collections",
            "organize_gallery",
            "start_generation",
        }

    def test_all_builtin_tools_have_hints(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)
        for tool in registry.get_all():
            assert tool.hint, f"Tool '{tool.name}' is missing a hint"

    TOOL_GROUPS = {
        "Models & presets",
        "Prompt writing",
        "Saved prompts",
        "Form & segments",
        "Generation",
        "Phrasebook vocabulary",
        "Memory",
        "Collections",
    }

    def test_all_builtin_tools_have_group_and_user_description(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)
        for tool in registry.get_all():
            assert tool.group in self.TOOL_GROUPS, (
                f"Tool '{tool.name}' has group '{tool.group}' outside the taxonomy"
            )
            assert tool.user_description, f"Tool '{tool.name}' is missing a user_description"
            assert tool.user_description != tool.description

    def test_group_and_user_description_stay_out_of_llm_schema(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)
        for tool in registry.get_all():
            schema = tool.to_schema()
            assert set(schema["function"].keys()) == {"name", "description", "parameters"}
            assert tool.user_description not in json.dumps(schema)

    def _generation_prompt(self, registry):
        from src.features.chat.modes import ChatModeRegistry, build_generation_mode
        mode_registry = ChatModeRegistry()
        mode = build_generation_mode()
        mode_registry.register(mode)
        names = [t.name for t in registry.get_for_mode(mode)]
        return mode_registry.resolve_system_prompt(mode, registry.get_tool_hints_text(names))

    def test_generation_mode_prompt_includes_all_builtin_hints(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)
        prompt = self._generation_prompt(registry)
        assert "PotionUI" in prompt
        for tool in registry.get_all():
            assert tool.name in prompt

    def test_generation_mode_prompt_includes_marker_syntax(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)
        prompt = self._generation_prompt(registry)
        assert "Phrasebook markers" in prompt
        assert "#category.path" in prompt
        assert "#category.path.ValueLabel" in prompt
        assert "#[path with spaces]" in prompt
        assert "list_phrasebook_categories" in prompt
        assert "get_phrasebook_values" in prompt
        assert "create_phrasebook_category" in prompt
        assert "create_phrasebook_values" in prompt


# ---------------------------------------------------------------------------
# ListSegmentCategoriesTool
# ---------------------------------------------------------------------------

class TestListSegmentCategoriesTool:
    def _tool(self):
        return ListSegmentCategoriesTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "list_segment_categories"
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["required"] == []

    @pytest.mark.asyncio
    async def test_returns_categories(self):
        cats = [
            make_category("c1", "Style", description="Visual style", color="#ff0000"),
            make_category("c2", "Environment", description="", color=""),
        ]
        sm = MagicMock()
        sm.get_categories.return_value = cats
        ctx = make_context(segment_manager=sm)

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 2
        assert data["categories"][0]["id"] == "c1"
        assert data["categories"][0]["name"] == "Style"
        assert data["categories"][0]["color"] == "#ff0000"
        sm.get_categories.assert_called_once_with(user_id="user-test")

    @pytest.mark.asyncio
    async def test_no_segment_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_empty_categories(self):
        sm = MagicMock()
        sm.get_categories.return_value = []
        ctx = make_context(segment_manager=sm)
        result = await self._tool().execute(ctx)
        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 0
        assert data["categories"] == []

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        sm = MagicMock()
        sm.get_categories.side_effect = RuntimeError("db error")
        ctx = make_context(segment_manager=sm)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "db error" in result.error


# ---------------------------------------------------------------------------
# GetSavedSegmentsTool / GetSegmentTemplatesTool
# ---------------------------------------------------------------------------

class TestGetSavedSegmentsTool:
    def _tool(self):
        return GetSavedSegmentsTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "get_saved_segments"
        schema = tool.to_schema()
        assert "category_id" in schema["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_returns_category_filtered_saved_segments(self):
        segments = [
            make_saved_segment(
                "s1", "Cinematic", "cinematic shot", tags=["film"], category_id="c1"
            )
        ]
        sm = MagicMock()
        sm.get_segments.return_value = segments
        result = await self._tool().execute(
            make_context(segment_manager=sm), category_id="c1"
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["segments"][0]["content"] == "cinematic shot"
        assert data["segments"][0]["category_id"] == "c1"
        sm.get_segments.assert_called_once_with(
            user_id="user-test", category_id="c1"
        )

    @pytest.mark.asyncio
    async def test_no_segment_manager(self):
        result = await self._tool().execute(make_context())
        assert result.success is False


class TestGetSegmentTemplatesTool:
    def _tool(self):
        return GetSegmentTemplatesTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "get_segment_templates"
        schema = tool.to_schema()
        assert schema["function"]["parameters"]["properties"] == {}
        assert schema["function"]["parameters"]["required"] == []

    @pytest.mark.asyncio
    async def test_returns_ordered_multi_segment_templates(self):
        tmpls = [
            make_template(
                "t1",
                "Cinematic",
                description="desc",
                tags=["film"],
                segments=[
                    RichSegment(content="cinematic shot"),
                    RichSegment(type="break"),
                ],
            ),
        ]
        sm = MagicMock()
        sm.get_templates.return_value = tmpls
        ctx = make_context(segment_manager=sm)

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["templates"][0]["id"] == "t1"
        assert data["templates"][0]["segments"][0]["content"] == "cinematic shot"
        assert data["templates"][0]["segments"][1]["type"] == "break"
        assert data["templates"][0]["tags"] == ["film"]
        sm.get_templates.assert_called_once_with(user_id="user-test")

    @pytest.mark.asyncio
    async def test_no_segment_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        sm = MagicMock()
        sm.get_templates.side_effect = ValueError("db error")
        ctx = make_context(segment_manager=sm)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "db error" in result.error


# ---------------------------------------------------------------------------
# GetModelInfoTool
# ---------------------------------------------------------------------------

class TestGetModelInfoTool:
    def _tool(self):
        return GetModelInfoTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "get_model_info"
        schema = tool.to_schema()
        assert "model_id" in schema["function"]["parameters"]["properties"]
        assert "model_id" in schema["function"]["parameters"]["required"]

    @pytest.mark.asyncio
    async def test_returns_model_info(self):
        raw = {
            "model": {
                "id": "m-1",
                "filename": "sdxl.safetensors",
                "type": "checkpoint",
                "description": "SDXL base model",
                "tags": ["sdxl", "base"],
                "provider_info": {
                    "name": "Stability AI",
                    "description": "Official",
                    "tags": ["official"],
                },
            }
        }
        mim = MagicMock()
        mim.get_model_by_id.return_value = raw
        ctx = make_context(model_index_manager=mim)

        # provider is opt-in — request it explicitly via `fields`
        result = await self._tool().execute(ctx, model_id="m-1", fields=["provider"])

        assert result.success is True
        data = json.loads(result.data)
        assert data["id"] == "m-1"
        assert data["filename"] == "sdxl.safetensors"
        assert data["type"] == "checkpoint"
        assert data["provider"]["name"] == "Stability AI"
        mim.get_model_by_id.assert_called_once_with("m-1")

    @pytest.mark.asyncio
    async def test_default_omits_extras_fields_param_opts_in(self):
        """Compact core by default; `fields` opts in extras one at a time, and
        bogus field names are silently ignored (the
        small chat model drowned in unconditional description/tags/provider)."""
        raw = {
            "model": {
                "id": "m-1",
                "filename": "sdxl.safetensors",
                "type": "checkpoint",
                "description": "A very long description.",
                "tags": ["sdxl", "base"],
                "provider_info": {"name": "Stability AI", "description": "Official"},
            }
        }
        mim = MagicMock()
        mim.get_model_by_id.return_value = raw
        ctx = make_context(model_index_manager=mim)

        default_result = await self._tool().execute(ctx, model_id="m-1")
        default_data = json.loads(default_result.data)
        assert default_data == {"id": "m-1", "filename": "sdxl.safetensors", "type": "checkpoint"}

        desc_result = await self._tool().execute(ctx, model_id="m-1", fields=["description"])
        desc_data = json.loads(desc_result.data)
        assert desc_data["description"] == "A very long description."
        assert "tags" not in desc_data
        assert "provider" not in desc_data

        bogus_result = await self._tool().execute(
            ctx, model_id="m-1", fields=["description", "made_up_field"]
        )
        bogus_data = json.loads(bogus_result.data)
        assert bogus_data["description"] == "A very long description."
        assert "made_up_field" not in bogus_data

    @pytest.mark.asyncio
    async def test_returns_trigger_words_and_guidance(self):
        # Maintainer report 2026-07-16: the tool omitted trigger words (and
        # prompting guidance), so the chat LLM couldn't weave them into prompts.
        raw = {
            "model": {
                "id": "m-2",
                "filename": "style_lora.safetensors",
                "type": "lora",
                "description": "",
                "tags": [],
                "triggers": ["stylized art", "sty1e"],
                "prompting_guidance": "Lead with the trigger words.",
            }
        }
        mim = MagicMock()
        mim.get_model_by_id.return_value = raw
        ctx = make_context(model_index_manager=mim)

        result = await self._tool().execute(ctx, model_id="m-2")

        assert result.success is True
        data = json.loads(result.data)
        assert data["trigger_words"] == ["stylized art", "sty1e"]
        assert data["prompting_guidance"] == "Lead with the trigger words."

    @pytest.mark.asyncio
    async def test_omits_empty_triggers_and_guidance(self):
        raw = {"model": {"id": "m-3", "filename": "f.safetensors", "type": "checkpoint",
                         "description": "", "tags": [], "triggers": []}}
        mim = MagicMock()
        mim.get_model_by_id.return_value = raw
        ctx = make_context(model_index_manager=mim)

        result = await self._tool().execute(ctx, model_id="m-3")

        data = json.loads(result.data)
        assert "trigger_words" not in data
        assert "prompting_guidance" not in data

    @pytest.mark.asyncio
    async def test_no_model_index_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, model_id="x")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_missing_model_id(self):
        mim = MagicMock()
        ctx = make_context(model_index_manager=mim)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "model_id is required" in result.error

    @pytest.mark.asyncio
    async def test_flat_model_data_without_model_key(self):
        """Handle case where get_model_by_id returns a flat dict (no 'model' wrapper)."""
        raw = {
            "id": "m-2",
            "filename": "flux.safetensors",
            "type": "checkpoint",
            "description": "",
            "tags": [],
        }
        mim = MagicMock()
        mim.get_model_by_id.return_value = raw
        ctx = make_context(model_index_manager=mim)

        result = await self._tool().execute(ctx, model_id="m-2")

        assert result.success is True
        data = json.loads(result.data)
        assert data["id"] == "m-2"
        assert "provider" not in data  # no provider_info in raw

    @pytest.mark.asyncio
    async def test_all_lookups_fail_returns_error(self):
        """When get_model_by_id, path lookup, and filename search all fail."""
        mim = MagicMock()
        mim.get_model_by_id.side_effect = KeyError("not found")
        repo = MagicMock()
        repo.get_by_file_path.return_value = None
        repo.get_all.return_value = []
        mim.model_repo = repo
        ctx = make_context(model_index_manager=mim)
        result = await self._tool().execute(ctx, model_id="bad-id")
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_fallback_to_path_lookup(self):
        """When get_model_by_id fails, should try path-based lookup."""
        mim = MagicMock()
        mim.get_model_by_id.side_effect = KeyError("not found")

        model_obj = MagicMock()
        model_obj.to_dict.return_value = {
            "id": "m-path",
            "filename": "ltx-2.3.safetensors",
            "model_type": "checkpoint",
            "description": "LTX model",
            "tags": [{"name": "video"}],
            "providers": [],
        }
        repo = MagicMock()
        repo.get_by_file_path.return_value = model_obj
        mim.model_repo = repo
        ctx = make_context(model_index_manager=mim)

        result = await self._tool().execute(
            ctx, model_id="models/checkpoints/ltx-2.3.safetensors"
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["id"] == "m-path"
        assert data["filename"] == "ltx-2.3.safetensors"
        repo.get_by_file_path.assert_called_once_with(
            "models/checkpoints/ltx-2.3.safetensors", include_providers=True
        )

    @pytest.mark.asyncio
    async def test_fallback_to_filename_search(self):
        """When both ID and path fail, should search by filename."""
        mim = MagicMock()
        mim.get_model_by_id.side_effect = KeyError("not found")

        model_obj = MagicMock()
        model_obj.to_dict.return_value = {
            "id": "m-search",
            "filename": "sdxl.safetensors",
            "model_type": "checkpoint",
            "description": "SDXL",
            "tags": [],
            "providers": [{"name": "civitai", "description": "Community model"}],
        }
        repo = MagicMock()
        repo.get_by_file_path.return_value = None
        repo.get_all.return_value = [model_obj]
        mim.model_repo = repo
        ctx = make_context(model_index_manager=mim)

        result = await self._tool().execute(
            ctx, model_id="models/checkpoints/sdxl.safetensors", fields=["provider"]
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["id"] == "m-search"
        assert data["provider"]["name"] == "civitai"
        repo.get_all.assert_called_once_with(
            search="sdxl.safetensors", limit=1,
            include_providers=True, include_tags=True,
        )

    @pytest.mark.asyncio
    async def test_parameter_description_mentions_path(self):
        tool = self._tool()
        schema = tool.to_schema()
        desc = schema["function"]["parameters"]["properties"]["model_id"]["description"]
        assert "path" in desc.lower()

    @pytest.mark.asyncio
    async def test_both_lookup_paths_return_same_shape_for_equivalent_models(self):
        """The id-lookup path (_summarize) and the path/filename-fallback path
        (_model_obj_to_summary) must produce identical summaries for equivalent
        models — the old shape divergence between them is gone."""
        fields = ["description", "tags", "provider"]

        by_id_raw = {
            "model": {
                "id": "m-1",
                "filename": "style.safetensors",
                "type": "lora",
                "description": "A style LoRA.",
                "tags": ["style", "anime"],
                "triggers": ["sty1e"],
                "prompting_guidance": "Lead with the trigger word.",
                "provider_info": {"name": "civitai", "description": "Community model"},
            }
        }
        mim_by_id = MagicMock()
        mim_by_id.get_model_by_id.return_value = by_id_raw
        ctx_by_id = make_context(model_index_manager=mim_by_id)
        result_by_id = await self._tool().execute(ctx_by_id, model_id="m-1", fields=fields)
        data_by_id = json.loads(result_by_id.data)

        model_obj = MagicMock()
        model_obj.to_dict.return_value = {
            "id": "m-1",
            "filename": "style.safetensors",
            "model_type": "lora",
            "description": "A style LoRA.",
            "tags": [{"name": "style"}, {"name": "anime"}],
            "triggers": ["sty1e"],
            "prompting_guidance": "Lead with the trigger word.",
            "providers": [{"name": "civitai", "description": "Community model"}],
        }
        repo = MagicMock()
        repo.get_by_file_path.return_value = model_obj
        mim_by_path = MagicMock()
        mim_by_path.get_model_by_id.side_effect = KeyError("not found")
        mim_by_path.model_repo = repo
        ctx_by_path = make_context(model_index_manager=mim_by_path)
        result_by_path = await self._tool().execute(
            ctx_by_path, model_id="models/loras/style.safetensors", fields=fields
        )
        data_by_path = json.loads(result_by_path.data)

        assert data_by_id == data_by_path


# ---------------------------------------------------------------------------
# GetPresetInfoTool
# ---------------------------------------------------------------------------

class TestGetPresetInfoTool:
    def _tool(self):
        return GetPresetInfoTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "get_preset_info"
        schema = tool.to_schema()
        assert "preset_id" in schema["function"]["parameters"]["properties"]
        assert schema["function"]["parameters"]["required"] == []

    def _make_preset_data(self):
        return {
            "preset": {
                "id": "p-1",
                "name": "SDXL Standard",
                "description": "Standard SDXL preset",
                "modes": ["t2i", "i2i"],
                "form": [
                    {"name": "prompt", "type": "textarea", "label": "Prompt"},
                    {"name": "steps", "type": "slider", "label": "Steps"},
                ],
                "pipeline": ["checkpoint_loader", "generator"],
            }
        }

    @pytest.mark.asyncio
    async def test_returns_preset_info_with_explicit_id(self):
        pm = MagicMock()
        pm.get_preset.return_value = self._make_preset_data()
        ctx = make_context(preset_manager=pm)

        result = await self._tool().execute(ctx, preset_id="p-1")

        assert result.success is True
        data = json.loads(result.data)
        assert data["id"] == "p-1"
        assert data["name"] == "SDXL Standard"
        assert len(data["form_fields"]) == 2
        assert data["form_fields"][0]["name"] == "prompt"
        assert data["pipeline_steps"] == ["checkpoint_loader", "generator"]
        pm.get_preset.assert_called_once_with("p-1")

    @pytest.mark.asyncio
    async def test_falls_back_to_session_metadata(self):
        pm = MagicMock()
        pm.get_preset.return_value = self._make_preset_data()
        ctx = make_context(preset_manager=pm, session_metadata={"preset_id": "p-1"})

        result = await self._tool().execute(ctx)

        assert result.success is True
        pm.get_preset.assert_called_once_with("p-1")

    @pytest.mark.asyncio
    async def test_no_preset_id_and_no_session_metadata(self):
        pm = MagicMock()
        ctx = make_context(preset_manager=pm)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "No preset_id" in result.error

    @pytest.mark.asyncio
    async def test_no_preset_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, preset_id="p-1")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_preset_with_pipes_key_instead_of_pipeline(self):
        raw = {
            "preset": {
                "id": "p-2",
                "name": "Custom",
                "description": "",
                "modes": [],
                "pipes": ["downloader", "generator"],
            }
        }
        pm = MagicMock()
        pm.get_preset.return_value = raw
        ctx = make_context(preset_manager=pm)

        result = await self._tool().execute(ctx, preset_id="p-2")

        assert result.success is True
        data = json.loads(result.data)
        assert data["pipeline_steps"] == ["downloader", "generator"]

    @pytest.mark.asyncio
    async def test_flat_preset_data_without_preset_key(self):
        raw = {"id": "p-3", "name": "Flat", "description": "", "modes": []}
        pm = MagicMock()
        pm.get_preset.return_value = raw
        ctx = make_context(preset_manager=pm)

        result = await self._tool().execute(ctx, preset_id="p-3")

        assert result.success is True
        data = json.loads(result.data)
        assert data["id"] == "p-3"

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        pm = MagicMock()
        pm.get_preset.side_effect = Exception("preset not found")
        ctx = make_context(preset_manager=pm)
        result = await self._tool().execute(ctx, preset_id="bad")
        assert result.success is False
        assert "preset not found" in result.error


# ---------------------------------------------------------------------------
# ListPhrasebookCategoriesTool
# ---------------------------------------------------------------------------

class TestListPhrasebookCategoriesTool:
    def _tool(self):
        return ListPhrasebookCategoriesTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "list_phrasebook_categories"
        schema = tool.to_schema()
        assert schema["function"]["parameters"]["required"] == []

    @pytest.mark.asyncio
    async def test_returns_categories(self):
        cats = [
            make_category("a1", "Camera Angles", description="Angles", path="/camera", is_active=True),
            make_category("a2", "Art Styles", description="", path="", is_active=False),
        ]
        am = MagicMock()
        am.categories.get_all.return_value = cats
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 2
        assert data["categories"][0]["id"] == "a1"
        assert data["categories"][0]["path"] == "/camera"
        assert data["categories"][0]["is_active"] is True
        assert data["categories"][0]["marker"] == "#/camera"
        assert data["categories"][1]["is_active"] is False
        assert "marker" not in data["categories"][1]
        am.categories.get_all.assert_called_once_with(user_id="user-test")

    @pytest.mark.asyncio
    async def test_marker_uses_bracket_form_when_path_has_spaces(self):
        cats = [
            make_category("a1", "Camera Angles", description="", path="camera angles", is_active=True),
        ]
        am = MagicMock()
        am.categories.get_all.return_value = cats
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx)

        data = json.loads(result.data)
        assert data["categories"][0]["marker"] == "#[camera angles]"

    @pytest.mark.asyncio
    async def test_no_phrasebook_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        am = MagicMock()
        am.categories.get_all.side_effect = ConnectionError("timeout")
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "timeout" in result.error


# ---------------------------------------------------------------------------
# GetPhrasebookValuesTool
# ---------------------------------------------------------------------------

class TestGetPhrasebookValuesTool:
    def _tool(self):
        return GetPhrasebookValuesTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "get_phrasebook_values"
        schema = tool.to_schema()
        assert "category_id" in schema["function"]["parameters"]["properties"]
        assert "category_id" in schema["function"]["parameters"]["required"]

    @pytest.mark.asyncio
    async def test_returns_values(self):
        vals = [
            make_phrasebook_value("v1", "Wide angle", "wide angle lens"),
            make_phrasebook_value("v2", "Close-up", "extreme close-up shot"),
        ]
        am = MagicMock()
        am.values.get_by_category.return_value = vals
        am.get_category_by_id.side_effect = ValueError("Category not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category_id="cat-cam")

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 2
        assert data["values"][0]["id"] == "v1"
        assert data["values"][0]["label"] == "Wide angle"
        assert data["values"][0]["value"] == "wide angle lens"
        am.values.get_by_category.assert_called_once_with(
            category_id="cat-cam", user_id="user-test"
        )

    @pytest.mark.asyncio
    async def test_returns_markers_when_category_resolves(self):
        vals = [
            make_phrasebook_value("v1", "Wide angle", "wide angle lens"),
        ]
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am = MagicMock()
        am.values.get_by_category.return_value = vals
        am.get_category_by_id.return_value = cat
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category_id="cat-cam")

        assert result.success is True
        data = json.loads(result.data)
        assert data["category_path"] == "camera.angles"
        assert data["category_marker"] == "#camera.angles"
        assert data["values"][0]["marker"] == "#[camera.angles.Wide angle]"
        assert "instruction" in data
        am.get_category_by_id.assert_called_once_with(
            category_id="cat-cam", user_id="user-test"
        )

    @pytest.mark.asyncio
    async def test_still_succeeds_without_markers_when_category_lookup_fails(self):
        vals = [
            make_phrasebook_value("v1", "Wide angle", "wide angle lens"),
        ]
        am = MagicMock()
        am.values.get_by_category.return_value = vals
        am.get_category_by_id.side_effect = ValueError("Category not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category_id="cat-cam")

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert "marker" not in data["values"][0]
        assert "category_path" not in data
        assert "category_marker" not in data
        assert "instruction" not in data

    @pytest.mark.asyncio
    async def test_no_phrasebook_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, category_id="x")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_missing_category_id(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "category_id is required" in result.error

    @pytest.mark.asyncio
    async def test_empty_values(self):
        am = MagicMock()
        am.values.get_by_category.return_value = []
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(ctx, category_id="empty-cat")
        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 0
        assert data["values"] == []

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        am = MagicMock()
        am.values.get_by_category.side_effect = PermissionError("access denied")
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(ctx, category_id="restricted")
        assert result.success is False
        assert "access denied" in result.error

    @pytest.mark.asyncio
    async def test_default_limit_is_100_and_reports_has_more(self):
        vals = [make_phrasebook_value(f"v{i}", f"Label {i}", f"value {i}") for i in range(150)]
        am = MagicMock()
        am.values.get_by_category.return_value = vals
        am.get_category_by_id.side_effect = ValueError("Category not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category_id="cat-cam")

        data = json.loads(result.data)
        assert data["limit"] == 100
        assert data["count"] == 100
        assert data["total"] == 150
        assert data["has_more"] is True

    @pytest.mark.asyncio
    async def test_limit_is_hard_capped_at_200(self):
        vals = [make_phrasebook_value(f"v{i}", f"Label {i}", f"value {i}") for i in range(500)]
        am = MagicMock()
        am.values.get_by_category.return_value = vals
        am.get_category_by_id.side_effect = ValueError("Category not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category_id="cat-cam", limit=1000)

        data = json.loads(result.data)
        assert data["limit"] == 200
        assert data["count"] == 200

    @pytest.mark.asyncio
    async def test_offset_pages_through_results(self):
        vals = [make_phrasebook_value(f"v{i}", f"Label {i}", f"value {i}") for i in range(10)]
        am = MagicMock()
        am.values.get_by_category.return_value = vals
        am.get_category_by_id.side_effect = ValueError("Category not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category_id="cat-cam", offset=8, limit=5)

        data = json.loads(result.data)
        assert data["offset"] == 8
        assert data["count"] == 2
        assert data["has_more"] is False
        assert [v["id"] for v in data["values"]] == ["v8", "v9"]

    @pytest.mark.asyncio
    async def test_search_filters_by_label_or_value_case_insensitive(self):
        vals = [
            make_phrasebook_value("v1", "Wide angle", "wide angle lens"),
            make_phrasebook_value("v-cat", "Cat closeup", "a close up of a cat"),
        ]
        am = MagicMock()
        am.values.get_by_category.return_value = vals
        am.get_category_by_id.side_effect = ValueError("Category not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category_id="cat-cam", search="cat")

        data = json.loads(result.data)
        assert data["total"] == 1
        assert data["values"][0]["id"] == "v-cat"

    @pytest.mark.asyncio
    async def test_search_applies_before_marker_pagination_fields(self):
        vals = [
            make_phrasebook_value("v1", "Wide angle", "wide angle lens"),
            make_phrasebook_value("v-cat", "Cat closeup", "a close up of a cat"),
        ]
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am = MagicMock()
        am.values.get_by_category.return_value = vals
        am.get_category_by_id.return_value = cat
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category_id="cat-cam", search="cat")

        data = json.loads(result.data)
        assert data["values"][0]["marker"] == "#[camera.angles.Cat closeup]"
        assert data["total"] == 1


# ---------------------------------------------------------------------------
# ListPhrasebookValuesTool
# ---------------------------------------------------------------------------

class TestListPhrasebookValuesTool:
    def _tool(self):
        return ListPhrasebookValuesTool()

    def _values(self, n):
        return [make_phrasebook_value(f"v{i}", f"Label {i}", f"value {i}") for i in range(n)]

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "list_phrasebook_values"
        assert tool.requires_approval is False
        schema = tool.to_schema()
        assert "category" in schema["function"]["parameters"]["required"]

    @pytest.mark.asyncio
    async def test_resolves_category_by_id(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.get_category_by_id.return_value = cat
        am.values.get_by_category.return_value = self._values(3)
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category="cat-cam")

        assert result.success is True
        data = json.loads(result.data)
        assert data["category_path"] == "camera.angles"
        assert data["total"] == 3
        am.values.get_by_category.assert_called_once_with("cat-cam", "user-test")

    @pytest.mark.asyncio
    async def test_resolves_category_by_path_when_id_lookup_fails(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = self._values(1)
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category="camera.angles")

        assert result.success is True
        am.categories.get_by_path.assert_called_once_with("camera.angles", "user-test")

    @pytest.mark.asyncio
    async def test_unknown_category_returns_error(self):
        am = MagicMock()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = None
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category="missing.category")

        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_default_limit_is_100_and_reports_has_more(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera", path="camera")
        am.get_category_by_id.return_value = cat
        am.values.get_by_category.return_value = self._values(150)
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category="cat-cam")

        data = json.loads(result.data)
        assert data["limit"] == 100
        assert data["returned"] == 100
        assert data["total"] == 150
        assert data["has_more"] is True
        assert data["values"][0]["id"] == "v0"

    @pytest.mark.asyncio
    async def test_limit_is_hard_capped_at_200(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera", path="camera")
        am.get_category_by_id.return_value = cat
        am.values.get_by_category.return_value = self._values(500)
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category="cat-cam", limit=1000)

        data = json.loads(result.data)
        assert data["limit"] == 200
        assert data["returned"] == 200

    @pytest.mark.asyncio
    async def test_offset_pages_through_results(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera", path="camera")
        am.get_category_by_id.return_value = cat
        am.values.get_by_category.return_value = self._values(10)
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category="cat-cam", offset=8, limit=5)

        data = json.loads(result.data)
        assert data["offset"] == 8
        assert data["returned"] == 2
        assert data["has_more"] is False
        assert [v["id"] for v in data["values"]] == ["v8", "v9"]

    @pytest.mark.asyncio
    async def test_search_filters_by_label_or_value_case_insensitive(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera", path="camera")
        am.get_category_by_id.return_value = cat
        am.values.get_by_category.return_value = [
            self._values(1)[0],
            make_phrasebook_value("v-cat", "Cat closeup", "a close up of a cat"),
            make_phrasebook_value("v-kitten", "Kitten macro", "macro shot of a kitten"),
        ]
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category="cat-cam", search="cat")

        data = json.loads(result.data)
        assert data["total"] == 1
        assert data["values"][0]["id"] == "v-cat"

    @pytest.mark.asyncio
    async def test_returns_dense_id_text_active_shape(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera", path="camera")
        am.get_category_by_id.return_value = cat
        val = self._values(1)[0]
        val.is_active = False
        am.values.get_by_category.return_value = [val]
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, category="cat-cam")

        data = json.loads(result.data)
        entry = data["values"][0]
        assert set(entry.keys()) == {"id", "text", "active"}
        assert entry["text"] == "Label 0: value 0"
        assert entry["active"] is False

    @pytest.mark.asyncio
    async def test_no_phrasebook_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, category="camera")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_missing_category_returns_error(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "category" in result.error

    @pytest.mark.asyncio
    async def test_cross_user_isolation_category_not_found(self):
        """A category owned by another user must resolve as not-found for both
        the id and the path lookup, never leaking that user's values."""
        am = MagicMock()

        def get_by_id(category_id, user_id):
            if user_id != "owner":
                raise ValueError("Category not found")
            return make_category("cat-cam", "Camera Angles", path="camera.angles")

        def get_by_path(path, user_id):
            return None if user_id != "owner" else make_category("cat-cam", "Camera Angles", path=path)

        am.get_category_by_id.side_effect = get_by_id
        am.categories.get_by_path.side_effect = get_by_path
        ctx = make_context(phrasebook_manager=am)
        ctx.user_id = "intruder"

        result = await self._tool().execute(ctx, category="cat-cam")

        assert result.success is False
        assert "not found" in result.error
        am.values.get_by_category.assert_not_called()


# ---------------------------------------------------------------------------
# CreatePhrasebookCategoryTool
# ---------------------------------------------------------------------------

class TestCreatePhrasebookCategoryTool:
    def _tool(self):
        return CreatePhrasebookCategoryTool()

    def test_name_and_approval(self):
        tool = self._tool()
        assert tool.name == "create_phrasebook_category"
        assert tool.requires_approval is True
        schema = tool.to_schema()
        assert "path" in schema["function"]["parameters"]["required"]

    @pytest.mark.asyncio
    async def test_preview_nested_path_with_existing_parent(self):
        am = MagicMock()
        parent = make_category("cat-parent", "Camera", path="camera")

        def get_by_path(path, user_id):
            if path == "camera.angles":
                return None
            if path == "camera":
                return parent
            return None

        am.categories.get_by_path.side_effect = get_by_path
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, path="camera.angles", description="Camera angle options")

        assert result.success is True
        data = json.loads(result.data)
        assert data["status"] == "pending_approval"
        assert data["path"] == "camera.angles"
        assert data["name"] == "angles"
        assert data["parent_path"] == "camera"
        assert data["marker"] == "#camera.angles"

    @pytest.mark.asyncio
    async def test_preview_uses_explicit_name(self):
        am = MagicMock()
        am.categories.get_by_path.return_value = None
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, path="lighting", name="Lighting Setups")

        data = json.loads(result.data)
        assert data["name"] == "Lighting Setups"
        assert data["parent_path"] is None

    @pytest.mark.asyncio
    async def test_duplicate_path_returns_error(self):
        am = MagicMock()
        am.categories.get_by_path.return_value = make_category("existing-1", "Angles", path="camera.angles")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, path="camera.angles")

        assert result.success is False
        assert "already exists" in result.error

    @pytest.mark.asyncio
    async def test_missing_parent_returns_error(self):
        am = MagicMock()
        am.categories.get_by_path.return_value = None  # neither the path nor the parent exist
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, path="camera.angles")

        assert result.success is False
        assert "camera" in result.error
        assert "create_phrasebook_category" in result.error

    @pytest.mark.asyncio
    async def test_invalid_path_returns_error(self):
        ctx = make_context(phrasebook_manager=MagicMock())
        result = await self._tool().execute(ctx, path="")
        assert result.success is False
        assert "path" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_phrasebook_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, path="camera")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_confirmed_creates_category(self):
        am = MagicMock()
        am.categories.get_by_path.return_value = None
        created = make_category("cat-new", "Angles", path="angles")
        am.create_category.return_value = created
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(
            ctx, path="angles", name="Angles", description="desc"
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["created"] is True
        assert data["category"]["id"] == "cat-new"
        assert data["category"]["path"] == "angles"
        assert data["marker"] == "#angles"

        call_args = am.create_category.call_args
        request = call_args[0][0]
        assert request.name == "Angles"
        assert request.path == "angles"
        assert request.description == "desc"

    @pytest.mark.asyncio
    async def test_confirmed_maps_value_error_to_failure(self):
        am = MagicMock()
        am.categories.get_by_path.return_value = None
        am.create_category.side_effect = ValueError("Category with this path already exists")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(ctx, path="angles")

        assert result.success is False
        assert "already exists" in result.error

    @pytest.mark.asyncio
    async def test_confirmed_missing_parent_returns_error(self):
        am = MagicMock()
        am.categories.get_by_path.return_value = None
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(ctx, path="camera.angles")

        assert result.success is False
        assert "camera" in result.error
        am.create_category.assert_not_called()


# ---------------------------------------------------------------------------
# CreatePhrasebookValuesTool
# ---------------------------------------------------------------------------

class TestCreatePhrasebookValuesTool:
    def _tool(self):
        return CreatePhrasebookValuesTool()

    def test_name_and_approval(self):
        tool = self._tool()
        assert tool.name == "create_phrasebook_values"
        assert tool.requires_approval is True
        schema = tool.to_schema()
        assert "values" in schema["function"]["parameters"]["required"]

    @pytest.mark.asyncio
    async def test_preview_by_category_path(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = []
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category_path="camera.angles",
            values=[{"label": "Wide angle"}, {"label": "Close-up", "value": "extreme close-up shot"}],
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["status"] == "pending_approval"
        assert data["category_path"] == "camera.angles"
        assert data["count"] == 2
        assert data["values"][0] == {"label": "Wide angle", "value": "Wide angle"}
        assert data["values"][1] == {"label": "Close-up", "value": "extreme close-up shot"}
        am.categories.get_by_path.assert_called_once_with("camera.angles", "user-test")

    @pytest.mark.asyncio
    async def test_preview_by_category_id(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.get_category_by_id.return_value = cat
        am.values.get_by_category.return_value = []
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category_id="cat-cam", values=[{"label": "Wide angle"}]
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["category_path"] == "camera.angles"
        am.get_category_by_id.assert_called_once_with(category_id="cat-cam", user_id="user-test")

    @pytest.mark.asyncio
    async def test_category_not_found_mentions_create_tool(self):
        am = MagicMock()
        am.categories.get_by_path.return_value = None
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category_path="missing.category", values=[{"label": "Wide angle"}]
        )

        assert result.success is False
        assert "create_phrasebook_category" in result.error

    @pytest.mark.asyncio
    async def test_missing_category_identifier_returns_error(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, values=[{"label": "Wide angle"}])

        assert result.success is False
        assert "category_path" in result.error or "category_id" in result.error

    @pytest.mark.asyncio
    async def test_duplicate_labels_excluded_and_warned(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = [
            make_phrasebook_value("v1", "Wide angle", "wide angle lens"),
        ]
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category_path="camera.angles",
            values=[{"label": "Wide angle"}, {"label": "Close-up"}],
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["values"][0]["label"] == "Close-up"
        assert any("Wide angle" in w for w in data["warnings"])

    @pytest.mark.asyncio
    async def test_empty_values_returns_error(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(ctx, category_path="camera.angles", values=[])
        assert result.success is False
        assert "values" in result.error.lower()

    @pytest.mark.asyncio
    async def test_missing_label_returns_error(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(
            ctx, category_path="camera.angles", values=[{"label": ""}]
        )
        assert result.success is False
        assert "label" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_phrasebook_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, category_path="camera.angles", values=[{"label": "x"}])
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_confirmed_creates_each_value_with_markers(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = []

        def create_value(request, user_id):
            return make_phrasebook_value("v-new", request.label, request.value)

        am.create_value.side_effect = create_value
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(
            ctx, category_path="camera.angles",
            values=[{"label": "Wide angle"}, {"label": "Close-up", "value": "extreme close-up shot"}],
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["created_count"] == 2
        assert data["values"][0]["marker"] == "#[camera.angles.Wide angle]"
        assert data["values"][1]["marker"] == "#camera.angles.Close-up"
        assert "instruction" in data
        assert am.create_value.call_count == 2

    @pytest.mark.asyncio
    async def test_confirmed_partial_failure_reports_failed_and_succeeds(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = []

        def create_value(request, user_id):
            if request.label == "Bad":
                raise ValueError("Category not found")
            return make_phrasebook_value("v-ok", request.label, request.value)

        am.create_value.side_effect = create_value
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(
            ctx, category_path="camera.angles",
            values=[{"label": "Good"}, {"label": "Bad"}],
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["created_count"] == 1
        assert data["values"][0]["label"] == "Good"
        assert len(data["failed"]) == 1
        assert data["failed"][0]["label"] == "Bad"

    @pytest.mark.asyncio
    async def test_confirmed_all_fail_returns_failure(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = []
        am.create_value.side_effect = ValueError("Category not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(
            ctx, category_path="camera.angles", values=[{"label": "Wide angle"}]
        )

        assert result.success is False
        assert "Category not found" in result.error

    @pytest.mark.asyncio
    async def test_confirmed_re_excludes_duplicates_created_meanwhile(self):
        """execute_confirmed receives the ORIGINAL args, so it must re-check for
        duplicates in case a value was created between preview and confirmation."""
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = [
            make_phrasebook_value("v1", "Wide angle", "wide angle lens"),
        ]

        def create_value(request, user_id):
            return make_phrasebook_value("v-new", request.label, request.value)

        am.create_value.side_effect = create_value
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(
            ctx, category_path="camera.angles",
            values=[{"label": "Wide angle"}, {"label": "Close-up"}],
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["created_count"] == 1
        assert data["values"][0]["label"] == "Close-up"
        am.create_value.assert_called_once()


# ---------------------------------------------------------------------------
# RemovePhrasebookValuesTool
# ---------------------------------------------------------------------------

class TestRemovePhrasebookValuesTool:
    def _tool(self):
        return RemovePhrasebookValuesTool()

    def _value(self, id_, label, category_id="cat-cam"):
        val = make_phrasebook_value(id_, label, label)
        val.category_id = category_id
        return val

    def test_name_and_approval(self):
        tool = self._tool()
        assert tool.name == "remove_phrasebook_values"
        assert tool.requires_approval is True
        schema = tool.to_schema()
        assert "value_ids" in schema["function"]["parameters"]["required"]

    @pytest.mark.asyncio
    async def test_preview_returns_matched_values(self):
        am = MagicMock()
        am.get_value_by_id.side_effect = lambda vid, user_id: self._value(vid, f"Label {vid}")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, value_ids=["v1", "v2"])

        assert result.success is True
        data = json.loads(result.data)
        assert data["status"] == "pending_approval"
        assert data["count"] == 2
        assert {v["id"] for v in data["values"]} == {"v1", "v2"}

    @pytest.mark.asyncio
    async def test_preview_skips_unknown_ids(self):
        am = MagicMock()

        def get_value(vid, user_id):
            if vid == "missing":
                raise ValueError("Value not found")
            return self._value(vid, "Wide angle")

        am.get_value_by_id.side_effect = get_value
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, value_ids=["v1", "missing"])

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["skipped"][0]["id"] == "missing"

    @pytest.mark.asyncio
    async def test_preview_all_missing_returns_error(self):
        am = MagicMock()
        am.get_value_by_id.side_effect = ValueError("Value not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(ctx, value_ids=["missing"])

        assert result.success is False
        assert "None of the given value_ids" in result.error

    @pytest.mark.asyncio
    async def test_preview_category_scope_rejects_mismatched_values(self):
        am = MagicMock()
        cat = make_category("cat-cam", "Camera Angles", path="camera.angles")
        am.categories.get_by_path.return_value = cat
        am.get_value_by_id.side_effect = lambda vid, user_id: self._value(
            vid, "Wide angle", category_id="cat-other" if vid == "wrong" else "cat-cam"
        )
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, value_ids=["v1", "wrong"], category_path="camera.angles"
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["values"][0]["id"] == "v1"
        assert data["skipped"][0]["id"] == "wrong"

    @pytest.mark.asyncio
    async def test_preview_unknown_category_path_returns_error(self):
        am = MagicMock()
        am.categories.get_by_path.return_value = None
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, value_ids=["v1"], category_path="missing.category"
        )

        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_missing_value_ids_returns_error(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "value_ids" in result.error

    @pytest.mark.asyncio
    async def test_no_phrasebook_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, value_ids=["v1"])
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_confirmed_deletes_each_value(self):
        am = MagicMock()
        am.get_value_by_id.side_effect = lambda vid, user_id: self._value(vid, f"Label {vid}")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(ctx, value_ids=["v1", "v2"])

        assert result.success is True
        data = json.loads(result.data)
        assert data["deleted_count"] == 2
        assert am.delete_value.call_count == 2
        am.delete_value.assert_any_call("v1", "user-test")
        am.delete_value.assert_any_call("v2", "user-test")

    @pytest.mark.asyncio
    async def test_confirmed_partial_failure_reports_failed_and_succeeds(self):
        am = MagicMock()
        am.get_value_by_id.side_effect = lambda vid, user_id: self._value(vid, f"Label {vid}")

        def delete_value(vid, user_id):
            if vid == "v2":
                raise ValueError("Value not found")
            return True

        am.delete_value.side_effect = delete_value
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(ctx, value_ids=["v1", "v2"])

        assert result.success is True
        data = json.loads(result.data)
        assert data["deleted_count"] == 1
        assert data["values"][0]["id"] == "v1"
        assert data["failed"][0]["id"] == "v2"

    @pytest.mark.asyncio
    async def test_confirmed_all_fail_returns_failure(self):
        am = MagicMock()
        am.get_value_by_id.side_effect = ValueError("Value not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(ctx, value_ids=["missing"])

        assert result.success is False
        assert "Failed to delete" in result.error

    @pytest.mark.asyncio
    async def test_cross_user_isolation_value_not_found(self):
        """A value owned by another user must resolve as not-found, never deleted."""
        am = MagicMock()

        def get_value(vid, user_id):
            if user_id != "owner":
                raise ValueError("Value not found")
            return self._value(vid, "Wide angle")

        am.get_value_by_id.side_effect = get_value
        ctx = make_context(phrasebook_manager=am)
        ctx.user_id = "intruder"

        result = await self._tool().execute(ctx, value_ids=["v1"])

        assert result.success is False
        am.delete_value.assert_not_called()


# ---------------------------------------------------------------------------
# UpdatePhrasebookValuesTool
# ---------------------------------------------------------------------------

class TestUpdatePhrasebookValuesTool:
    def _tool(self):
        return UpdatePhrasebookValuesTool()

    def _category(self, id_="cat-cam", path="camera.angles"):
        return make_category(id_, "Camera Angles", path=path)

    def _value(self, id_, label, value=None, category_id="cat-cam"):
        val = make_phrasebook_value(id_, label, value or label)
        val.category_id = category_id
        return val

    def test_name_and_approval(self):
        tool = self._tool()
        assert tool.name == "update_phrasebook_values"
        assert tool.requires_approval is True
        schema = tool.to_schema()
        required = schema["function"]["parameters"]["required"]
        assert "category" in required
        assert "edits" in required

    @pytest.mark.asyncio
    async def test_no_phrasebook_manager(self):
        ctx = make_context()
        result = await self._tool().execute(ctx, category="camera.angles", edits=[{"id": "v1", "new_label": "x"}])
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_missing_category_returns_error(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(ctx, edits=[{"id": "v1", "new_label": "x"}])
        assert result.success is False
        assert "category" in result.error.lower()

    @pytest.mark.asyncio
    async def test_empty_edits_returns_error(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(ctx, category="camera.angles", edits=[])
        assert result.success is False
        assert "edits" in result.error.lower()

    @pytest.mark.asyncio
    async def test_edit_missing_id_returns_error(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(
            ctx, category="camera.angles", edits=[{"new_label": "Wide angle"}]
        )
        assert result.success is False
        assert "id" in result.error.lower()

    @pytest.mark.asyncio
    async def test_edit_missing_new_fields_returns_error(self):
        am = MagicMock()
        ctx = make_context(phrasebook_manager=am)
        result = await self._tool().execute(
            ctx, category="camera.angles", edits=[{"id": "v1"}]
        )
        assert result.success is False
        assert "new_label" in result.error or "new_value" in result.error

    @pytest.mark.asyncio
    async def test_category_not_found_returns_error(self):
        am = MagicMock()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = None
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category="missing.category", edits=[{"id": "v1", "new_label": "x"}]
        )
        assert result.success is False
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_preview_rename_label(self):
        am = MagicMock()
        cat = self._category()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = [self._value("v1", "Wide")]
        am.get_value_by_id.side_effect = lambda vid, user_id: self._value(vid, "Wide")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category="camera.angles", edits=[{"id": "v1", "new_label": "Wide angle"}]
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["status"] == "pending_approval"
        assert data["category_path"] == "camera.angles"
        assert data["count"] == 1
        edit = data["edits"][0]
        assert edit["old_label"] == "Wide"
        assert edit["new_label"] == "Wide angle"
        assert edit["new_value"] == "Wide"
        assert result.preview.action == "Edit values"
        assert result.preview.target == "in category camera.angles"
        assert "Wide" in result.preview.items[0] and "Wide angle" in result.preview.items[0]

    @pytest.mark.asyncio
    async def test_preview_change_value_text(self):
        am = MagicMock()
        cat = self._category()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = [self._value("v1", "Wide", "wide angle lens")]
        am.get_value_by_id.side_effect = lambda vid, user_id: self._value(vid, "Wide", "wide angle lens")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category="camera.angles",
            edits=[{"id": "v1", "new_value": "extreme wide angle lens"}],
        )

        assert result.success is True
        data = json.loads(result.data)
        edit = data["edits"][0]
        assert edit["new_label"] == "Wide"
        assert edit["new_value"] == "extreme wide angle lens"

    @pytest.mark.asyncio
    async def test_preview_both_label_and_value(self):
        am = MagicMock()
        cat = self._category()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = [self._value("v1", "Wide", "wide angle lens")]
        am.get_value_by_id.side_effect = lambda vid, user_id: self._value(vid, "Wide", "wide angle lens")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category="camera.angles",
            edits=[{"id": "v1", "new_label": "Wide angle", "new_value": "extreme wide angle lens"}],
        )

        assert result.success is True
        data = json.loads(result.data)
        edit = data["edits"][0]
        assert edit["new_label"] == "Wide angle"
        assert edit["new_value"] == "extreme wide angle lens"

    @pytest.mark.asyncio
    async def test_preview_skips_unknown_id(self):
        am = MagicMock()
        cat = self._category()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = [self._value("v1", "Wide")]

        def get_value(vid, user_id):
            if vid == "missing":
                raise ValueError("Value not found")
            return self._value(vid, "Wide")

        am.get_value_by_id.side_effect = get_value
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category="camera.angles",
            edits=[{"id": "v1", "new_label": "Wide angle"}, {"id": "missing", "new_label": "x"}],
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["skipped"][0]["id"] == "missing"

    @pytest.mark.asyncio
    async def test_preview_all_unknown_returns_error(self):
        am = MagicMock()
        cat = self._category()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = []
        am.get_value_by_id.side_effect = ValueError("Value not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category="camera.angles", edits=[{"id": "missing", "new_label": "x"}]
        )

        assert result.success is False
        assert "None of the given edits" in result.error

    @pytest.mark.asyncio
    async def test_preview_label_collision_skipped(self):
        am = MagicMock()
        cat = self._category()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        values_by_id = {
            "v1": self._value("v1", "Wide"),
            "v2": self._value("v2", "Close-up"),
            "v3": self._value("v3", "Macro"),
        }
        am.values.get_by_category.return_value = list(values_by_id.values())
        am.get_value_by_id.side_effect = lambda vid, user_id: values_by_id[vid]
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute(
            ctx, category="camera.angles",
            edits=[
                {"id": "v1", "new_label": "close-up"},
                {"id": "v3", "new_label": "Extreme macro"},
            ],
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["edits"][0]["id"] == "v3"
        assert data["skipped"][0]["id"] == "v1"
        assert "close-up" in data["skipped"][0]["error"]

    @pytest.mark.asyncio
    async def test_confirmed_applies_edit_with_fresh_marker(self):
        am = MagicMock()
        cat = self._category()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = [self._value("v1", "Wide")]
        am.get_value_by_id.side_effect = lambda vid, user_id: self._value(vid, "Wide")

        def update_value(value_id, request, user_id):
            return make_phrasebook_value(value_id, request.label, request.value)

        am.update_value.side_effect = update_value
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(
            ctx, category="camera.angles", edits=[{"id": "v1", "new_label": "Wide angle"}]
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["updated_count"] == 1
        assert data["values"][0]["label"] == "Wide angle"
        assert data["values"][0]["marker"] == "#[camera.angles.Wide angle]"
        am.update_value.assert_called_once()
        call_args = am.update_value.call_args
        assert call_args[0][0] == "v1"
        assert call_args[0][1].label == "Wide angle"

    @pytest.mark.asyncio
    async def test_confirmed_partial_failure_reports_failed_and_succeeds(self):
        am = MagicMock()
        cat = self._category()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = [
            self._value("v1", "Wide"), self._value("v2", "Close-up"),
        ]
        am.get_value_by_id.side_effect = lambda vid, user_id: (
            self._value("v1", "Wide") if vid == "v1" else self._value("v2", "Close-up")
        )

        def update_value(value_id, request, user_id):
            if value_id == "v2":
                raise ValueError("Value not found")
            return make_phrasebook_value(value_id, request.label, request.value)

        am.update_value.side_effect = update_value
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(
            ctx, category="camera.angles",
            edits=[
                {"id": "v1", "new_label": "Wide angle"},
                {"id": "v2", "new_label": "Close up shot"},
            ],
        )

        assert result.success is True
        data = json.loads(result.data)
        assert data["updated_count"] == 1
        assert data["values"][0]["label"] == "Wide angle"
        assert data["failed"][0]["id"] == "v2"

    @pytest.mark.asyncio
    async def test_confirmed_all_fail_returns_failure(self):
        am = MagicMock()
        cat = self._category()
        am.get_category_by_id.side_effect = ValueError("Category not found")
        am.categories.get_by_path.return_value = cat
        am.values.get_by_category.return_value = [self._value("v1", "Wide")]
        am.get_value_by_id.side_effect = lambda vid, user_id: self._value(vid, "Wide")
        am.update_value.side_effect = ValueError("Value not found")
        ctx = make_context(phrasebook_manager=am)

        result = await self._tool().execute_confirmed(
            ctx, category="camera.angles", edits=[{"id": "v1", "new_label": "Wide angle"}]
        )

        assert result.success is False
        assert "Failed to update" in result.error


# ---------------------------------------------------------------------------
# EnhancePromptTool
# ---------------------------------------------------------------------------

class TestEnhancePromptTool:
    def _tool(self):
        return EnhancePromptTool()

    def _manager(self, candidates=None):
        manager = MagicMock()
        manager.enhance = AsyncMock(return_value={
            "candidates": [{"text": c, "direction": ""} for c in (candidates or ["a rich prompt"])],
            "model_id": "m-1",
            "brief": "a cat",
            "exemplar_ids": [],
        })
        return manager

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "enhance_prompt"
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert "brief" in schema["function"]["parameters"]["properties"]
        assert schema["function"]["parameters"]["required"] == []

    @pytest.mark.asyncio
    async def test_missing_manager_returns_error(self):
        ctx = make_context(llm_id="llm-1")
        result = await self._tool().execute(ctx, brief="a cat")
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_missing_llm_id_returns_error(self):
        ctx = make_context(prompt_enhancement_manager=self._manager())
        result = await self._tool().execute(ctx, brief="a cat")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_segment_instruction_calls_the_real_tool(self):
        """The enhanced prompt is presented by calling update_segment as a real
        tool -- never wrapped in <tool_action> markup in the reply text."""
        ctx = make_context(prompt_enhancement_manager=self._manager(), llm_id="llm-1")
        instruction = json.loads((await self._tool().execute(ctx, brief="a cat")).data)["instruction"]
        assert "update_segment" in instruction
        assert "tool_action" not in instruction

    @pytest.mark.asyncio
    async def test_brief_passed_to_manager(self):
        manager = self._manager()
        ctx = make_context(prompt_enhancement_manager=manager, llm_id="llm-1")
        result = await self._tool().execute(ctx, brief="a cat")
        assert result.success is True
        data = json.loads(result.data)
        assert data["enhanced_prompt"] == "a rich prompt"
        assert "EXACTLY as-is" in data["instruction"]
        manager.enhance.assert_awaited_once()
        assert manager.enhance.await_args.kwargs["brief"] == "a cat"

    @pytest.mark.asyncio
    async def test_brief_falls_back_to_segments(self):
        manager = self._manager()
        ctx = make_context(
            prompt_enhancement_manager=manager,
            llm_id="llm-1",
            session_metadata={"segments": [
                {"content": "a fox", "type": "content", "isDisabled": False},
                {"content": "blurry", "type": "negative", "isDisabled": False},
                {"content": "ignored", "type": "content", "isDisabled": True},
            ]},
        )
        result = await self._tool().execute(ctx)
        assert result.success is True
        assert manager.enhance.await_args.kwargs["brief"] == "a fox"

    @pytest.mark.asyncio
    async def test_no_brief_anywhere_returns_error(self):
        ctx = make_context(prompt_enhancement_manager=self._manager(), llm_id="llm-1")
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "No prompt to enhance" in result.error

    @pytest.mark.asyncio
    async def test_multiple_candidates_exposed_as_alternatives(self):
        manager = self._manager(candidates=["first", "second"])
        ctx = make_context(prompt_enhancement_manager=manager, llm_id="llm-1")
        result = await self._tool().execute(ctx, brief="a cat", n_candidates=2)
        data = json.loads(result.data)
        assert data["enhanced_prompt"] == "first"
        assert data["alternative_prompts"] == ["second"]

    @pytest.mark.asyncio
    async def test_manager_exception_returns_error(self):
        manager = MagicMock()
        manager.enhance = AsyncMock(side_effect=RuntimeError("boom"))
        ctx = make_context(prompt_enhancement_manager=manager, llm_id="llm-1")
        result = await self._tool().execute(ctx, brief="a cat")
        assert result.success is False
        assert "boom" in result.error

    def test_available_without_form_state(self):
        assert self._tool().is_available(None) is True

    def test_available_when_video_director_inactive(self):
        form_state = {"video_director": {"active": False}}
        assert self._tool().is_available(form_state) is True

    def test_unavailable_when_video_director_active(self):
        """enhance_prompt's returned instruction points the model at the
        update_segment tool, which has no target once the Video Director owns
        "segment #N" (a shot) -- so the tool drops out entirely there."""
        form_state = {"video_director": {"active": True}}
        assert self._tool().is_available(form_state) is False


# ---------------------------------------------------------------------------
# GetCurrentSegmentsTool
# ---------------------------------------------------------------------------

class TestGetCurrentSegmentsTool:
    def _tool(self):
        return GetCurrentSegmentsTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "get_current_segments"
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["required"] == []
        assert schema["function"]["parameters"]["properties"] == {}

    @pytest.mark.asyncio
    async def test_returns_segments(self):
        segments = [
            {"id": "seg-1", "content": "cinematic shot", "name": "Shot", "type": "style", "enabled": True},
            {"id": "seg-2", "content": "golden hour lighting", "name": "Light", "type": "lighting", "enabled": False},
        ]
        ctx = make_context(session_metadata={"segments": segments})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 2
        assert data["segments"][0]["index"] == 0
        assert data["segments"][0]["id"] == "seg-1"
        assert data["segments"][0]["content"] == "cinematic shot"
        assert data["segments"][0]["name"] == "Shot"
        assert data["segments"][0]["type"] == "style"
        assert data["segments"][0]["enabled"] is True
        assert data["segments"][1]["index"] == 1
        assert data["segments"][1]["enabled"] is False

    @pytest.mark.asyncio
    async def test_no_segments_in_metadata(self):
        ctx = make_context(session_metadata={})

        result = await self._tool().execute(ctx)

        assert result.success is False
        assert "No segment data available" in result.error
        assert "tools context may not include segment data" in result.error

    @pytest.mark.asyncio
    async def test_empty_segments_list(self):
        ctx = make_context(session_metadata={"segments": []})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 0
        assert data["segments"] == []

    @pytest.mark.asyncio
    async def test_includes_template_provenance_when_present(self):
        segments = [
            {
                "id": "seg-1",
                "content": "cinematic shot",
                "name": "Shot",
                "type": "style",
                "enabled": True,
                "template": {"id": "tmpl-1", "name": "Cinematic Base", "slot": "Shot", "position": 0},
            },
            {"id": "seg-2", "content": "golden hour lighting", "name": "Light", "type": "lighting", "enabled": False},
        ]
        ctx = make_context(session_metadata={"segments": segments})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["segments"][0]["template"] == {
            "id": "tmpl-1",
            "name": "Cinematic Base",
            "slot": "Shot",
            "position": 0,
        }
        assert "template" not in data["segments"][1]

    @pytest.mark.asyncio
    async def test_includes_proposal_instruction(self):
        ctx = make_context(session_metadata={"segments": [
            {"id": "seg-1", "content": "wide shot", "title": "Shot", "type": "framing", "isDisabled": False}
        ]})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert "instruction" in data
        assert "update_segment" in data["instruction"]
        assert "tool_action" not in data["instruction"]
        assert "phrasebook chips" in data["instruction"]
        assert "get_phrasebook_values" in data["instruction"]

    def test_available_without_form_state(self):
        assert self._tool().is_available(None) is True

    def test_available_when_video_director_inactive(self):
        form_state = {"video_director": {"active": False}}
        assert self._tool().is_available(form_state) is True

    def test_unavailable_when_video_director_active(self):
        form_state = {"video_director": {"active": True}}
        assert self._tool().is_available(form_state) is False


# ---------------------------------------------------------------------------
# UpdateSegmentTool
# ---------------------------------------------------------------------------

class TestUpdateSegmentToolSchema:
    def _tool(self):
        return UpdateSegmentTool()

    def test_name_and_requires_approval(self):
        tool = self._tool()
        assert tool.name == "update_segment"
        assert tool.requires_approval is True

    def test_parameters_requires_updates(self):
        schema = self._tool().to_schema()["function"]["parameters"]
        assert schema["required"] == ["updates"]
        item_props = schema["properties"]["updates"]["items"]["properties"]
        assert set(item_props) == {"segment_id", "segment_index", "content"}

    def test_available_without_form_state(self):
        assert self._tool().is_available(None) is True

    def test_unavailable_when_video_director_active(self):
        form_state = {"video_director": {"active": True}}
        assert self._tool().is_available(form_state) is False


class TestUpdateSegmentToolExecute:
    def _tool(self):
        return UpdateSegmentTool()

    def _segments(self):
        return [
            {"id": "seg-1", "content": "a lone hiker", "name": "Subject", "type": "content", "enabled": True},
            {"id": "seg-2", "content": "golden hour lighting", "name": "Light", "type": "lighting", "enabled": True},
        ]

    @pytest.mark.asyncio
    async def test_no_updates_is_an_error(self):
        ctx = make_context(session_metadata={"segments": self._segments()})
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "No updates provided" in result.error

    @pytest.mark.asyncio
    async def test_no_segments_loaded_is_an_error(self):
        ctx = make_context(session_metadata={})
        result = await self._tool().execute(ctx, updates=[{"segment_id": "seg-1", "content": "x"}])
        assert result.success is False
        assert "No segment data available" in result.error

    @pytest.mark.asyncio
    async def test_resolves_by_id(self):
        ctx = make_context(session_metadata={"segments": self._segments()})
        result = await self._tool().execute(ctx, updates=[
            {"segment_id": "seg-2", "segment_index": 99, "content": "blue hour lighting"},
        ])
        assert result.success is True
        data = json.loads(result.data)
        assert data["status"] == "pending_approval"
        assert data["updates"] == [
            {"segment_id": "seg-2", "segment_index": 1, "content": "blue hour lighting"},
        ]

    @pytest.mark.asyncio
    async def test_resolves_by_index_when_id_missing(self):
        ctx = make_context(session_metadata={"segments": self._segments()})
        result = await self._tool().execute(ctx, updates=[
            {"segment_index": 0, "content": "a lone hiker in a red parka"},
        ])
        assert result.success is True
        data = json.loads(result.data)
        assert data["updates"] == [
            {"segment_id": "seg-1", "segment_index": 0, "content": "a lone hiker in a red parka"},
        ]

    @pytest.mark.asyncio
    async def test_resolves_by_index_when_id_unknown(self):
        ctx = make_context(session_metadata={"segments": self._segments()})
        result = await self._tool().execute(ctx, updates=[
            {"segment_id": "stale-id", "segment_index": 1, "content": "blue hour lighting"},
        ])
        assert result.success is True
        data = json.loads(result.data)
        assert data["updates"] == [
            {"segment_id": "seg-2", "segment_index": 1, "content": "blue hour lighting"},
        ]

    @pytest.mark.asyncio
    async def test_unknown_segment_lists_valid_ids_and_indices(self):
        ctx = make_context(session_metadata={"segments": self._segments()})
        result = await self._tool().execute(ctx, updates=[
            {"segment_id": "nope", "segment_index": 99, "content": "x"},
        ])
        assert result.success is False
        assert "seg-1" in result.error
        assert "seg-2" in result.error
        assert "0-1" in result.error

    @pytest.mark.asyncio
    async def test_preview_summarizes_each_change(self):
        ctx = make_context(session_metadata={"segments": self._segments()})
        result = await self._tool().execute(ctx, updates=[
            {"segment_id": "seg-1", "content": "a lone hiker in a red parka"},
        ])
        assert result.preview is not None
        assert result.preview.action == "Update segments"
        assert result.preview.items == ["Subject: a lone hiker in a red parka"]

    @pytest.mark.asyncio
    async def test_reason_is_included_when_provided(self):
        ctx = make_context(session_metadata={"segments": self._segments()})
        result = await self._tool().execute(ctx, updates=[
            {"segment_id": "seg-1", "content": "x"},
        ], reason="sharper subject")
        data = json.loads(result.data)
        assert data["reason"] == "sharper subject"


class TestUpdateSegmentToolExecuteConfirmed:
    def _tool(self):
        return UpdateSegmentTool()

    def _segments(self):
        return [
            {"id": "seg-1", "content": "a lone hiker", "name": "Subject", "type": "content", "enabled": True},
        ]

    @pytest.mark.asyncio
    async def test_confirmed_returns_apply_action_payload(self):
        ctx = make_context(session_metadata={"segments": self._segments()})
        result = await self._tool().execute_confirmed(ctx, updates=[
            {"segment_id": "seg-1", "content": "a lone hiker in a red parka"},
        ])
        assert result.success is True
        data = json.loads(result.data)
        assert data["action"] == "apply_segment_updates"
        assert data["updates"] == [
            {"segment_id": "seg-1", "segment_index": 0, "content": "a lone hiker in a red parka"},
        ]
        assert data["summary"] == ["Subject: a lone hiker in a red parka"]

    @pytest.mark.asyncio
    async def test_confirmed_revalidates_and_rejects_unknown_segment(self):
        ctx = make_context(session_metadata={"segments": []})
        result = await self._tool().execute_confirmed(ctx, updates=[
            {"segment_id": "seg-1", "content": "x"},
        ])
        assert result.success is False
        assert "No segment matches" in result.error


# ---------------------------------------------------------------------------
# GetFormStateTool
# ---------------------------------------------------------------------------

def _make_form_schema_response(fields_dict):
    """Build a get_form_schema() return value from {name: {type, title, configuration}}."""
    props = {}
    for name, info in fields_dict.items():
        props[name] = {
            "type": info.get("type", "model"),
            "title": info.get("title", name),
        }
        if "configuration" in info:
            props[name]["configuration"] = info["configuration"]
    return {"form_schema": {"properties": props}}


class TestGetFormStateTool:
    def _tool(self):
        return GetFormStateTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "get_form_state"
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["required"] == []
        assert schema["function"]["parameters"]["properties"] == {}

    @pytest.mark.asyncio
    async def test_returns_preset_and_mode(self):
        """With no form_data and no preset_manager, only preset/mode are returned."""
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
        }
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["preset"] == "sdxl-standard"
        assert data["mode"] == "t2i"
        assert "form_data" not in data
        assert "fields" not in data

    @pytest.mark.asyncio
    async def test_returns_fields_with_values_only_when_no_preset_manager(self):
        """Without preset_manager, form_data values are placed in 'fields' with only a 'value' key."""
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
            "form_data": {
                "checkpoint": "models/checkpoints/sdxl.safetensors",
                "steps": 25,
                "cfg": 7.0,
            },
        }
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["preset"] == "sdxl-standard"
        assert data["mode"] == "t2i"
        # No preset_manager → no schema → form_data fields appear under 'fields' with only 'value'
        assert "fields" in data
        assert "form_data" not in data
        assert data["fields"]["steps"]["value"] == 25
        assert data["fields"]["cfg"]["value"] == 7.0
        assert data["fields"]["checkpoint"]["value"] == "models/checkpoints/sdxl.safetensors"
        # No label or type metadata since schema was unavailable
        assert "label" not in data["fields"]["steps"]
        assert "type" not in data["fields"]["steps"]

    @pytest.mark.asyncio
    async def test_merges_schema_with_form_data_when_preset_manager_available(self):
        """With preset_manager that returns schema, returns 'fields' key with merged info."""
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
            "form_data": {
                "checkpoint": "models/checkpoints/sdxl.safetensors",
                "steps": 25,
            },
        }
        pm = MagicMock()
        pm.get_form_schema.return_value = _make_form_schema_response({
            "checkpoint": {
                "type": "model",
                "title": "Checkpoint",
                "configuration": {"model_type": "checkpoint"},
            },
            "steps": {
                "type": "slider",
                "title": "Steps",
            },
        })
        ctx = make_context(preset_manager=pm, session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["preset"] == "sdxl-standard"
        assert data["mode"] == "t2i"
        assert "fields" in data
        assert "form_data" not in data

        checkpoint_field = data["fields"]["checkpoint"]
        assert checkpoint_field["label"] == "Checkpoint"
        assert checkpoint_field["type"] == "model"
        assert checkpoint_field["model_type"] == "checkpoint"
        assert checkpoint_field["value"] == "models/checkpoints/sdxl.safetensors"

        steps_field = data["fields"]["steps"]
        assert steps_field["label"] == "Steps"
        assert steps_field["type"] == "slider"
        assert steps_field["value"] == 25
        assert "model_type" not in steps_field

        pm.get_form_schema.assert_called_once_with("sdxl-standard", mode="t2i")

    @pytest.mark.asyncio
    async def test_media_field_value_compacted_to_path_label_name_type(self):
        """A media-loader field's value (single object or `multiple` array) is
        trimmed to path/label/name/type for the tool caller -- dropping the
        verbose url/relative_path/metadata a form value otherwise carries, so
        an `upsert_media.form_media` addressing lookup has exactly what it
        needs without excess noise."""
        form_state = {
            "preset": "wan-director",
            "mode": "video",
            "form_data": {
                "reference_image": {
                    "path": "uploads/hero.png",
                    "relative_path": "uploads/hero.png",
                    "url": "/api/media/uploads/hero.png",
                    "name": "hero.png",
                    "type": "image",
                    "label": "Hero",
                    "metadata": {"width": 1024, "height": 1024, "size": 12345},
                },
                "gallery": [
                    {
                        "path": "uploads/a.png",
                        "url": "/api/media/uploads/a.png",
                        "name": "a.png",
                        "type": "image",
                        "label": "First",
                        "metadata": {"width": 512, "height": 512},
                    },
                    {
                        "path": "uploads/b.png",
                        "url": "/api/media/uploads/b.png",
                        "name": "b.png",
                        "type": "image",
                    },
                ],
            },
        }
        pm = MagicMock()
        pm.get_form_schema.return_value = _make_form_schema_response({
            "reference_image": {"type": "image", "title": "Reference Image"},
            "gallery": {"type": "image", "title": "Gallery"},
        })
        ctx = make_context(preset_manager=pm, session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["fields"]["reference_image"]["value"] == {
            "path": "uploads/hero.png",
            "label": "Hero",
            "name": "hero.png",
            "type": "image",
        }
        assert data["fields"]["gallery"]["value"] == [
            {"path": "uploads/a.png", "label": "First", "name": "a.png", "type": "image"},
            {"path": "uploads/b.png", "name": "b.png", "type": "image"},
        ]

    @pytest.mark.asyncio
    async def test_non_media_field_value_left_untouched(self):
        """A non-media field's value (even one that happens to be an object)
        is never routed through the media-compaction path."""
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
            "form_data": {"resolution": {"width": 1024, "height": 1024}},
        }
        pm = MagicMock()
        pm.get_form_schema.return_value = _make_form_schema_response({
            "resolution": {"type": "resolution", "title": "Resolution"},
        })
        ctx = make_context(preset_manager=pm, session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        data = json.loads(result.data)
        assert data["fields"]["resolution"]["value"] == {"width": 1024, "height": 1024}

    @pytest.mark.asyncio
    async def test_schema_fields_without_value_are_included_without_value_key(self):
        """Schema fields not present in form_data are included but without a 'value' key."""
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
            "form_data": {"steps": 20},
        }
        pm = MagicMock()
        pm.get_form_schema.return_value = _make_form_schema_response({
            "checkpoint": {"type": "model", "title": "Checkpoint"},
            "steps": {"type": "slider", "title": "Steps"},
        })
        ctx = make_context(preset_manager=pm, session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert "fields" in data
        assert "value" not in data["fields"]["checkpoint"]
        assert data["fields"]["steps"]["value"] == 20

    @pytest.mark.asyncio
    async def test_form_data_fields_not_in_schema_are_still_included(self):
        """Fields in form_data but not in schema still appear in 'fields' with just their value."""
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
            "form_data": {"steps": 20, "unknown_field": "hello"},
        }
        pm = MagicMock()
        pm.get_form_schema.return_value = _make_form_schema_response({
            "steps": {"type": "slider", "title": "Steps"},
        })
        ctx = make_context(preset_manager=pm, session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert "fields" in data
        assert "unknown_field" in data["fields"]
        assert data["fields"]["unknown_field"]["value"] == "hello"

    @pytest.mark.asyncio
    async def test_schema_error_still_returns_fields_with_values(self):
        """If get_form_schema raises, form_data values still appear under 'fields' with only 'value' key."""
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
            "form_data": {"steps": 30},
        }
        pm = MagicMock()
        pm.get_form_schema.side_effect = RuntimeError("schema unavailable")
        ctx = make_context(preset_manager=pm, session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        # Schema failed → no label/type metadata, but values still appear under 'fields'
        assert "fields" in data
        assert data["fields"]["steps"]["value"] == 30
        assert "label" not in data["fields"]["steps"]

    @pytest.mark.asyncio
    async def test_no_form_state_in_metadata(self):
        ctx = make_context(session_metadata={})

        result = await self._tool().execute(ctx)

        assert result.success is False
        assert "No form state available" in result.error

    @pytest.mark.asyncio
    async def test_prompt_variables_rendered_when_present(self):
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
            "variables": [
                {"name": "mood", "type": "choice", "options": ["noir", "sunlit"],
                 "mode": "shuffle", "lastRoll": "sunlit"},
                {"name": "subject", "type": "text", "value": "a fox"},
            ],
        }
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        data = json.loads(result.data)
        assert data["prompt_variables"] == [
            "mood: one of noir, sunlit — shuffles each generation; last roll: sunlit",
            "subject: a fox",
        ]

    @pytest.mark.asyncio
    async def test_prompt_variables_absent_when_no_variables(self):
        form_state = {"preset": "sdxl-standard", "mode": "t2i"}
        ctx = make_context(session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        data = json.loads(result.data)
        assert "prompt_variables" not in data

    @pytest.mark.asyncio
    async def test_description_mentions_model_and_model_path(self):
        tool = self._tool()
        desc = tool.description.lower()
        assert "model" in desc
        assert "modelpath" in desc.replace("_", "").replace(" ", "")


# ---------------------------------------------------------------------------
# GetActiveModelsTool
# ---------------------------------------------------------------------------

def _make_model_repo_with_model(file_path, model_dict):
    """Return a mock model_repo that returns a model for get_by_file_path."""
    mock_model = MagicMock()
    mock_model.to_dict.return_value = model_dict
    repo = MagicMock()
    repo.get_by_file_path.return_value = mock_model
    repo.get_all.return_value = []
    return repo


class TestGetActiveModelsTool:
    def _tool(self):
        return GetActiveModelsTool()

    def test_name_and_schema(self):
        tool = self._tool()
        assert tool.name == "get_active_models"
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["required"] == []
        assert schema["function"]["parameters"]["properties"] == {}

    @pytest.mark.asyncio
    async def test_no_preset_manager_succeeds_without_field_metadata(self):
        """preset_manager is now optional — tool should still work and return results."""
        model_repo = _make_model_repo_with_model("models/checkpoints/sdxl.safetensors", {
            "id": "m-1", "filename": "sdxl.safetensors", "model_type": "checkpoint",
            "description": "", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo
        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"checkpoint": "models/checkpoints/sdxl.safetensors"},
            }},
        )
        result = await self._tool().execute(ctx)
        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        # Without preset_manager, type still resolves from the model record itself
        assert data["models"][0]["type"] == "checkpoint"

    @pytest.mark.asyncio
    async def test_resolves_current_model_reference_by_id(self):
        model_dict = {
            "id": "m-1", "filename": "sdxl.safetensors", "model_type": "checkpoint",
            "description": "SDXL base model", "tags": [], "providers": [],
        }
        mock_model = MagicMock()
        mock_model.to_dict.return_value = model_dict
        model_repo = MagicMock()
        model_repo.get_by_id.return_value = mock_model
        mim = MagicMock(model_repo=model_repo)
        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"checkpoint": "model:m-1"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["models"][0]["id"] == "m-1"
        model_repo.get_by_id.assert_called_once_with(
            "m-1", include_providers=True, include_tags=True
        )

    @pytest.mark.asyncio
    async def test_resolves_nested_lora_rows_and_skips_zero_strength(self):
        def model_for(model_id):
            model = MagicMock()
            model.to_dict.return_value = {
                "id": model_id,
                "filename": f"{model_id}.safetensors",
                "model_type": "lora",
                "description": "",
                "tags": [],
                "providers": [],
            }
            return model

        model_repo = MagicMock()
        model_repo.get_by_id.side_effect = lambda model_id, **_: model_for(model_id)
        mim = MagicMock(model_repo=model_repo)
        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {
                    "loras": [
                        {"model": "model:lora-on", "strength": 0.75},
                        {"model": "model:lora-off", "strength": 0},
                    ]
                },
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["models"][0]["id"] == "lora-on"
        assert data["models"][0]["weight"] == 0.75

    @pytest.mark.asyncio
    async def test_no_model_index_manager(self):
        pm = MagicMock()
        ctx = make_context(preset_manager=pm)
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "Model index manager not available" in result.error

    @pytest.mark.asyncio
    async def test_no_form_state_in_metadata(self):
        mim = MagicMock()
        ctx = make_context(model_index_manager=mim, session_metadata={})
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "No form state available" in result.error

    @pytest.mark.asyncio
    async def test_no_preset_selected_still_scans_form_data(self):
        """preset_id is only used for field metadata — missing preset does not block execution."""
        model_repo = _make_model_repo_with_model("models/checkpoints/sdxl.safetensors", {
            "id": "m-1", "filename": "sdxl.safetensors", "model_type": "checkpoint",
            "description": "", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo
        # Note: no "preset" key in form_state
        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "mode": "t2i",
                "form_data": {"checkpoint": "models/checkpoints/sdxl.safetensors"},
            }},
        )
        result = await self._tool().execute(ctx)
        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["models"][0]["field"] == "checkpoint"

    @pytest.mark.asyncio
    async def test_no_form_data(self):
        mim = MagicMock()
        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {"preset": "p-1", "mode": "t2i"}},
        )
        result = await self._tool().execute(ctx)
        assert result.success is False
        assert "No form data available" in result.error

    @pytest.mark.asyncio
    async def test_no_model_paths_in_form_data(self):
        """form_data with no model paths (no model extensions, no modelPath key) → 0 models."""
        mim = MagicMock()
        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"steps": 25, "cfg": 7.0, "prompt": "a cat"},
            }},
        )
        result = await self._tool().execute(ctx)
        assert result.success is True
        data = json.loads(result.data)
        assert data["models"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_returns_active_model_found_by_file_path(self):
        """Model path in form_data string is detected and looked up via model_repo."""
        model_dict = {
            "id": "m-1",
            "filename": "sdxl.safetensors",
            "model_type": "checkpoint",
            "description": "SDXL base model",
            "tags": [{"name": "sdxl"}, {"name": "base"}],
            "providers": [{"name": "Stability AI", "description": "Official SDXL"}],
        }
        model_repo = _make_model_repo_with_model("models/checkpoints/sdxl.safetensors", model_dict)
        mim = MagicMock()
        mim.model_repo = model_repo

        # Optional: preset_manager for field metadata
        pm = MagicMock()
        pm.get_form_schema.return_value = _make_form_schema_response({
            "checkpoint": {
                "type": "model",
                "title": "Checkpoint",
                "configuration": {"model_type": "checkpoint"},
            },
        })

        ctx = make_context(
            preset_manager=pm,
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"checkpoint": "models/checkpoints/sdxl.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        entry = data["models"][0]
        # Compact survey entry : field/id/name/type(+hint)
        # only. The nested full model dict — description, tags, provider — is
        # gone; fetch it per model via get_model_info when actually needed.
        assert entry["field"] == "checkpoint"
        assert entry["type"] == "checkpoint"
        assert entry["id"] == "m-1"
        assert entry["name"] == "sdxl.safetensors"
        assert "model_details" not in entry
        assert "model_path" not in entry
        assert "description" not in entry
        assert "tags" not in entry
        assert "provider_info" not in entry

    @pytest.mark.asyncio
    async def test_falls_back_to_filename_search_when_path_not_found(self):
        model_dict = {
            "id": "m-2",
            "filename": "flux.safetensors",
            "model_type": "checkpoint",
            "description": "FLUX model",
            "tags": [],
            "providers": [],
        }
        mock_model = MagicMock()
        mock_model.to_dict.return_value = model_dict

        model_repo = MagicMock()
        model_repo.get_by_file_path.return_value = None  # exact path not found
        model_repo.get_all.return_value = [mock_model]

        mim = MagicMock()
        mim.model_repo = model_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"checkpoint": "models/checkpoints/flux.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["models"][0]["id"] == "m-2"

    @pytest.mark.asyncio
    async def test_model_not_found_returns_placeholder(self):
        model_repo = MagicMock()
        model_repo.get_by_file_path.return_value = None
        model_repo.get_all.return_value = []

        mim = MagicMock()
        mim.model_repo = model_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"checkpoint": "models/checkpoints/unknown.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        entry = data["models"][0]
        # lookup_model's not-found placeholder has no id/filename — its
        # (empty) id flows straight into `id`, and `name` falls back to the
        # locator's path tail.
        assert entry["id"] == ""
        assert entry["name"] == "unknown.safetensors"
        assert entry["type"] == "unknown"

    @pytest.mark.asyncio
    async def test_skips_fields_with_no_model_extension(self):
        """form_data values that don't look like model paths are skipped."""
        model_repo = _make_model_repo_with_model("models/checkpoints/sdxl.safetensors", {
            "id": "m-1", "filename": "sdxl.safetensors", "model_type": "checkpoint",
            "description": "", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                # lora is absent — only checkpoint has a model path
                "form_data": {
                    "checkpoint": "models/checkpoints/sdxl.safetensors",
                    "lora": "",          # empty string → not a model path
                    "steps": 25,         # integer → not a model path
                },
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["models"][0]["field"] == "checkpoint"

    @pytest.mark.asyncio
    async def test_handles_model_path_as_dict_with_model_path_key(self):
        model_repo = _make_model_repo_with_model("models/checkpoints/sdxl.safetensors", {
            "id": "m-1", "filename": "sdxl.safetensors", "model_type": "checkpoint",
            "description": "", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {
                    "checkpoint": {"modelPath": "models/checkpoints/sdxl.safetensors", "extra": "data"}
                },
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        # model_path is no longer surfaced in the compact entry; confirm the
        # dict-wrapped locator still resolved to the right model via id/name.
        assert data["models"][0]["id"] == "m-1"
        assert data["models"][0]["name"] == "sdxl.safetensors"

    @pytest.mark.asyncio
    async def test_detects_model_path_in_form_data_without_schema(self):
        """Model paths in form_data are detected by extension regardless of schema presence."""
        model_repo = _make_model_repo_with_model("models/vae/sdxl_vae.safetensors", {
            "id": "vae-1", "filename": "sdxl_vae.safetensors", "model_type": "vae",
            "description": "SDXL VAE", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        # No preset_manager at all — tool still detects the model path
        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"vae": "models/vae/sdxl_vae.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["models"][0]["field"] == "vae"
        # No schema needed — the model record's own type wins, fixing the old
        # "unknown" bug for callers that never wired field metadata.
        assert data["models"][0]["type"] == "vae"
        assert data["models"][0]["type_hint"].startswith("The VAE converts")

    @pytest.mark.asyncio
    async def test_type_from_model_record_wins_over_schema_meta(self):
        """When the model record and the form schema disagree, the model
        record's own type is authoritative ."""
        model_repo = _make_model_repo_with_model("models/loras/style.safetensors", {
            "id": "lora-1", "filename": "style.safetensors", "model_type": "lora",
            "description": "", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        pm = MagicMock()
        pm.get_form_schema.return_value = _make_form_schema_response({
            "lora": {
                "type": "model",
                "title": "LoRA",
                # Deliberately wrong/stale schema metadata
                "configuration": {"model_type": "checkpoint"},
            },
        })

        ctx = make_context(
            preset_manager=pm,
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"lora": "models/loras/style.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["models"][0]["type"] == "lora"

    @pytest.mark.asyncio
    async def test_schema_model_type_is_fallback_when_model_record_has_no_type(self):
        """Schema model_type is only used when the model record has none — and
        field_label is no longer emitted at all (compact entries dropped
        field_name/field_label)."""
        model_repo = _make_model_repo_with_model("models/vae/sdxl_vae.safetensors", {
            "id": "vae-1", "filename": "sdxl_vae.safetensors", "model_type": "",
            "description": "SDXL VAE", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        pm = MagicMock()
        pm.get_form_schema.return_value = _make_form_schema_response({
            "vae": {
                "type": "model",
                "title": "VAE Model",
                "configuration": {"model_type": "vae"},
            },
        })

        ctx = make_context(
            preset_manager=pm,
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"vae": "models/vae/sdxl_vae.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        entry = data["models"][0]
        assert entry["field"] == "vae"
        assert entry["type"] == "vae"
        assert "field_label" not in entry
        assert "field_name" not in entry

    @pytest.mark.asyncio
    async def test_type_hint_present_for_known_type_absent_for_unknown(self):
        """type_hint is only attached for types in MODEL_TYPE_HINTS."""
        lora_repo = _make_model_repo_with_model("models/loras/style.safetensors", {
            "id": "lora-1", "filename": "style.safetensors", "model_type": "lora",
            "description": "", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = lora_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"lora": "models/loras/style.safetensors"},
            }},
        )
        result = await self._tool().execute(ctx)
        data = json.loads(result.data)
        assert "type_hint" in data["models"][0]

        odd_repo = _make_model_repo_with_model("models/misc/thing.safetensors", {
            "id": "m-odd", "filename": "thing.safetensors", "model_type": "some_new_type",
            "description": "", "tags": [], "providers": [],
        })
        mim2 = MagicMock()
        mim2.model_repo = odd_repo
        ctx2 = make_context(
            model_index_manager=mim2,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"misc": "models/misc/thing.safetensors"},
            }},
        )
        result2 = await self._tool().execute(ctx2)
        data2 = json.loads(result2.data)
        assert "type_hint" not in data2["models"][0]

    @pytest.mark.asyncio
    async def test_trigger_words_and_prompting_guidance_surface_in_survey_entry(self):
        """A LoRA's trigger words and prompting guidance must reach the compact
        survey entry directly — the LLM should not have to call get_model_info
        just to learn how to prompt an active LoRA."""
        model_repo = _make_model_repo_with_model("models/loras/style.safetensors", {
            "id": "lora-1", "filename": "style.safetensors", "model_type": "lora",
            "description": "", "tags": [], "providers": [],
            "triggers": ["sty1e", "stylized art"],
            "prompting_guidance": "Lead with the trigger words.",
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"lora": "models/loras/style.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        entry = data["models"][0]
        assert entry["trigger_words"] == ["sty1e", "stylized art"]
        assert entry["prompting_guidance"] == "Lead with the trigger words."

    @pytest.mark.asyncio
    async def test_exception_from_model_repo_returns_error(self):
        """A hard exception during model lookup propagates as a tool error."""
        model_repo = MagicMock()
        model_repo.get_by_file_path.side_effect = RuntimeError("db error")
        model_repo.get_all.side_effect = RuntimeError("db error")
        mim = MagicMock()
        mim.model_repo = model_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-bad",
                "mode": "t2i",
                "form_data": {"checkpoint": "some/path.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        # The lookup errors are caught internally and a placeholder is returned,
        # but the tool still succeeds with a not-found placeholder.
        # The overall execute() should not fail unless there is an outer exception.
        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        entry = data["models"][0]
        assert entry["id"] == ""
        assert entry["name"] == "path.safetensors"

    @pytest.mark.asyncio
    async def test_ai_hint_included_from_schema(self):
        """When schema has ai_hint, it should appear in the active model entry."""
        model_repo = _make_model_repo_with_model("models/checkpoints/sdxl.safetensors", {
            "id": "m-1", "filename": "sdxl.safetensors", "model_type": "checkpoint",
            "description": "SDXL base", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        pm = MagicMock()
        props = {
            "checkpoint": {
                "type": "model",
                "title": "Checkpoint",
                "configuration": {"model_type": "checkpoint"},
                "ai_hint": "Use a high-quality checkpoint for best results",
            }
        }
        pm.get_form_schema.return_value = {"form_schema": {"properties": props}}

        ctx = make_context(
            preset_manager=pm,
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"checkpoint": "models/checkpoints/sdxl.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        entry = data["models"][0]
        assert entry["ai_hint"] == "Use a high-quality checkpoint for best results"

    @pytest.mark.asyncio
    async def test_weight_included_when_strength_field_present(self):
        """When a companion _strength field exists in form_data, weight should be included."""
        model_repo = _make_model_repo_with_model("models/loras/style.safetensors", {
            "id": "lora-1", "filename": "style.safetensors", "model_type": "lora",
            "description": "Style LoRA", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {
                    "lora": "models/loras/style.safetensors",
                    "lora_strength": 0.75,
                },
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["count"] == 1
        assert data["models"][0]["weight"] == 0.75

    @pytest.mark.asyncio
    async def test_weight_included_when_weight_field_present(self):
        """When a companion _weight field exists in form_data, weight should be included."""
        model_repo = _make_model_repo_with_model("models/loras/detail.safetensors", {
            "id": "lora-2", "filename": "detail.safetensors", "model_type": "lora",
            "description": "Detail LoRA", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {
                    "lora": "models/loras/detail.safetensors",
                    "lora_weight": 0.5,
                },
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert data["models"][0]["weight"] == 0.5

    @pytest.mark.asyncio
    async def test_weight_not_included_when_no_companion_field(self):
        """When no companion weight field, weight key should not appear in entry."""
        model_repo = _make_model_repo_with_model("models/checkpoints/sdxl.safetensors", {
            "id": "m-1", "filename": "sdxl.safetensors", "model_type": "checkpoint",
            "description": "", "tags": [], "providers": [],
        })
        mim = MagicMock()
        mim.model_repo = model_repo

        ctx = make_context(
            model_index_manager=mim,
            session_metadata={"form_state": {
                "preset": "p-1",
                "mode": "t2i",
                "form_data": {"checkpoint": "models/checkpoints/sdxl.safetensors"},
            }},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert "weight" not in data["models"][0]

    def test_model_to_dict_includes_triggers_and_guidance(self):
        """get_active_models (and anything else via model_to_dict) must carry
        trigger words + prompting guidance."""
        d = {
            "id": "m-lora",
            "filename": "style.safetensors",
            "model_type": "lora",
            "description": "",
            "tags": [],
            "triggers": ["sty1e", "stylized"],
            "prompting_guidance": "Open with the trigger words.",
        }
        from src.features.llm.tools.builtin.utils import model_to_dict
        result = model_to_dict(d)
        assert result["trigger_words"] == ["sty1e", "stylized"]
        assert result["prompting_guidance"] == "Open with the trigger words."

    def test_model_to_dict_omits_empty_triggers_and_guidance(self):
        d = {"id": "m-x", "filename": "f.safetensors", "model_type": "checkpoint",
             "description": "", "tags": [], "triggers": [], "prompting_guidance": None}
        from src.features.llm.tools.builtin.utils import model_to_dict
        result = model_to_dict(d)
        assert "trigger_words" not in result
        assert "prompting_guidance" not in result

    def test_combined_description_with_model_and_provider(self):
        """model_to_dict should build combined_description from model + provider descriptions."""
        d = {
            "id": "m-1",
            "filename": "sdxl.safetensors",
            "model_type": "checkpoint",
            "description": "SDXL base model",
            "tags": [],
            "providers": [{"name": "Stability AI", "description": "Official SDXL by Stability"}],
        }
        from src.features.llm.tools.builtin.utils import model_to_dict
        result = model_to_dict(d)
        assert "combined_description" in result
        assert "SDXL base model" in result["combined_description"]
        assert "Official SDXL by Stability" in result["combined_description"]
        assert " | " in result["combined_description"]

    def test_combined_description_only_model_when_no_provider(self):
        """combined_description should only contain model description when no provider."""
        d = {
            "id": "m-2",
            "filename": "sdxl.safetensors",
            "model_type": "checkpoint",
            "description": "Only model description",
            "tags": [],
            "providers": [],
        }
        from src.features.llm.tools.builtin.utils import model_to_dict
        result = model_to_dict(d)
        assert result["combined_description"] == "Only model description"

    def test_combined_description_absent_when_no_descriptions(self):
        """combined_description should not be present when both descriptions are empty."""
        d = {
            "id": "m-3",
            "filename": "sdxl.safetensors",
            "model_type": "checkpoint",
            "description": "",
            "tags": [],
            "providers": [],
        }
        from src.features.llm.tools.builtin.utils import model_to_dict
        result = model_to_dict(d)
        assert "combined_description" not in result


# ---------------------------------------------------------------------------
# GetFormStateTool - ai_hint tests
# ---------------------------------------------------------------------------

class TestGetFormStateToolAiHint:
    def _tool(self):
        return GetFormStateTool()

    @pytest.mark.asyncio
    async def test_ai_hint_included_in_field_when_present(self):
        """When schema has ai_hint, it should appear in the merged field entry."""
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
            "form_data": {"checkpoint": "models/checkpoints/sdxl.safetensors"},
        }
        props = {
            "checkpoint": {
                "type": "model",
                "title": "Checkpoint",
                "configuration": {"model_type": "checkpoint"},
                "ai_hint": "Primary generation model - quality determines output quality",
            }
        }
        pm = MagicMock()
        pm.get_form_schema.return_value = {"form_schema": {"properties": props}}
        ctx = make_context(preset_manager=pm, session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        field = data["fields"]["checkpoint"]
        assert field["ai_hint"] == "Primary generation model - quality determines output quality"

    @pytest.mark.asyncio
    async def test_ai_hint_not_included_when_absent(self):
        """When schema has no ai_hint, the key should not appear in the field entry."""
        form_state = {
            "preset": "sdxl-standard",
            "mode": "t2i",
            "form_data": {"steps": 25},
        }
        pm = MagicMock()
        pm.get_form_schema.return_value = _make_form_schema_response({
            "steps": {"type": "slider", "title": "Steps"},
        })
        ctx = make_context(preset_manager=pm, session_metadata={"form_state": form_state})

        result = await self._tool().execute(ctx)

        assert result.success is True
        data = json.loads(result.data)
        assert "ai_hint" not in data["fields"]["steps"]
