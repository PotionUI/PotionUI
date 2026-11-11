from src.platform.database.database import db
from src.platform.database.rows import now_iso

_NAME = "096_mcp_tool_seed"

_WRITE_TOOLS = (
    "create_segment_category", "update_segment_category", "delete_segment_category",
    "create_saved_segment", "update_saved_segment", "delete_saved_segment",
    "create_segment_template", "update_segment_template", "delete_segment_template",
    "create_phrasebook_category", "update_phrasebook_category", "delete_phrasebook_category",
    "create_phrasebook_values", "remove_phrasebook_values", "update_phrasebook_values",
    "add_prompt", "edit_prompt", "delete_prompt",
    "write_memory", "update_memory", "delete_memory",
    "manage_collections", "organize_gallery", "start_generation", "run_media_tool",
    "manage_prompt_variables",
)

_READ_TOOLS = (
    "list_segment_categories", "get_saved_segments", "get_segment_templates",
    "get_model_info", "get_preset_info", "list_presets",
    "list_phrasebook_categories", "get_phrasebook_values",
    "enhance_prompt", "search_model_prompts", "search_gallery",
    "get_prompt", "list_prompts", "search_models", "list_library_items", "read_memory",
    "get_prompt_variables", "list_tab_drafts", "write_condition",
)

_SURFACE = _WRITE_TOOLS + _READ_TOOLS

_ACTIVE_OWNERS = "SELECT DISTINCT user_id FROM api_keys WHERE revoked_at IS NULL"


def _marks(values):
    return ", ".join("?" for _ in values)


def _default_configuration(cursor):
    cursor.execute("SELECT value FROM settings WHERE key = 'llm_default_provider'")
    row = cursor.fetchone()
    value = (row[0] or "").strip() if row else ""
    return value or None


def up():
    now = now_iso()
    with db.get_cursor() as cursor:
        config_id = _default_configuration(cursor)

        cursor.execute(
            "INSERT OR IGNORE INTO mcp_tool_ceiling (tool_name, enabled, updated_at, updated_by) "
            "SELECT tool_name, 0, ?, NULL FROM tool_governance WHERE llm_config_id = ? AND enabled = 0",
            (now, config_id),
        )
        ceiling = cursor.rowcount

        cursor.execute(
            "INSERT OR IGNORE INTO mcp_tool_defaults (user_id, tool_name, enabled, updated_at) "
            f"SELECT d.user_id, d.tool_name, 0, ? FROM user_disabled_tools d "
            f"WHERE d.tool_name IN ({_marks(_SURFACE)}) "
            f"AND d.user_id IN ({_ACTIVE_OWNERS}) "
            "AND NOT EXISTS (SELECT 1 FROM tool_governance g WHERE g.llm_config_id = ? "
            "AND g.tool_name = d.tool_name AND g.enabled = 1 AND g.locked = 1)",
            (now, *_SURFACE, config_id),
        )
        opted_out = cursor.rowcount

        for tool in _WRITE_TOOLS:
            cursor.execute(
                "INSERT OR IGNORE INTO mcp_tool_defaults (user_id, tool_name, enabled, updated_at) "
                f"SELECT user_id, ?, 1, ? FROM ({_ACTIVE_OWNERS})",
                (tool, now),
            )
    print(
        f"Migration {_NAME}: {ceiling} tools the administrator had turned off in chat now stay off for MCP, "
        f"{opted_out} per-user chat opt-outs carried over, write tools kept on for users with keys"
    )
