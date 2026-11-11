"""Approval-gated tools for detached rich Prompt aggregates."""

import json
from typing import Any, Dict, Optional

from src.features.prompt_database import operations
from src.features.prompt_database.dto import PromptRequest
from src.features.prompt_database.repository import flatten_segments
from src.features.llm.tools.base import ApprovalTool, BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.builtin.condition_text import ensure_condition_tags
from src.features.llm.tools.builtin.utils import parse_page, viewer_is_restricted


def _truncate(text: str, limit: int = 90) -> str:
    return text[:limit] + ("..." if len(text) > limit else "")


RICH_SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["content"], "default": "content"},
        "content": {"type": "string", "default": ""},
        "chips": {"type": "object", "description": "Complete phrasebook chip state keyed by chip id."},
        "enabled": {"type": "boolean", "default": True},
        "name": {"type": "string"},
        "color": {"type": "string"},
        "description": {"type": "string"},
    },
}

VARIABLES_SCHEMA = {
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["text", "choice", "number"]},
            "value": {"oneOf": [{"type": "string"}, {"type": "number"}]},
            "min": {"type": "number"},
            "max": {"type": "number"},
            "step": {"type": "number"},
            "mode": {"type": "string", "enum": ["shuffle", "pin", "per-image"], "description": "choice or number"},
            "pinnedIndex": {"type": "integer", "description": "choice + pin only"},
            "options": {
                "type": "array",
                "description": "choice only",
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "when": {
                                    "type": "object",
                                    "properties": {
                                        "var": {"type": "string"},
                                        "values": {"type": "array", "items": {"type": "string"}},
                                    },
                                    "required": ["var", "values"],
                                },
                            },
                            "required": ["text"],
                        },
                    ]
                },
            },
        },
        "required": ["type"],
    },
    "description": "Prompt ${name} variables, keyed by name.",
}


def _condition_texts(request: PromptRequest) -> Dict[str, Optional[str]]:
    return {
        f"segment {index} {field}": getattr(segment, field, None)
        for index, segment in enumerate(request.segments, start=1)
        for field in ("content", "prefix", "suffix")
    }


def _visible_prompt(context: ToolContext, prompt_id: Optional[str]):
    if not context.prompt_database or not prompt_id:
        return None
    prompt = context.prompt_database.repository.get_by_id(prompt_id, context.user_id)
    if prompt is not None and prompt.nsfw and viewer_is_restricted(context):
        return None
    return prompt


def _require_prompt(context: ToolContext, prompt_id: Optional[str]):
    prompt = _visible_prompt(context, prompt_id)
    if prompt is None:
        raise ValueError(f"Prompt '{prompt_id}' not found")
    return prompt


def _prompt_request(existing=None, **kwargs) -> PromptRequest:
    if existing is None:
        return PromptRequest(
            name=kwargs.get("name"), usage_hint=kwargs.get("usage_hint"),
            segments=kwargs.get("segments") or [], source_provider="llm_tool",
            tags=kwargs.get("tags") or [], variables=kwargs.get("variables"),
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
        variables=kwargs.get("variables", existing.variables),
        segments=kwargs.get("segments", [segment.model_dump() for segment in existing.segments]),
    )
    return PromptRequest(**values)


class GetPromptTool(BaseTool):
    modes = ["generation", "prompts"]
    icon = "book-open"
    context_group = "library"

    name = "get_prompt"
    mcp = True
    read_only = True
    group = "Saved prompts"
    user_description = "Reads one prompt from your library in full."
    requires_approval = False

    @property
    def description(self):
        return (
            "Read one saved Prompt in full: name, usage_hint, every segment with its chips, "
            "and its ${name} variables. The result has the same shape edit_prompt accepts, "
            "so read it first, change what is needed, and send it back."
        )

    @property
    def parameters(self):
        return {"type": "object", "properties": {"prompt_id": {"type": "string"}}, "required": ["prompt_id"]}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        prompt_id = kwargs.get("prompt_id")
        existing = _visible_prompt(context, prompt_id)
        if existing is None:
            return ToolResult(success=False, data="", error=f"Prompt '{prompt_id}' not found")
        payload = {
            "prompt_id": existing.id,
            "name": existing.name,
            "usage_hint": existing.usage_hint,
            "segments": [segment.model_dump(mode="json", exclude_none=True) for segment in existing.segments],
            "variables": existing.variables or {},
            "is_favorite": existing.is_favorite,
        }
        return ToolResult(success=True, data=json.dumps(payload))


class ListPromptsTool(BaseTool):
    modes = ["generation", "prompts"]
    icon = "book-open"
    context_group = "library"

    name = "list_prompts"
    mcp = True
    read_only = True
    group = "Saved prompts"
    user_description = "Lists the prompts in your library."
    requires_approval = False

    @property
    def description(self):
        return (
            "List the user's saved Prompts, newest first, with id, name, usage_hint, a short "
            "text preview and updated_at. Use get_prompt for the full content."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Match against name and text."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.prompt_database:
            return ToolResult(success=False, data="", error="Prompt library not available")
        try:
            offset, limit = parse_page(kwargs, 20, 100)
        except ValueError as exc:
            return ToolResult(success=False, data="", error=str(exc))
        prompts = context.prompt_database.repository.get_all(
            context.user_id, limit=limit, offset=offset,
            q=(kwargs.get("search") or "").strip() or None, sort_by="updated_at", nsfw="exclude",
        )
        return ToolResult(success=True, data=json.dumps({
            "prompts": [
                {
                    "id": prompt.id,
                    "name": prompt.display_name,
                    "usage_hint": prompt.usage_hint,
                    "preview": _truncate(" ".join((prompt.flattened_text or "").split()), 120),
                    "updated_at": prompt.updated_at.isoformat() if prompt.updated_at else None,
                }
                for prompt in prompts
            ],
            "limit": limit,
            "offset": offset,
        }))


class _PromptWriteTool(ApprovalTool):
    modes = ["prompts"]
    context_group = "library"
    group = "Saved prompts"
    repositories = ("prompt_database",)
    unavailable_error = "Prompt library not available"


class AddPromptTool(_PromptWriteTool):
    icon = "book-plus"

    name = "add_prompt"
    mcp = True
    user_description = "Saves a new prompt composition to your library."

    @property
    def description(self):
        return (
            "Save one detached Prompt: an ordered array of one or more rich segments. "
            "Positive and negative prompts are separate records selected with usage_hint. "
            "Optionally carries ${name} prompt variables. "
            "When the user supplies the prompt text, save it VERBATIM in one segment: do not add, "
            "reorder, translate or enrich terms, and do not borrow vocabulary from memory or search "
            "results. Compose or improve text only when the user asks you to. Use the name the user gave."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Optional library name."},
                "usage_hint": {"type": "string", "enum": ["positive", "negative"]},
                "segments": {"type": "array", "items": RICH_SEGMENT_SCHEMA, "minItems": 1},
                "tags": {"type": "array", "items": {"type": "string"}},
                "variables": VARIABLES_SCHEMA,
            },
            "required": ["segments"],
        }

    def _preview(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        request = _prompt_request(**kwargs)
        ensure_condition_tags(_condition_texts(request))
        text = flatten_segments(request.segments)
        fields = []
        if request.name:
            fields.append({"label": "Name", "value": request.name})
        if request.usage_hint:
            fields.append({"label": "Usage", "value": request.usage_hint})
        if request.tags:
            fields.append({"label": "Tags", "value": ", ".join(request.tags)})
        if request.variables:
            fields.append({"label": "Variables", "value": f"{len(request.variables)} variables"})
        preview = ToolApprovalPreview(
            action="Add prompt",
            target=request.name or None,
            kind="text_edit",
            summary=_truncate(text) or "New prompt",
            fields=fields,
            text_blocks=[{"label": "Prompt", "text": text}],
        )
        return self._proposal(preview, request.model_dump(), "This detached Prompt will be added to the library. Please confirm.")

    async def _apply(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        saved = await operations.create_prompt(
            context.prompt_database, context.user_id, _prompt_request(**kwargs),
        )
        saved_name = saved.display_name
        return ToolResult(success=True, data=json.dumps({
            "action": "add_prompt", "success": True, "prompt_id": saved.id,
            "name": saved_name,
            "message": f'Saved the prompt "{saved_name}" to the library (id {saved.id}).',
        }))


class EditPromptTool(_PromptWriteTool):
    icon = "book-open"

    name = "edit_prompt"
    mcp = True
    user_description = "Updates a prompt already saved in your library."

    @property
    def description(self):
        return (
            "Replace a Prompt's complete ordered segment array and/or its library name or "
            "usage hint. Can also replace its ${name} prompt variables. "
            "When the user supplies the new text, save it VERBATIM: do not add, reorder or enrich "
            "terms. Rewrite or improve text only when the user asks you to."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "prompt_id": {"type": "string"},
                "name": {"type": "string"},
                "usage_hint": {"type": "string", "enum": ["positive", "negative"]},
                "segments": {"type": "array", "items": RICH_SEGMENT_SCHEMA, "minItems": 1},
                "variables": VARIABLES_SCHEMA,
            },
            "required": ["prompt_id"],
        }

    def _preview(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        prompt_id = kwargs.get("prompt_id")
        existing = _require_prompt(context, prompt_id)
        if not any(key in kwargs for key in ("name", "usage_hint", "segments", "variables")):
            raise ValueError("No Prompt fields were supplied")
        request = _prompt_request(existing, **kwargs)
        ensure_condition_tags(_condition_texts(request))

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
        if "variables" in kwargs and kwargs["variables"] != existing.variables:
            fields.append({
                "label": "Variables",
                "value": f"{len(request.variables or {})} variables",
                "old": f"{len(existing.variables or {})} variables",
            })

        preview = ToolApprovalPreview(
            action="Edit prompt",
            target=existing.display_name,
            kind="text_edit",
            summary=_truncate(new_text),
            fields=fields,
            text_blocks=[text_block],
        )
        return self._proposal(preview, {
            "prompt_id": prompt_id,
            "old": {"name": existing.name, "segments": [s.model_dump() for s in existing.segments]},
            "new": request.model_dump(),
        }, "The complete Prompt aggregate will be replaced. Please confirm.")

    async def _apply(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        prompt_id = kwargs.get("prompt_id")
        existing = _require_prompt(context, prompt_id)
        saved = await operations.replace_prompt(
            context.prompt_database, context.user_id, prompt_id, _prompt_request(existing, **kwargs),
        )
        payload = {"action": "edit_prompt", "success": bool(saved), "prompt_id": prompt_id}
        if saved:
            payload["name"] = saved.display_name
            payload["message"] = f'Updated the prompt "{saved.display_name}" in the library.'
        return ToolResult(
            success=bool(saved), data=json.dumps(payload), error=None if saved else "Prompt could not be updated"
        )


class DeletePromptTool(_PromptWriteTool):
    icon = "trash-2"

    name = "delete_prompt"
    mcp = True
    user_description = "Removes a saved prompt from your library."

    @property
    def description(self): return "Delete one detached Prompt aggregate from the user's library."

    @property
    def parameters(self):
        return {"type": "object", "properties": {"prompt_id": {"type": "string"}}, "required": ["prompt_id"]}

    def _preview(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        prompt_id = kwargs.get("prompt_id")
        existing = _require_prompt(context, prompt_id)
        preview = ToolApprovalPreview(
            action="Delete prompt",
            target=existing.display_name,
            summary=_truncate(existing.flattened_text),
        )
        return self._proposal(
            preview,
            {"prompt_id": prompt_id, "name": existing.display_name, "preview": existing.flattened_text[:200]},
            "This Prompt will be permanently deleted. Please confirm.",
        )

    def _apply(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        prompt_id = kwargs.get("prompt_id")
        _require_prompt(context, prompt_id)
        success = operations.delete_prompt(context.prompt_database, context.user_id, prompt_id)
        return ToolResult(success=success, data=json.dumps({
            "action": "delete_prompt", "success": success, "prompt_id": prompt_id,
        }), error=None if success else f"Prompt '{prompt_id}' not found")
