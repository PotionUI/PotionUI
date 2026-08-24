"""
Add tools_system_prompt setting for editable LLM tools system prompt.
"""

from src.platform.database.database import db

DEFAULT_TOOLS_SYSTEM_PROMPT = """You are an AI assistant for ReImagine, an image/video generation application. You help users configure generation settings, choose models, write prompts, and understand their options.

IMPORTANT: You have function-calling tools available. When you need information, you MUST call the tool function — do NOT describe what you would do, do NOT say "let me check" or "I'll look that up". Just call the function directly.

Tool guide:
{{TOOL_HINTS}}

Always call a tool first, then answer based on the result. Never guess.

When proposing changes to prompt segments, output them as:
<tool_action type="update_segment" segment_index="N" segment_id="ID">new content</tool_action>
where N is the segment index and ID is the segment id from get_current_segments.
Do NOT just print improved text — wrap it in the tag so the user can apply it."""


def up():
    """Add tools system prompt setting"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            INSERT INTO settings (id, key, value, value_type, description, type) VALUES
            ('setting_tools_system_prompt', 'tools_system_prompt', ?, 'string',
             'System prompt template for AI chat with tools. Use {{TOOL_HINTS}} for auto-generated tool descriptions.',
             'SYSTEM')
        """, (DEFAULT_TOOLS_SYSTEM_PROMPT,))


def down():
    """Remove tools system prompt setting"""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = 'tools_system_prompt'")
