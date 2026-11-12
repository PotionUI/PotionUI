import dataclasses

from src.features.llm.tools.base import ToolContext

V010_LEADING_FIELDS = (
    "user_id", "mode_id", "is_admin", "session_metadata", "segment_category_repository",
    "saved_segment_repository", "segment_template_repository", "model_index_manager",
    "preset_collaborators", "phrasebook_category_repository", "phrasebook_value_repository",
    "llm_repository", "prompt_database", "generation_orchestrator", "llm_memory_repository",
    "prompt_enhancement_manager", "media_indexer", "settings", "llm_id", "collection_repository",
    "tag_repository", "plugin_registry", "generation_history_facade", "content_safety",
    "library_collaborators", "tab_drafts", "media_tool_service", "elixirs", "chat_session",
)


def test_the_v010_positional_order_is_kept_and_new_fields_come_after_it():
    names = tuple(field.name for field in dataclasses.fields(ToolContext))

    assert names[: len(V010_LEADING_FIELDS)] == V010_LEADING_FIELDS
    assert names[len(V010_LEADING_FIELDS):] == ("acts_as_admin",)
