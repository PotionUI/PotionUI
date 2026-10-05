"""Preset info tool for accessing preset configuration."""

import json
import logging
from typing import Any, Dict, List

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.errors import unexpected
from src.features.presets import operations as preset_operations
from src.features.presets.form_overrides import mode_field_inventory
from src.platform.security.user import AccountType, User

logger = logging.getLogger(__name__)

_NOT_FOUND_HINT = "Call list_presets to see the preset ids you can use."

DEFAULT_PRESET_LIMIT = 50
MAX_PRESET_LIMIT = 200


def _short(text: Any, limit: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _stand_in_user(context: ToolContext) -> User:
    return User(
        username="", email="", password_hash="",
        id=context.user_id,
        account_type=AccountType.ADMIN if context.is_admin else AccountType.USER,
    )


class ListPresetsTool(BaseTool):
    modes = ["generation"]
    icon = "layout-grid"

    @property
    def name(self) -> str:
        return "list_presets"

    @property
    def group(self) -> str:
        return "Models & presets"

    @property
    def user_description(self) -> str:
        return "Lists the presets you can generate with."

    @property
    def hint(self) -> str:
        return (
            "Call to find a preset id: before get_preset_info or start_generation when no "
            "preset is open, or when the user names a model family or asks what they can make."
        )

    @property
    def description(self) -> str:
        return (
            "List the presets the user can generate with, honoring their preset and group "
            "access. Each entry has id, name, engine, modes and a short description. Pass an id "
            "to get_preset_info for its form fields. Optional 'query' matches id, name, tags and "
            "description; 'engine' filters by engine (e.g. 'native', 'comfyui'). Paged with "
            f"'offset'/'limit' (default {DEFAULT_PRESET_LIMIT}, max {MAX_PRESET_LIMIT})."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Case-insensitive match on id, name, tags and description."},
                "engine": {"type": "string", "description": "Only presets for this engine."},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PRESET_LIMIT, "default": DEFAULT_PRESET_LIMIT},
            },
            "required": [],
        }

    @staticmethod
    def _matches(preset: Dict[str, Any], query: str, engine: str) -> bool:
        if engine and (preset.get("engine") or "").lower() != engine:
            return False
        if not query:
            return True
        haystack = " ".join([
            str(preset.get("id") or ""), str(preset.get("name") or ""),
            str(preset.get("description") or ""), " ".join(preset.get("tags") or []),
        ]).lower()
        return query in haystack

    def _modes(self, context: ToolContext, preset_id: str) -> List[str]:
        try:
            found = context.preset_collaborators.file_repo.find_preset_by_id(preset_id)
        except Exception:
            logger.warning("list_presets could not load preset %s", preset_id, exc_info=True)
            return []
        return list((getattr(found, "modes", None) or {}).keys()) if found else []

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.preset_collaborators:
            return ToolResult(success=False, data="", error="Presets are not available on this server.")
        query = (kwargs.get("query") or "").strip().lower()
        engine = (kwargs.get("engine") or "").strip().lower()
        try:
            offset = max(0, int(kwargs.get("offset") or 0))
            raw_limit = kwargs.get("limit")
            limit = DEFAULT_PRESET_LIMIT if raw_limit in (None, "") else int(raw_limit)
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'offset' and 'limit' must be integers.")
        limit = min(max(1, limit), MAX_PRESET_LIMIT)
        try:
            presets = preset_operations.list_presets(context.preset_collaborators, _stand_in_user(context))
        except Exception as e:
            logger.error("list_presets failed: %s", e)
            return ToolResult(success=False, data="", error=unexpected("list_presets", "list", e))
        matching = sorted(
            (p for p in presets if self._matches(p, query, engine)),
            key=lambda p: (str(p.get("name") or "").lower(), str(p.get("id"))),
        )
        page = matching[offset:offset + limit]
        entries = []
        for preset in page:
            entry = {
                "id": preset.get("id"),
                "name": preset.get("name"),
                "engine": preset.get("engine"),
                "modes": self._modes(context, preset.get("id")),
                "description": _short(preset.get("description")),
            }
            if preset.get("tags"):
                entry["tags"] = preset["tags"]
            entries.append(entry)
        payload: Dict[str, Any] = {
            "presets": entries,
            "total": len(matching),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < len(matching),
        }
        if not matching:
            payload["message"] = (
                "No presets matched." if query or engine
                else "No presets are available to you yet. An admin can assign presets to you or your group."
            )
        return ToolResult(success=True, data=json.dumps(payload))


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
            "Get information about a preset configuration: its modes, the form fields of one "
            "mode (name, type, default, range) and its prompting guide. Get preset ids from "
            "list_presets. preset_id is required unless the chat has an open Generate tab, in "
            "which case it defaults to that tab's preset. 'mode' picks which mode's fields to "
            "describe (default: the open tab's mode, else the preset's first mode)."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "preset_id": {
                    "type": "string",
                    "description": "Preset id from list_presets. Optional only when a Generate tab is open.",
                },
                "mode": {
                    "type": "string",
                    "description": "Mode whose form fields to describe; one of the preset's modes.",
                },
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
                error=(
                    "preset_id is required: there is no open Generate tab to take it from. "
                    "Call list_presets to see the preset ids you can use."
                ),
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
            explicit_mode = kwargs.get("mode")
            if explicit_mode and explicit_mode not in mode_names:
                return ToolResult(
                    success=False, data="",
                    error=f"Preset '{preset_id}' has no mode '{explicit_mode}'. Its modes are: {', '.join(mode_names) or 'none'}.",
                )
            requested_mode = explicit_mode or (
                form_state.get("mode") if form_state and preset_id == form_state_preset_id else None
            )
            current_mode = requested_mode if requested_mode in mode_names else (
                mode_names[0] if mode_names else None
            )

            summary: Dict[str, Any] = {
                "id": found_preset.id,
                "name": found_preset.name,
                "engine": getattr(found_preset, "engine", None),
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
            return ToolResult(success=False, data="", error=unexpected("get_preset_info", "lookup", e))
