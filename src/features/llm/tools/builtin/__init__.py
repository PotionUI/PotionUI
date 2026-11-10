"""Built-in LLM tools for accessing application data."""

import logging

from src.features.llm.tools.builtin.segments_tool import (
    CreateSavedSegmentTool,
    CreateSegmentCategoryTool,
    CreateSegmentTemplateTool,
    DeleteSavedSegmentTool,
    DeleteSegmentCategoryTool,
    DeleteSegmentTemplateTool,
    GetSavedSegmentsTool,
    GetSegmentTemplatesTool,
    ListSegmentCategoriesTool,
    UpdateSavedSegmentTool,
    UpdateSegmentCategoryTool,
    UpdateSegmentTemplateTool,
)
from src.features.llm.tools.builtin.model_info_tool import GetModelInfoTool
from src.features.llm.tools.builtin.preset_info_tool import GetPresetInfoTool, ListPresetsTool
from src.features.llm.tools.builtin.phrasebook_tool import (
    ListPhrasebookCategoriesTool,
    GetPhrasebookValuesTool,
    CreatePhrasebookCategoryTool,
    UpdatePhrasebookCategoryTool,
    DeletePhrasebookCategoryTool,
    CreatePhrasebookValuesTool,
    RemovePhrasebookValuesTool,
    UpdatePhrasebookValuesTool,
)
from src.features.llm.tools.builtin.enhance_prompt_tool import EnhancePromptTool
from src.features.llm.tools.builtin.form_context_tool import GetCurrentSegmentsTool, GetFormStateTool
from src.features.llm.tools.builtin.active_models_tool import GetActiveModelsTool
from src.features.llm.tools.builtin.update_form_settings_tool import UpdateFormSettingsTool
from src.features.llm.tools.builtin.prompt_variables_tool import (
    GetPromptVariablesTool,
    ListTabDraftsTool,
    ManagePromptVariablesTool,
)
from src.features.llm.tools.builtin.condition_tool import WriteConditionTool
from src.features.llm.tools.builtin.search_prompts_tool import SearchModelPromptsTool
from src.features.llm.tools.builtin.search_gallery_tool import SearchGalleryTool
from src.features.llm.tools.builtin.manage_prompts_tool import (
    AddPromptTool, DeletePromptTool, EditPromptTool, GetPromptTool, ListPromptsTool,
)
from src.features.llm.tools.builtin.run_generation_tool import RunGenerationTool
from src.features.llm.tools.builtin.list_models_tool import ListModelsTool, SearchModelsTool
from src.features.llm.tools.builtin.library_items_tool import ListLibraryItemsTool
from src.features.llm.tools.builtin.memory_tool import (
    WriteMemoryTool,
    ReadMemoryTool,
    UpdateMemoryTool,
    DeleteMemoryTool,
)
from src.features.llm.tools.builtin.prompt_relay_tool import SetPromptRelayTimelineTool
from src.features.llm.tools.builtin.regional_prompt_tool import SetRegionalPromptTool
from src.features.llm.tools.builtin.video_director_tool import GetVideoDirectorTool
from src.features.llm.tools.builtin.music_director_tool import GetMusicDirectorTool, UpdateMusicDirectorTool
from src.features.llm.tools.builtin.manage_collections_tool import ManageCollectionsTool
from src.features.llm.tools.builtin.organize_gallery_tool import OrganizeGalleryTool
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.llm.tools.builtin.run_media_tool_tool import RunMediaToolTool
from src.features.llm.tools.builtin.elixir_tools import ELIXIR_TOOLS
from src.platform.media_tools import MediaToolRegistry
from src.platform.media_tools.ops import register_core_tools as register_core_media_tools

logger = logging.getLogger(__name__)


def register_builtin_tools(registry, media_tool_registry=None) -> None:
    """Register all built-in tools with the given registry."""
    if media_tool_registry is None:
        media_tool_registry = MediaToolRegistry()
        register_core_media_tools(media_tool_registry)
    tools = [
        ListSegmentCategoriesTool(),
        GetSavedSegmentsTool(),
        GetSegmentTemplatesTool(),
        CreateSegmentCategoryTool(),
        UpdateSegmentCategoryTool(),
        DeleteSegmentCategoryTool(),
        CreateSavedSegmentTool(),
        UpdateSavedSegmentTool(),
        DeleteSavedSegmentTool(),
        CreateSegmentTemplateTool(),
        UpdateSegmentTemplateTool(),
        DeleteSegmentTemplateTool(),
        GetModelInfoTool(),
        GetPresetInfoTool(),
        ListPresetsTool(),
        ListPhrasebookCategoriesTool(),
        GetPhrasebookValuesTool(),
        CreatePhrasebookCategoryTool(),
        UpdatePhrasebookCategoryTool(),
        DeletePhrasebookCategoryTool(),
        CreatePhrasebookValuesTool(),
        RemovePhrasebookValuesTool(),
        UpdatePhrasebookValuesTool(),
        EnhancePromptTool(),
        GetCurrentSegmentsTool(),
        GetFormStateTool(),
        GetActiveModelsTool(),
        UpdateFormSettingsTool(),
        GetPromptVariablesTool(),
        ManagePromptVariablesTool(),
        ListTabDraftsTool(),
        WriteConditionTool(),
        SearchModelPromptsTool(),
        SearchGalleryTool(),
        GetPromptTool(),
        ListPromptsTool(),
        AddPromptTool(),
        EditPromptTool(),
        DeletePromptTool(),
        RunGenerationTool(),
        ListModelsTool(),
        SearchModelsTool(),
        WriteMemoryTool(),
        ReadMemoryTool(),
        UpdateMemoryTool(),
        DeleteMemoryTool(),
        SetPromptRelayTimelineTool(),
        SetRegionalPromptTool(),
        GetVideoDirectorTool(),
        GetMusicDirectorTool(),
        UpdateMusicDirectorTool(),
        ManageCollectionsTool(),
        ListLibraryItemsTool(),
        OrganizeGalleryTool(),
        StartGenerationTool(),
        RunMediaToolTool(media_tool_registry),
        *(tool() for tool in ELIXIR_TOOLS),
    ]
    for tool in tools:
        registry.register(tool)
    logger.info(f"Registered {len(tools)} builtin tools: {[t.name for t in tools]}")
