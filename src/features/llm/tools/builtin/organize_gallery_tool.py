"""Tags, rates and lists the user's generation history. Tag names are
resolved against the user's own GENERATION-type tags (created on first use);
rating/listing act on `context.generation_history_manager`, the same
collaborator the `/api/generations/history*` routes use.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.errors import teach, unexpected
from src.features.tags.dto import CreateTagRequest, TagType

logger = logging.getLogger(__name__)

_OPERATIONS = ("tag", "untag", "rate", "list_recent", "get")

# list_recent truncates the error summary so one bad row doesn't blow the
# tool-result budget; `get` returns it untruncated for real diagnosis.
_LIST_ERROR_MAX_LEN = 500

_TAGS_UNAVAILABLE = teach(
    "Tag management is not configured for this session",
    "'tag'/'untag' need the backend's tag manager wired in, which is not something you can fix",
    "tell the user tagging isn't available right now instead of retrying",
)


def _resolve_or_create_tag_id(tag_manager: Any, user_id: str, name: str) -> str:
    """The id of the user's GENERATION tag named `name`, creating it if it
    doesn't exist yet (mirrors how the Generate UI's tag picker behaves)."""
    existing = tag_manager.repository.get_tag_by_name(name, type=TagType.GENERATION.value, user_id=user_id)
    if existing:
        return existing.id
    created = tag_manager.create_tag(CreateTagRequest(name=name, type=TagType.GENERATION), user_id)
    return created.id


class OrganizeGalleryTool(BaseTool):
    """Tag, rate, and browse the user's generation history."""

    modes = ["generation", "history"]
    icon = "tags"

    @property
    def name(self) -> str:
        return "organize_gallery"

    @property
    def group(self) -> str:
        return "Generation"

    @property
    def user_description(self) -> str:
        return "Tags, rates, and lists your recent generations."

    @property
    def hint(self) -> str:
        return "When the user wants to tag, rate, or review their recent generations."

    @property
    def description(self) -> str:
        return (
            "Organize the user's generation history. `operation` selects what happens: 'tag'/'untag' "
            "need `generation_id` and `tags` (a list of tag names - unknown names are created on first "
            "use for 'tag'); 'rate' needs `generation_id` and `rating` (0-5, 0 clears it); 'list_recent' "
            "returns generations with their id, status, prompt, rating, tags, file paths and an `error` "
            "summary for failed ones (`limit`, default 20), optionally narrowed with `text` (prompt "
            "search), `preset_id`, `model_name`, `min_rating`, `created_from`/`created_to` (ISO 8601) - "
            "use this to find generations matching what the user described, or to get real generation "
            "ids/paths to act on instead of guessing them; 'get' needs `generation_id` and returns full "
            "detail for one generation (status, untruncated error, prompt, preset, created_at, paths, "
            "tags, rating) - use this to diagnose why a generation failed. Example: "
            '{"operation": "tag", "generation_id": "01ARZ...", "tags": ["favorite", "portrait"]}'
        )

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": list(_OPERATIONS)},
                "generation_id": {"type": "string", "description": "Required for tag/untag/rate/get."},
                "tags": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Tag names. Required for tag/untag.",
                },
                "rating": {"type": "integer", "minimum": 0, "maximum": 5, "description": "Required for rate; 0 clears the rating."},
                "limit": {"type": "integer", "description": "Max results for list_recent (default 20)."},
                "text": {"type": "string", "description": "list_recent only: free-text search over the prompt."},
                "preset_id": {"type": "string", "description": "list_recent only: filter to one preset id."},
                "model_name": {"type": "string", "description": "list_recent only: filter to generations that used this model."},
                "min_rating": {"type": "integer", "minimum": 0, "maximum": 5, "description": "list_recent only: minimum rating."},
                "created_from": {"type": "string", "description": "list_recent only: ISO 8601 lower bound on creation time."},
                "created_to": {"type": "string", "description": "list_recent only: ISO 8601 upper bound on creation time."},
            },
            "required": ["operation"],
        }

    @staticmethod
    def _missing(field: str) -> ToolResult:
        return ToolResult(success=False, data="", error=f"'{field}' is required for this operation")

    @staticmethod
    def _truncate_error(error_message: Optional[str]) -> Optional[str]:
        if not error_message or len(error_message) <= _LIST_ERROR_MAX_LEN:
            return error_message
        return "..." + error_message[-_LIST_ERROR_MAX_LEN:]

    @staticmethod
    def _list_recent(history_manager, user_id: str, limit: int, kwargs: Dict[str, Any]) -> ToolResult:
        history = history_manager.get_history(
            user_id=user_id, limit=limit, offset=0, sort_by="created_at", sort_dir="desc",
            search=kwargs.get("text") or None,
            preset_id=kwargs.get("preset_id") or None,
            model_name=kwargs.get("model_name") or None,
            min_rating=kwargs.get("min_rating"),
            created_from=kwargs.get("created_from") or None,
            created_to=kwargs.get("created_to") or None,
        )
        generations = []
        for gen in history.get("generations", []):
            form_data = gen.get("form_data") or {}
            generations.append({
                "id": gen.get("id"),
                "status": gen.get("status"),
                "created_at": gen.get("created_at"),
                "rating": gen.get("rating"),
                "is_favorite": gen.get("is_favorite"),
                "prompt": form_data.get("prompt"),
                "preset_name": gen.get("preset_name"),
                "tags": [t.get("name") for t in gen.get("tags", [])],
                "paths": [f.get("file_path") for f in gen.get("files", []) if f.get("file_path")],
                "error": OrganizeGalleryTool._truncate_error(gen.get("error_message")),
            })
        return ToolResult(success=True, data=json.dumps({"generations": generations, "total": history.get("total", len(generations))}))

    @staticmethod
    def _get(history_manager, generation_id: str, user_id: str) -> ToolResult:
        gen = history_manager.get_by_id(generation_id, user_id)
        form_data = gen.get("form_data") or {}
        detail = {
            "id": gen.get("id"),
            "status": gen.get("status"),
            "error": gen.get("error_message"),
            "prompt": form_data.get("prompt"),
            "preset_name": gen.get("preset_name"),
            "created_at": gen.get("created_at"),
            "started_at": gen.get("started_at"),
            "completed_at": gen.get("completed_at"),
            "rating": gen.get("rating"),
            "is_favorite": gen.get("is_favorite"),
            "tags": [t.get("name") for t in gen.get("tags", [])],
            "paths": [f.get("file_path") for f in gen.get("files", []) if f.get("file_path")],
        }
        return ToolResult(success=True, data=json.dumps(detail))

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Preview of the proposed action; performs no mutation. `list_recent`
        has no mutation to preview, so it returns the real listing directly."""
        history_manager = context.generation_history_manager
        if history_manager is None:
            return ToolResult(success=False, data="", error="Generation history not available")

        operation = kwargs.get("operation")
        if operation not in _OPERATIONS:
            return ToolResult(success=False, data="", error=f"'operation' must be one of {list(_OPERATIONS)}")

        try:
            if operation == "list_recent":
                limit = max(1, int(kwargs.get("limit") or 20))
                return self._list_recent(history_manager, context.user_id, limit, kwargs)

            generation_id = kwargs.get("generation_id")
            if not generation_id:
                return self._missing("generation_id")

            if operation == "get":
                return self._get(history_manager, generation_id, context.user_id)

            current_tags = history_manager.get_tags(generation_id, context.user_id)

            if operation in ("tag", "untag"):
                tags: List[str] = kwargs.get("tags") or []
                if not tags:
                    return self._missing("tags")
                verb = "Add" if operation == "tag" else "Remove"
                preview = ToolApprovalPreview(action=f"{verb} tags", items=tags, target=f"generation {generation_id}")
                return ToolResult(success=True, data=json.dumps({
                    "action": operation,
                    "proposal": {"generation_id": generation_id, "tags": tags, "current_tags": [t.get("name") for t in current_tags]},
                    "message": f"Tags {tags} will be {'added to' if operation == 'tag' else 'removed from'} this generation. Please confirm.",
                }), preview=preview)

            # rate
            if "rating" not in kwargs:
                return self._missing("rating")
            rating = kwargs["rating"]
            if not isinstance(rating, int) or isinstance(rating, bool) or rating < 0 or rating > 5:
                return ToolResult(success=False, data="", error="'rating' must be an integer between 0 and 5")
            preview = ToolApprovalPreview(action="Set rating", items=[str(rating)], target=f"generation {generation_id}")
            return ToolResult(success=True, data=json.dumps({
                "action": "rate",
                "proposal": {"generation_id": generation_id, "rating": rating},
                "message": f"Rating will be set to {rating}. Please confirm.",
            }), preview=preview)
        except ValueError as e:
            return ToolResult(success=False, data="", error=str(e))
        except Exception as e:
            logger.error(f"organize_gallery preview failed: {e}")
            return ToolResult(success=False, data="", error=f"Generation '{kwargs.get('generation_id')}' not found or access denied")

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        history_manager = context.generation_history_manager
        if history_manager is None:
            return ToolResult(success=False, data="", error="Generation history not available")

        operation = kwargs.get("operation")
        if operation not in _OPERATIONS:
            return ToolResult(success=False, data="", error=f"'operation' must be one of {list(_OPERATIONS)}")

        try:
            if operation == "list_recent":
                limit = max(1, int(kwargs.get("limit") or 20))
                return self._list_recent(history_manager, context.user_id, limit, kwargs)

            generation_id = kwargs.get("generation_id")
            if not generation_id:
                return self._missing("generation_id")

            if operation == "get":
                return self._get(history_manager, generation_id, context.user_id)

            if operation == "tag":
                tags: List[str] = kwargs.get("tags") or []
                if not tags:
                    return self._missing("tags")
                if context.tag_manager is None:
                    return ToolResult(success=False, data="", error=_TAGS_UNAVAILABLE)
                current = {t["id"] for t in history_manager.get_tags(generation_id, context.user_id)}
                new_ids = {_resolve_or_create_tag_id(context.tag_manager, context.user_id, name) for name in tags}
                updated = history_manager.update_tags(generation_id, list(current | new_ids), context.user_id)
                return ToolResult(success=True, data=json.dumps({
                    "action": "tag", "success": True, "generation_id": generation_id,
                    "tags": [t.get("name") for t in updated],
                }))

            if operation == "untag":
                tags: List[str] = kwargs.get("tags") or []
                if not tags:
                    return self._missing("tags")
                if context.tag_manager is None:
                    return ToolResult(success=False, data="", error=_TAGS_UNAVAILABLE)
                removed, skipped = [], []
                for name in tags:
                    existing = context.tag_manager.repository.get_tag_by_name(
                        name, type=TagType.GENERATION.value, user_id=context.user_id,
                    )
                    if existing and history_manager.remove_tag(generation_id, existing.id, context.user_id):
                        removed.append(name)
                    else:
                        skipped.append(name)
                return ToolResult(success=True, data=json.dumps({
                    "action": "untag", "success": True, "generation_id": generation_id,
                    "removed": removed, "skipped": skipped,
                }))

            # rate
            if "rating" not in kwargs:
                return self._missing("rating")
            rating = kwargs["rating"]
            if not isinstance(rating, int) or isinstance(rating, bool) or rating < 0 or rating > 5:
                return ToolResult(success=False, data="", error="'rating' must be an integer between 0 and 5")
            value = history_manager.set_rating(generation_id, rating, context.user_id)
            return ToolResult(success=True, data=json.dumps({
                "action": "rate", "success": True, "generation_id": generation_id, "rating": value,
            }))
        except ValueError as e:
            return ToolResult(success=False, data="", error=str(e))
        except Exception as e:
            logger.error(f"organize_gallery failed: {e}")
            return ToolResult(success=False, data="", error=unexpected("organize_gallery", operation, e))
