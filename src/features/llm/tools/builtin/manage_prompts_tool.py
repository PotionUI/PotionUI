"""Approval-gated tools for detached rich Prompt aggregates."""

import json
import logging
from typing import Any, Dict, Optional

from src.features.prompt_database import operations
from src.features.prompt_database.dto import PromptRequest
from src.features.prompt_database.repository import flatten_segments
from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.errors import unexpected

logger = logging.getLogger(__name__)


def _truncate(text: str, limit: int = 90) -> str:
    return text[:limit] + ("..." if len(text) > limit else "")


RICH_SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["content", "break"], "default": "content"},
        "content": {"type": "string", "default": ""},
        "chips": {"type": "object", "description": "Complete phrasebook chip state keyed by chip id."},
        "enabled": {"type": "boolean", "default": True},
        "name": {"type": "string"},
        "color": {"type": "string"},
        "description": {"type": "string"},
    },
}


def _prompt_request(existing=None, **kwargs) -> PromptRequest:
    if existing is None:
        return PromptRequest(
            name=kwargs.get("name"), usage_hint=kwargs.get("usage_hint"),
            segments=kwargs.get("segments") or [], source_provider="llm_tool",
            tags=kwargs.get("tags") or [],
        )
    metadata_fields = (
        "source_provider", "source_id", "source_url", "source_group_id", "model_id",
        "model_name", "base_model", "cfg_scale", "steps", "sampler", "width", "height",
        "heart_count", "like_count", "laugh_count", "cry_count", "comment_count", "tags",
        "nsfw", "metadata",
    )
    values = {field: getattr(existing, field) for field in metadata_fields}
    values.update(
        name=kwargs.get("name", existing.name),
        usage_hint=kwargs.get("usage_hint", existing.usage_hint),
        segments=kwargs.get("segments", [segment.model_dump() for segment in existing.segments]),
    )
    return PromptRequest(**values)


class AddPromptTool(BaseTool):
    modes = ["generation", "prompts"]
    icon = "book-plus"

    @property
    def name(self): return "add_prompt"

    @property
    def group(self): return "Saved prompts"

    @property
    def user_description(self): return "Saves a new prompt composition to your library."

    @property
    def hint(self): return "When the user wants to save a rich, ordered prompt composition"

    @property
    def description(self):
        return (
            "Save one detached Prompt: an ordered array of one or more rich segments. "
            "Positive and negative prompts are separate records selected with usage_hint."
        )

    @property
    def requires_approval(self): return True

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Optional library name."},
                "usage_hint": {"type": "string", "enum": ["positive", "negative"]},
                "segments": {"type": "array", "items": RICH_SEGMENT_SCHEMA, "minItems": 1},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["segments"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.prompt_database:
            return ToolResult(success=False, data="", error="Prompt library not available")
        try:
            request = _prompt_request(**kwargs)
        except Exception as exc:
            return ToolResult(success=False, data="", error=str(exc))
        text = flatten_segments(request.segments)
        fields = []
        if request.usage_hint:
            fields.append({"label": "Usage", "value": request.usage_hint})
        if request.tags:
            fields.append({"label": "Tags", "value": ", ".join(request.tags)})
        preview = ToolApprovalPreview(
            action="Add prompt",
            target=request.name or None,
            kind="text_edit",
            summary=_truncate(text) or "New prompt",
            fields=fields,
            text_blocks=[{"label": "Prompt", "text": text}],
        )
        return ToolResult(success=True, data=json.dumps({
            "action": "add_prompt",
            "proposal": request.model_dump(),
            "message": "This detached Prompt will be added to the library. Please confirm.",
        }), preview=preview)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.prompt_database:
            return ToolResult(success=False, data="", error="Prompt library not available")
        try:
            saved = await operations.create_prompt(
                context.prompt_database, context.user_id, _prompt_request(**kwargs),
            )
            return ToolResult(success=True, data=json.dumps({
                "action": "add_prompt", "success": True, "prompt_id": saved.id,
                "message": f"Prompt {saved.id} saved.",
            }))
        except Exception as exc:
            logger.error("add_prompt failed: %s", exc)
            return ToolResult(success=False, data="", error=unexpected("add_prompt", "save", exc))


class EditPromptTool(BaseTool):
    modes = ["generation", "prompts"]
    icon = "book-open"

    @property
    def name(self): return "edit_prompt"

    @property
    def group(self): return "Saved prompts"

    @property
    def user_description(self): return "Updates a prompt already saved in your library."

    @property
    def hint(self): return "When the user wants to replace a saved Prompt composition"

    @property
    def description(self):
        return "Replace a Prompt's complete ordered segment array and/or its library name or usage hint."

    @property
    def requires_approval(self): return True

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "prompt_id": {"type": "string"},
                "name": {"type": "string"},
                "usage_hint": {"type": "string", "enum": ["positive", "negative"]},
                "segments": {"type": "array", "items": RICH_SEGMENT_SCHEMA, "minItems": 1},
            },
            "required": ["prompt_id"],
        }

    def _existing(self, context, prompt_id):
        return context.prompt_database.repository.get_by_id(prompt_id, context.user_id)

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.prompt_database:
            return ToolResult(success=False, data="", error="Prompt library not available")
        prompt_id = kwargs.get("prompt_id")
        existing = self._existing(context, prompt_id) if prompt_id else None
        if existing is None:
            return ToolResult(success=False, data="", error=f"Prompt '{prompt_id}' not found")
        if not any(key in kwargs for key in ("name", "usage_hint", "segments")):
            return ToolResult(success=False, data="", error="No Prompt fields were supplied")
        try:
            request = _prompt_request(existing, **kwargs)
        except Exception as exc:
            return ToolResult(success=False, data="", error=str(exc))

        old_text = flatten_segments(existing.segments)
        new_text = flatten_segments(request.segments)
        text_block = {"label": "Prompt", "text": new_text}
        if new_text != old_text:
            text_block["old_text"] = old_text

        fields = []
        if kwargs.get("name") and kwargs["name"] != existing.name:
            fields.append({"label": "Name", "value": kwargs["name"], "old": existing.name or "Untitled"})
        if kwargs.get("usage_hint") and kwargs["usage_hint"] != existing.usage_hint:
            fields.append({"label": "Usage", "value": kwargs["usage_hint"], "old": existing.usage_hint or "unset"})

        preview = ToolApprovalPreview(
            action="Edit prompt",
            target=existing.display_name,
            kind="text_edit",
            summary=_truncate(new_text),
            fields=fields,
            text_blocks=[text_block],
        )
        return ToolResult(success=True, data=json.dumps({
            "action": "edit_prompt",
            "proposal": {
                "prompt_id": prompt_id,
                "old": {"name": existing.name, "segments": [s.model_dump() for s in existing.segments]},
                "new": request.model_dump(),
            },
            "message": "The complete Prompt aggregate will be replaced. Please confirm.",
        }), preview=preview)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        prompt_id = kwargs.get("prompt_id")
        existing = self._existing(context, prompt_id) if context.prompt_database and prompt_id else None
        if existing is None:
            return ToolResult(success=False, data="", error=f"Prompt '{prompt_id}' not found")
        try:
            saved = await operations.replace_prompt(
                context.prompt_database, context.user_id, prompt_id, _prompt_request(existing, **kwargs),
            )
            return ToolResult(success=bool(saved), data=json.dumps({
                "action": "edit_prompt", "success": bool(saved), "prompt_id": prompt_id,
            }), error=None if saved else "Prompt could not be updated")
        except Exception as exc:
            logger.error("edit_prompt failed: %s", exc)
            return ToolResult(success=False, data="", error=unexpected("edit_prompt", "update", exc))


class DeletePromptTool(BaseTool):
    modes = ["generation", "prompts"]
    icon = "trash-2"

    @property
    def name(self): return "delete_prompt"

    @property
    def group(self): return "Saved prompts"

    @property
    def user_description(self): return "Removes a saved prompt from your library."

    @property
    def hint(self): return "When the user wants to permanently remove a saved Prompt"

    @property
    def description(self): return "Delete one detached Prompt aggregate from the user's library."

    @property
    def requires_approval(self): return True

    @property
    def parameters(self):
        return {"type": "object", "properties": {"prompt_id": {"type": "string"}}, "required": ["prompt_id"]}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        prompt_id = kwargs.get("prompt_id")
        existing = context.prompt_database.repository.get_by_id(prompt_id, context.user_id) if context.prompt_database and prompt_id else None
        if existing is None:
            return ToolResult(success=False, data="", error=f"Prompt '{prompt_id}' not found")
        preview = ToolApprovalPreview(
            action="Delete prompt",
            target=existing.display_name,
            summary=_truncate(existing.flattened_text),
        )
        return ToolResult(success=True, data=json.dumps({
            "action": "delete_prompt",
            "proposal": {"prompt_id": prompt_id, "name": existing.display_name, "preview": existing.flattened_text[:200]},
            "message": "This Prompt will be permanently deleted. Please confirm.",
        }), preview=preview)

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        prompt_id = kwargs.get("prompt_id")
        if not context.prompt_database or not prompt_id:
            return ToolResult(success=False, data="", error="prompt_id is required")
        success = operations.delete_prompt(context.prompt_database, context.user_id, prompt_id)
        return ToolResult(success=success, data=json.dumps({
            "action": "delete_prompt", "success": success, "prompt_id": prompt_id,
        }), error=None if success else f"Prompt '{prompt_id}' not found")
