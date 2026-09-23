"""Preset info tool for accessing preset configuration."""

import json
import logging
from typing import Any, Dict

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.presets.form_overrides import mode_field_inventory

logger = logging.getLogger(__name__)

_NOT_FOUND_HINT = (
    "Omit preset_id to use the session's current preset, or call get_form_state "
    "to see which preset is active."
)


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
        if not context.preset_collaborators:
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

        collaborators = context.preset_collaborators
        if not context.is_admin:
            try:
                allowed_ids = collaborators.db_repo.get_available_preset_ids_for_user(context.user_id)
            except Exception:
                logger.warning("could not resolve assigned presets for access check", exc_info=True)
                allowed_ids = []
            if preset_id not in allowed_ids:
                return ToolResult(success=False, data="", error=f"No preset '{preset_id}'. {_NOT_FOUND_HINT}")

        try:
            found_preset = collaborators.file_repo.find_preset_by_id(preset_id)
            if not found_preset:
                return ToolResult(success=False, data="", error=f"No preset '{preset_id}'. {_NOT_FOUND_HINT}")

            mode_names = list((found_preset.modes or {}).keys())
            requested_mode = (
                form_state.get("mode") if form_state and preset_id == form_state_preset_id else None
            )
            current_mode = requested_mode if requested_mode in mode_names else (
                mode_names[0] if mode_names else None
            )

            summary: Dict[str, Any] = {
                "id": found_preset.id,
                "name": found_preset.name,
                "description": found_preset.description or "",
                "modes": mode_names,
            }

            if current_mode:
                summary["mode"] = current_mode
                fields_summary = []
                for field_item in mode_field_inventory(found_preset, current_mode).values():
                    field_info: Dict[str, Any] = {
                        "name": field_item.name or "",
                        "type": field_item.type,
                        "label": field_item.label or "",
                    }
                    if field_item.description:
                        field_info["description"] = field_item.description
                    if field_item.default is not None:
                        field_info["default"] = field_item.default
                    if field_item.ai_hint:
                        field_info["ai_hint"] = field_item.ai_hint
                    config = field_item.configuration or {}
                    if config.get("min") is not None:
                        field_info["min"] = config["min"]
                    if config.get("max") is not None:
                        field_info["max"] = config["max"]
                    if config.get("step") is not None:
                        field_info["step"] = config["step"]
                    options = config.get("options")
                    if isinstance(options, list):
                        field_info["options_count"] = len(options)
                    fields_summary.append(field_info)
                summary["form_fields"] = fields_summary

            # Preset-authored prompting guide (see docs/presets.md "LLM context"),
            # replaced by the current mode's override when one is declared.
            llm_spec = found_preset.llm or {}
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

            return ToolResult(success=True, data=json.dumps(summary))
        except Exception as e:
            logger.error(f"Error getting preset info: {e}")
            return ToolResult(success=False, data="", error=str(e))
