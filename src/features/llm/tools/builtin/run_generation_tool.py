"""Run generation tool for triggering image/video generation from chat."""

import json
import logging
from typing import Any, Dict

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import build_generation_preview
from src.features.llm.tools.errors import unexpected
from src.features.llm.tools.media_values import (
    MEDIA_VALUE_FORM,
    media_field_names,
    validate_media_changes,
)

logger = logging.getLogger(__name__)


class RunGenerationTool(BaseTool):
    """Triggers an image/video generation using the user's current form state."""

    modes = ["generation"]
    icon = "play"

    @property
    def name(self) -> str:
        return "run_generation"

    @property
    def group(self) -> str:
        return "Generation"

    @property
    def user_description(self) -> str:
        return "Starts a generation with your current form settings."

    @property
    def hint(self) -> str:
        return (
            "When the user wants to generate — e.g., 'generate', 'run it', "
            "'make an image', 'let's try it' — propose the generation."
            "{{#if get_form_state}} Call get_form_state first to confirm settings.{{/if}}"
        )

    @property
    def description(self) -> str:
        return (
            "Start an image/video generation using the user's current form state. "
            "Returns a preview of the generation config for user approval before starting."
            "{{#if get_form_state}} Call get_form_state first to verify settings are correct.{{/if}}"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "override_values": {
                    "type": "object",
                    "description": (
                        "Optional field overrides to apply before generating "
                        "(e.g., {'width': 1024, 'height': 1024}). "
                        "Field names must match form field names from get_form_state. "
                        "An image/video/audio/media field takes "
                        f"{MEDIA_VALUE_FORM}."
                    ),
                },
            },
            "required": [],
        }

    @property
    def requires_approval(self) -> bool:
        return True

    @staticmethod
    def _validate_media_overrides(context, preset_id, mode, override_values) -> list:
        """Errors for any media-field override the model proposed."""
        if not override_values or not context.preset_manager:
            return []
        try:
            schema_data = context.preset_manager.get_form_schema(preset_id, mode=mode)
            media_fields = media_field_names(
                schema_data.get("form_schema", {}).get("properties", {})
            )
        except Exception as e:
            logger.debug(f"Could not load form schema for media validation: {e}")
            return []
        return validate_media_changes(override_values, media_fields, context.storage_dir())

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Build a generation preview for user approval."""
        form_state = context.session_metadata.get("form_state")
        if not form_state:
            return ToolResult(
                success=False, data="",
                error="No form state available. The user needs to have a preset and form loaded.",
            )

        preset_id = form_state.get("preset")
        mode = form_state.get("mode", "txt2img")
        form_data = dict(form_state.get("form_data", {}))
        old_form_data = dict(form_data)

        if not preset_id:
            return ToolResult(
                success=False, data="",
                error="No preset selected. The user needs to select a preset first.",
            )

        # Apply overrides. A media override is a PATH the model chose, so it is
        # validated first: `bind_form` downstream checks containment but not
        # existence, so an invented path would only fail deep inside a pipe.
        override_values = kwargs.get("override_values") or {}
        media_errors = self._validate_media_overrides(context, preset_id, mode, override_values)
        if media_errors:
            return ToolResult(success=False, data="", error="; ".join(media_errors))
        for field_name, value in override_values.items():
            form_data[field_name] = value

        # Extract key settings for the preview
        segments = context.session_metadata.get("segments", [])
        prompt_parts = []
        negative_parts = []
        for seg in segments:
            if seg.get("enabled") is False or seg.get("isDisabled"):
                continue
            content = seg.get("content", "").strip()
            if not content:
                continue
            seg_type = seg.get("type", "positive")
            if seg_type == "negative":
                negative_parts.append(content)
            else:
                prompt_parts.append(content)

        prompt_text = " ".join(prompt_parts) if prompt_parts else form_data.get("prompt", "")
        negative_text = " ".join(negative_parts) if negative_parts else form_data.get("negative_prompt", "")

        # Build a human-readable preview
        preview = {
            "action": "Start generation",
            "preset_id": preset_id,
            "mode": mode,
            "prompt": prompt_text[:300] + ("..." if len(prompt_text) > 300 else ""),
        }
        if negative_text:
            preview["negative_prompt"] = negative_text[:200] + ("..." if len(negative_text) > 200 else "")
        if override_values:
            preview["overrides_applied"] = override_values

        # Include key form settings in preview
        key_fields = ["width", "height", "steps", "cfg", "cfg_scale", "sampler", "scheduler", "seed", "batch_size"]
        settings_preview = {}
        for field in key_fields:
            if field in form_data:
                settings_preview[field] = form_data[field]
        if settings_preview:
            preview["settings"] = settings_preview

        approval_preview = build_generation_preview(
            preset_id=preset_id,
            mode=mode,
            prompt_text=prompt_text,
            negative_text=negative_text,
            form_data=form_data,
            old_form_data=old_form_data,
        )

        return ToolResult(
            success=True,
            data=json.dumps(preview),
            preview=approval_preview,
        )

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """Actually start the generation after user approval."""
        if not context.generation_orchestrator:
            return ToolResult(
                success=False, data="",
                error="Generation orchestrator not available.",
            )

        form_state = context.session_metadata.get("form_state")
        if not form_state:
            return ToolResult(success=False, data="", error="No form state available.")

        preset_id = form_state.get("preset")
        mode = form_state.get("mode", "txt2img")
        form_data = dict(form_state.get("form_data", {}))

        # Apply overrides again (same as in execute, validation included: these
        # arguments are replayed from storage, so the preview's check does not
        # carry over, and approval is not evidence a path exists).
        override_values = kwargs.get("override_values") or {}
        media_errors = self._validate_media_overrides(context, preset_id, mode, override_values)
        if media_errors:
            return ToolResult(success=False, data="", error="; ".join(media_errors))
        for field_name, value in override_values.items():
            form_data[field_name] = value

        # Build prompts from segments
        segments = context.session_metadata.get("segments", [])
        prompt_parts = []
        negative_parts = []
        for seg in segments:
            if seg.get("enabled") is False or seg.get("isDisabled"):
                continue
            content = seg.get("content", "").strip()
            if not content:
                continue
            seg_type = seg.get("type", "positive")
            if seg_type == "negative":
                negative_parts.append(content)
            else:
                prompt_parts.append(content)

        prompt_text = " ".join(prompt_parts) if prompt_parts else form_data.get("prompt", "")
        negative_text = " ".join(negative_parts) if negative_parts else form_data.get("negative_prompt", "")

        try:
            from src.features.generation.dto import GenerationRequest, PromptPair

            request = GenerationRequest(
                preset_id=preset_id,
                mode=mode,
                form_data=form_data,
                prompts=[PromptPair(positive=prompt_text, negative=negative_text)],
            )

            result = await context.generation_orchestrator.start_generation(
                request=request,
                user_id=context.user_id,
            )

            return ToolResult(
                success=True,
                data=json.dumps({
                    "message": "Generation started successfully",
                    "generation_id": result.get("generation_id", ""),
                    "status": result.get("status", {}).get("status", "pending"),
                }),
            )
        except Exception as e:
            logger.error(f"Failed to start generation: {e}")
            return ToolResult(success=False, data="", error=unexpected("run_generation", "start the generation", e))
