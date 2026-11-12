import json
from typing import Any, Dict, Optional

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.sessions.generate import SessionRequestError


class ListSessionsTool(BaseTool):
    modes = ["generation"]
    icon = "list"
    context_group = "library"

    name = "list_sessions"
    mcp = True
    read_only = True
    group = "Generation"
    user_description = "Lists the settings the user saved in the web UI, and what each one lets a script change."

    def is_available(self, form_state: Optional[Dict[str, Any]]) -> bool:
        return not form_state

    @property
    def description(self) -> str:
        return (
            "List the sessions (saved settings) the user configured in the web UI, newest first. Each has an id, "
            "a name, its preset and the modes it holds. Pass 'session_id' (and 'mode' when it holds several) to "
            "see exactly what start_generation can change for it: the prompt, prompt variables, form fields with "
            "their current values, and any problems. Then call start_generation with that session_id and only "
            "what should change."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "preset_id": {"type": "string", "description": "Only sessions of this preset."},
                "query": {"type": "string", "description": "Only sessions whose name contains this text."},
                "session_id": {"type": "string", "description": "Describe this one session instead of listing."},
                "mode": {"type": "string", "description": "With session_id: the mode to describe, when the session holds several."},
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if context.sessions is None:
            return ToolResult(success=False, data="", error="Saved sessions are not available here.")
        try:
            if kwargs.get("session_id"):
                described = context.sessions.describe(context.user_id, context.is_admin, kwargs["session_id"], kwargs.get("mode"))
                described.pop("resolved_request", None)
                return ToolResult(success=True, data=json.dumps(described))
            listed = context.sessions.listing(context.user_id, kwargs.get("preset_id"), None, kwargs.get("query"))
        except SessionRequestError as exc:
            return ToolResult(success=False, data="", error=exc.message)
        return ToolResult(success=True, data=json.dumps({"sessions": listed}))
