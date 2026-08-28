"""Starts a generation from explicit preset/form arguments rather than a live
chat session's form_state (see `run_generation_tool.py` for the chat-form
counterpart). This is the tool an MCP client - which has no Generate form
open - uses to generate: it submits through the same
`generation_orchestrator.start_generation()` the HTTP `/api/generations/start`
route uses, so `bind_form` fills every field the caller didn't set with the
preset's own defaults.
"""

import json
import logging
from typing import Any, Dict

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import build_generation_preview
from src.features.llm.tools.errors import unexpected
from src.features.llm.tools.media_values import MEDIA_VALUE_FORM, preset_form_media_errors
from src.features.llm.tools.model_values import preset_form_model_errors

logger = logging.getLogger(__name__)


class StartGenerationTool(BaseTool):
    """Starts an image/video generation from an explicit preset id and form values."""

    modes = ["generation"]
    icon = "play"

    @property
    def name(self) -> str:
        return "start_generation"

    @property
    def group(self) -> str:
        return "Generation"

    @property
    def user_description(self) -> str:
        return "Starts a generation from a chosen preset, without needing an open form."

    @property
    def hint(self) -> str:
        return (
            "When there is no live Generate form to read (e.g. no form_state / an MCP caller) "
            "and the user wants to generate with a specific preset - give preset_id, prompt, and "
            "any field overrides directly.{{#if get_preset_info}} Call get_preset_info first to "
            "see the preset's fields and default mode.{{/if}}"
        )

    @property
    def description(self) -> str:
        return (
            "Start a generation for an explicit preset, independent of any live form state. "
            "Unset fields fall back to the preset's own defaults. Returns a preview for approval "
            "before starting. Example: "
            '{"preset_id": "sdxl/base", "mode": "txt2img", "prompt": "a red fox in snow", '
            '"form_overrides": {"width": 1024, "height": 1024, "steps": 30}}'
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "preset_id": {"type": "string", "description": "The preset id to generate with."},
                "mode": {"type": "string", "description": "Generation mode (e.g. txt2img, img2img). Defaults to txt2img."},
                "prompt": {"type": "string"},
                "negative_prompt": {"type": "string"},
                "form_overrides": {
                    "type": "object",
                    "description": (
                        "Field overrides on top of the preset's defaults (e.g. {'width': 1024, "
                        "'seed': 12345, 'batch_size': 4}). Field names must match the preset's form "
                        f"fields (see get_preset_info). An image/video/audio/media field takes {MEDIA_VALUE_FORM}."
                    ),
                },
            },
            "required": ["preset_id"],
        }

    @property
    def requires_approval(self) -> bool:
        return True

    @staticmethod
    def _build_preview(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        preview: Dict[str, Any] = {
            "action": "Start generation",
            "preset_id": kwargs.get("preset_id"),
            "mode": kwargs.get("mode") or "txt2img",
        }
        if kwargs.get("prompt"):
            preview["prompt"] = kwargs["prompt"]
        if kwargs.get("negative_prompt"):
            preview["negative_prompt"] = kwargs["negative_prompt"]
        if kwargs.get("form_overrides"):
            preview["form_overrides"] = kwargs["form_overrides"]
        return preview

    @staticmethod
    def _build_approval_preview(kwargs: Dict[str, Any]):
        # No live form here (see the module docstring), so there is no "old"
        # value to diff a form_override against - old_form_data stays None.
        return build_generation_preview(
            preset_id=kwargs.get("preset_id"),
            mode=kwargs.get("mode") or "txt2img",
            prompt_text=kwargs.get("prompt") or "",
            negative_text=kwargs.get("negative_prompt") or "",
            form_data=kwargs.get("form_overrides") or {},
        )

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Build a generation preview for user approval."""
        preset_id = kwargs.get("preset_id")
        if not preset_id:
            return ToolResult(success=False, data="", error="'preset_id' is required")

        mode = kwargs.get("mode") or "txt2img"
        overrides = kwargs.get("form_overrides") or {}
        media_errors = preset_form_media_errors(context.preset_manager, context.storage_dir(), preset_id, mode, overrides)
        if media_errors:
            return ToolResult(success=False, data="", error="; ".join(media_errors))
        model_errors = preset_form_model_errors(
            context.preset_manager, context.model_index_manager, preset_id, mode, overrides
        )
        if model_errors:
            return ToolResult(success=False, data="", error="; ".join(model_errors))

        return ToolResult(
            success=True,
            data=json.dumps(self._build_preview(kwargs)),
            preview=self._build_approval_preview(kwargs),
        )

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        """Actually start the generation after user approval."""
        if not context.generation_orchestrator:
            return ToolResult(success=False, data="", error="Generation orchestrator not available.")

        preset_id = kwargs.get("preset_id")
        if not preset_id:
            return ToolResult(success=False, data="", error="'preset_id' is required")

        mode = kwargs.get("mode") or "txt2img"
        form_data = dict(kwargs.get("form_overrides") or {})

        # Re-validated on replay (same reasoning as run_generation): approval
        # is not evidence a path still exists.
        media_errors = preset_form_media_errors(context.preset_manager, context.storage_dir(), preset_id, mode, form_data)
        if media_errors:
            return ToolResult(success=False, data="", error="; ".join(media_errors))
        model_errors = preset_form_model_errors(
            context.preset_manager, context.model_index_manager, preset_id, mode, form_data
        )
        if model_errors:
            return ToolResult(success=False, data="", error="; ".join(model_errors))

        try:
            from src.features.generation.dto import GenerationRequest, PromptPair

            request = GenerationRequest(
                preset_id=preset_id,
                mode=mode,
                form_data=form_data,
                prompts=[PromptPair(positive=kwargs.get("prompt") or "", negative=kwargs.get("negative_prompt") or "")],
            )

            result = await context.generation_orchestrator.start_generation(
                request=request,
                user_id=context.user_id,
            )

            return ToolResult(success=True, data=json.dumps({
                "message": "Generation started successfully",
                "generation_id": result.get("generation_id", ""),
                "status": result.get("status", {}).get("status", "pending"),
            }))
        except Exception as e:
            logger.error(f"Failed to start generation: {e}")
            return ToolResult(success=False, data="", error=unexpected("start_generation", "start the generation", e))
