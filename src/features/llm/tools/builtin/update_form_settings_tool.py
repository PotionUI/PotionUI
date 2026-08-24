"""Tool for proposing form field value changes."""

import json
import logging
from typing import Any, Dict

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.media_values import (
    MEDIA_VALUE_FORM,
    media_field_names,
    validate_media_value,
)

logger = logging.getLogger(__name__)


class UpdateFormSettingsTool(BaseTool):
    """Proposes changes to the user's form field values."""

    modes = ["generation"]
    icon = "sliders-horizontal"

    @property
    def name(self) -> str:
        return "update_form_settings"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Changes values on your generation form for you."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "When the user asks to change generation settings or wants recommendations "
            "for better values — propose changes with this tool."
            "{{#if get_form_state}} Always call get_form_state first to see current "
            "values before proposing changes.{{/if}}"
        )

    @property
    def description(self) -> str:
        return (
            "Propose changes to the user's form field values. Each change specifies "
            "a field_name, new value, and optional reason. The user must approve "
            "before changes are applied. An image/video/audio/media field takes "
            f"{MEDIA_VALUE_FORM}."
            "{{#if get_form_state}} Call get_form_state first to see available "
            "fields and their current values.{{/if}}"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "array",
                    "description": "List of field changes to propose",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field_name": {
                                "type": "string",
                                "description": "The form field name to change",
                            },
                            "value": {
                                "description": "The new value for the field",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Optional explanation for why this change is recommended",
                            },
                        },
                        "required": ["field_name", "value"],
                    },
                }
            },
            "required": ["changes"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Preview proposed changes - validates fields and shows old -> new values."""
        changes = kwargs.get("changes", [])
        if not changes:
            return ToolResult(
                success=False,
                data="",
                error="No changes provided. Specify at least one field change.",
            )

        form_state = context.session_metadata.get("form_state")
        if not form_state:
            return ToolResult(
                success=False,
                data="",
                error="No form state available. The user may not have a form loaded.",
            )

        form_data = form_state.get("form_data", {})
        preset_id = form_state.get("preset")
        mode = form_state.get("mode")

        # Get schema to validate field names
        known_fields = set(form_data.keys())
        media_fields: set = set()
        if preset_id and context.preset_manager:
            try:
                schema_data = context.preset_manager.get_form_schema(
                    preset_id, mode=mode
                )
                schema_props = schema_data.get("form_schema", {}).get("properties", {})
                known_fields.update(schema_props.keys())
                media_fields = media_field_names(schema_props)
            except Exception as e:
                logger.debug(f"Could not load form schema for validation: {e}")

        storage_dir = context.storage_dir()

        # Validate and build preview
        preview = []
        errors = []
        for change in changes:
            field_name = change.get("field_name", "")
            new_value = change.get("value")
            reason = change.get("reason", "")

            if not field_name:
                errors.append("Empty field_name in change")
                continue

            if field_name not in known_fields:
                errors.append(
                    f"Unknown field '{field_name}'. "
                    f"Available fields: {', '.join(sorted(known_fields)[:20])}"
                )
                continue

            # A media field's value is a PATH, and a model with only a
            # generation id in hand will invent one. Reject it here, naming the
            # valid form, rather than let it reach the form and fail in a pipe.
            media_errors = validate_media_value(field_name, new_value, storage_dir) \
                if field_name in media_fields else []
            if media_errors:
                errors.extend(media_errors)
                continue

            old_value = form_data.get(field_name)
            entry = {
                "field_name": field_name,
                "old_value": old_value,
                "new_value": new_value,
            }
            if reason:
                entry["reason"] = reason
            preview.append(entry)

        if errors and not preview:
            return ToolResult(
                success=False,
                data="",
                error="; ".join(errors),
            )

        result = {
            "status": "pending_approval",
            "proposed_changes": preview,
            "change_count": len(preview),
        }
        if errors:
            result["warnings"] = errors

        return ToolResult(success=True, data=json.dumps(result))

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """After user approval, return action payload for frontend to apply."""
        changes = kwargs.get("changes", [])

        form_state = context.session_metadata.get("form_state")
        form_data = form_state.get("form_data", {}) if form_state else {}

        # Approval is the USER agreeing to the change, not evidence the path
        # exists - and these arguments are replayed from storage, so the
        # preview's check does not carry over. Re-run it.
        media_fields = self._media_fields(context, form_state)
        storage_dir = context.storage_dir()

        applied = []
        rejected = []
        for change in changes:
            field_name = change.get("field_name", "")
            new_value = change.get("value")
            if field_name in media_fields:
                media_errors = validate_media_value(field_name, new_value, storage_dir)
                if media_errors:
                    rejected.extend(media_errors)
                    continue
            if field_name:
                applied.append({
                    "field_name": field_name,
                    "old_value": form_data.get(field_name),
                    "new_value": new_value,
                    "reason": change.get("reason", ""),
                })

        if rejected and not applied:
            return ToolResult(success=False, data="", error="; ".join(rejected))

        payload = {
            "action": "apply_form_changes",
            "applied_changes": applied,
        }
        if rejected:
            payload["rejected_changes"] = rejected

        return ToolResult(success=True, data=json.dumps(payload))

    @staticmethod
    def _media_fields(context: ToolContext, form_state) -> set:
        """Media-carrying field names for the loaded preset's form, or empty."""
        preset_id = (form_state or {}).get("preset")
        if not preset_id or not context.preset_manager:
            return set()
        try:
            schema_data = context.preset_manager.get_form_schema(
                preset_id, mode=(form_state or {}).get("mode")
            )
            return media_field_names(schema_data.get("form_schema", {}).get("properties", {}))
        except Exception as e:
            logger.debug(f"Could not load form schema for media validation: {e}")
            return set()
