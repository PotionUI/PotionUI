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
    ListPhrasebookValuesTool,
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
from src.features.llm.tools.builtin.prompt_variables_tool import ManagePromptVariablesTool
from src.features.llm.tools.builtin.search_prompts_tool import SearchModelPromptsTool
from src.features.llm.tools.builtin.search_gallery_tool import SearchGalleryTool
from src.features.llm.tools.builtin.manage_prompts_tool import (
    AddPromptTool, DeletePromptTool, EditPromptTool, GetPromptTool, ListPromptsTool,
)
from src.features.llm.tools.builtin.run_generation_tool import RunGenerationTool
from src.features.llm.tools.builtin.list_models_tool import ListModelsTool, SearchModelsTool
from src.features.llm.tools.builtin.memory_tool import (
    WriteMemoryTool,
    ReadMemoryTool,
    UpdateMemoryTool,
    DeleteMemoryTool,
)
from src.features.llm.tools.builtin.prompt_relay_tool import SetPromptRelayTimelineTool
from src.features.llm.tools.builtin.video_director_tool import GetVideoDirectorTool
from src.features.llm.tools.builtin.music_director_tool import GetMusicDirectorTool, UpdateMusicDirectorTool
from src.features.llm.tools.builtin.manage_collections_tool import ManageCollectionsTool
from src.features.llm.tools.builtin.organize_gallery_tool import OrganizeGalleryTool
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool

logger = logging.getLogger(__name__)


def register_builtin_tools(registry) -> None:
    """Register all built-in tools with the given registry."""
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
        ListPhrasebookValuesTool(),
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
        ManagePromptVariablesTool(),
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
        GetVideoDirectorTool(),
        GetMusicDirectorTool(),
        UpdateMusicDirectorTool(),
        ManageCollectionsTool(),
        OrganizeGalleryTool(),
        StartGenerationTool(),
    ]
    for tool in tools:
        registry.register(tool)
    logger.info(f"Registered {len(tools)} builtin tools: {[t.name for t in tools]}")
