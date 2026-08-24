"""Preset info tool for accessing preset configuration."""

import json
import logging
from typing import Any, Dict

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class GetPresetInfoTool(BaseTool):
    """Gets information about the current preset configuration."""

    modes = ["generation"]
    icon = "settings-2"

    @property
    def name(self) -> str:
        return "get_preset_info"

    @property
    def group(self) -> str:
        return "Models & presets"

    @property
    def user_description(self) -> str:
        return "Reads the settings and options of the preset you are using."

    @property
    def hint(self) -> str:
        return (
            "Call when you need to understand what the user's preset supports — "
            "available modes, pipeline steps, or form fields. Useful early in "
            "conversations and before suggesting workflow changes."
        )

    @property
    def description(self) -> str:
        return (
            "Get information about a preset configuration. "
            "Presets define how image generation works including available "
            "form fields, pipeline steps, and supported features. "
            "If no preset_id is provided, uses the current session's preset."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "preset_id": {
                    "type": "string",
                    "description": "The preset ID to look up. If omitted, uses the session's current preset.",
                }
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.preset_manager:
            return ToolResult(success=False, data="", error="Preset manager not available")

        form_state = context.session_metadata.get("form_state")
        form_state_preset_id = form_state.get("preset") if form_state else None
        preset_id = (
            kwargs.get("preset_id")
            or form_state_preset_id
            or context.session_metadata.get("preset_id")
        )
        if not preset_id:
            return ToolResult(
                success=False,
                data="",
                error="No preset_id provided and none found in session metadata",
            )

        try:
            preset_data = context.preset_manager.get_preset(preset_id)
            preset = preset_data.get("preset", preset_data)
            summary = {
                "id": preset.get("id", ""),
                "name": preset.get("name", ""),
                "description": preset.get("description", ""),
                "modes": preset.get("modes", []),
            }
            # The current mode only applies to this lookup when we resolved
            # preset_id from that same form_state - an explicit/legacy
            # preset_id may point at a different preset than the active form.
            current_mode = (
                form_state.get("mode")
                if form_state and preset_id == form_state_preset_id
                else None
            )
            # Include form fields summary if available
            form = preset.get("form")
            if form:
                fields_summary = []
                for field_item in (form if isinstance(form, list) else []):
                    field_info: Dict[str, Any] = {
                        "name": field_item.get("name", ""),
                        "type": field_item.get("type", ""),
                        "label": field_item.get("label", ""),
                    }
                    if field_item.get("description"):
                        field_info["description"] = field_item["description"]
                    if "default" in field_item:
                        field_info["default"] = field_item["default"]
                    if field_item.get("ai_hint"):
                        field_info["ai_hint"] = field_item["ai_hint"]
                    if field_item.get("min") is not None:
                        field_info["min"] = field_item["min"]
                    if field_item.get("max") is not None:
                        field_info["max"] = field_item["max"]
                    if field_item.get("step") is not None:
                        field_info["step"] = field_item["step"]
                    options = field_item.get("options")
                    if options and isinstance(options, list):
                        field_info["options_count"] = len(options)
                    fields_summary.append(field_info)
                summary["form_fields"] = fields_summary
            # Preset-authored prompting guide (see docs/presets.md "LLM context"),
            # replaced by the current mode's override when one is declared.
            llm_spec = preset.get("llm") or {}
            llm_modes = llm_spec.get("modes") or {}
            mode_spec = llm_modes.get(current_mode) if current_mode else None
            if mode_spec and mode_spec.get("guide"):
                summary["llm_guide"] = mode_spec["guide"]
            else:
                llm_guide = llm_spec.get("guide")
                if llm_guide:
                    summary["llm_guide"] = llm_guide
                if llm_modes:
                    summary["llm_guide_modes"] = list(llm_modes.keys())
            # Include pipeline info if available
            pipeline = preset.get("pipeline") or preset.get("pipes")
            if pipeline:
                summary["pipeline_steps"] = pipeline if isinstance(pipeline, list) else []
            return ToolResult(success=True, data=json.dumps(summary))
        except Exception as e:
            logger.error(f"Error getting preset info: {e}")
            return ToolResult(success=False, data="", error=str(e))
