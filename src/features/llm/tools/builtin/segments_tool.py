"""Segment tools for accessing user's prompt segments."""

import json
import logging
from typing import Any, Dict

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class ListSegmentCategoriesTool(BaseTool):
    """Lists all segment categories available to the user."""

    modes = ["generation"]
    icon = "layers"

    @property
    def name(self) -> str:
        return "list_segment_categories"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Lists the categories of your saved segment library."

    @property
    def hint(self) -> str:
        return (
            "When helping the user build or organize their prompt, call this "
            "to see what reusable segment categories exist."
        )

    @property
    def description(self) -> str:
        return (
            "List all segment categories available to the user. "
            "Segments are reusable prompt building blocks organized by category "
            "(e.g., 'Character', 'Environment', 'Style'). "
            "Use this to discover what prompt segments the user has created."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.segment_manager:
            return ToolResult(success=False, data="", error="Segment manager not available")

        try:
            categories = context.segment_manager.get_categories(user_id=context.user_id)
            result = []
            for cat in categories:
                result.append({
                    "id": cat.id,
                    "name": cat.name,
                    "description": getattr(cat, 'description', ''),
                    "color": getattr(cat, 'color', ''),
                })
            return ToolResult(
                success=True,
                data=json.dumps({"categories": result, "count": len(result)}),
            )
        except Exception as e:
            logger.error(f"Error listing segment categories: {e}")
            return ToolResult(success=False, data="", error=str(e))


class GetSavedSegmentsTool(BaseTool):
    """Gets reusable single saved Segments, optionally by category."""

    modes = ["generation"]
    icon = "layers"

    @property
    def name(self) -> str:
        return "get_saved_segments"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Fetches reusable prompt segments from your library."

    @property
    def hint(self) -> str:
        return "Use when the user wants one reusable prompt card from their categorized library."

    @property
    def description(self) -> str:
        return (
            "Get saved Segments: named, categorized single rich prompt cards. "
            "These are distinct from multi-segment Templates."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category_id": {
                    "type": "string",
                    "description": "Optional Segment Category ID.",
                }
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.segment_manager:
            return ToolResult(success=False, data="", error="Segment manager not available")
        try:
            segments = context.segment_manager.get_segments(
                user_id=context.user_id, category_id=kwargs.get("category_id")
            )
            return ToolResult(success=True, data=json.dumps({
                "segments": [item.model_dump(mode="json") for item in segments],
                "count": len(segments),
            }))
        except Exception as e:
            logger.error("Error getting saved Segments: %s", e)
            return ToolResult(success=False, data="", error=str(e))


class GetSegmentTemplatesTool(BaseTool):
    """Gets ordered multi-segment Templates."""

    modes = ["generation"]
    icon = "layout-template"

    @property
    def name(self) -> str:
        return "get_segment_templates"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Fetches your reusable multi-segment prompt templates."

    @property
    def hint(self) -> str:
        return (
            "When helping build prompts, use this to find reusable templates "
            "the user has created. Good for jumpstarting prompt construction."
        )

    @property
    def description(self) -> str:
        return (
            "Get Segment Templates. Each Template contains an ordered array of "
            "one or more rich segment slots and is not assigned to a Segment Category."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.segment_manager:
            return ToolResult(success=False, data="", error="Segment manager not available")

        try:
            templates = context.segment_manager.get_templates(user_id=context.user_id)
            result = [tmpl.model_dump(mode="json") for tmpl in templates]
            return ToolResult(
                success=True,
                data=json.dumps({"templates": result, "count": len(result)}),
            )
        except Exception as e:
            logger.error(f"Error getting segment templates: {e}")
            return ToolResult(success=False, data="", error=str(e))
