"""Built-in LLM tools for accessing application data."""

import logging

from src.features.llm.tools.builtin.segments_tool import (
    GetSavedSegmentsTool,
    GetSegmentTemplatesTool,
    ListSegmentCategoriesTool,
)
from src.features.llm.tools.builtin.model_info_tool import GetModelInfoTool
from src.features.llm.tools.builtin.preset_info_tool import GetPresetInfoTool
from src.features.llm.tools.builtin.phrasebook_tool import (
    ListPhrasebookCategoriesTool,
    GetPhrasebookValuesTool,
    ListPhrasebookValuesTool,
    CreatePhrasebookCategoryTool,
    CreatePhrasebookValuesTool,
    RemovePhrasebookValuesTool,
    UpdatePhrasebookValuesTool,
)
from src.features.llm.tools.builtin.enhance_prompt_tool import EnhancePromptTool
from src.features.llm.tools.builtin.form_context_tool import (
    GetCurrentSegmentsTool,
    GetFormStateTool,
    UpdateSegmentTool,
)
from src.features.llm.tools.builtin.active_models_tool import GetActiveModelsTool
from src.features.llm.tools.builtin.update_form_settings_tool import UpdateFormSettingsTool
from src.features.llm.tools.builtin.search_prompts_tool import SearchModelPromptsTool
from src.features.llm.tools.builtin.search_gallery_tool import SearchGalleryTool
from src.features.llm.tools.builtin.manage_prompts_tool import AddPromptTool, EditPromptTool, DeletePromptTool
from src.features.llm.tools.builtin.run_generation_tool import RunGenerationTool
from src.features.llm.tools.builtin.list_models_tool import ListModelsTool
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
        GetModelInfoTool(),
        GetPresetInfoTool(),
        ListPhrasebookCategoriesTool(),
        GetPhrasebookValuesTool(),
        ListPhrasebookValuesTool(),
        CreatePhrasebookCategoryTool(),
        CreatePhrasebookValuesTool(),
        RemovePhrasebookValuesTool(),
        UpdatePhrasebookValuesTool(),
        EnhancePromptTool(),
        GetCurrentSegmentsTool(),
        UpdateSegmentTool(),
        GetFormStateTool(),
        GetActiveModelsTool(),
        UpdateFormSettingsTool(),
        SearchModelPromptsTool(),
        SearchGalleryTool(),
        AddPromptTool(),
        EditPromptTool(),
        DeletePromptTool(),
        RunGenerationTool(),
        ListModelsTool(),
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
