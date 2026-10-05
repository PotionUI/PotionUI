import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from src.features.library import operations as library_operations
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import viewer_is_restricted
from src.features.llm.tools.errors import unexpected

logger = logging.getLogger(__name__)

DEFAULT_LIBRARY_LIMIT = 50
MAX_LIBRARY_LIMIT = 100
LIBRARY_MEDIA_TYPES = ("image", "video", "audio")


def _upload_path(filename: str) -> str:
    return f"uploads/{filename}"


def _content_states(context: ToolContext, paths: List[str]) -> Optional[Dict[str, str]]:
    if not viewer_is_restricted(context):
        return None
    try:
        return context.content_safety.ledger.states(set(paths)) or {}
    except Exception:
        logger.warning("could not read content states for library items", exc_info=True)
        return {}


class ListLibraryItemsTool(BaseTool):
    modes = ["generation", "history"]
    icon = "folder-open"

    @property
    def name(self) -> str:
        return "list_library_items"

    @property
    def group(self) -> str:
        return "Generation"

    @property
    def user_description(self) -> str:
        return "Lists the files in your library, such as uploads."

    @property
    def hint(self) -> str:
        return (
            "Use to find library item ids (uploads) before manage_collections with scope "
            "'library', or when the user asks about files they uploaded."
        )

    @property
    def description(self) -> str:
        return (
            "List the user's library items (uploaded and saved files, not generations), newest "
            "first. Each item has id (the upload id manage_collections takes as upload_ids with "
            "scope 'library'), file name, media_type, size, dimensions, path (pass it verbatim to "
            "a media form field) and the library collections it is in. Optional 'search' matches "
            "file names, 'media_type' is image, video or audio, 'collection_id' (from "
            "manage_collections list with scope 'library') narrows to one collection. Paged with "
            f"'offset'/'limit' (default {DEFAULT_LIBRARY_LIMIT}, max {MAX_LIBRARY_LIMIT})."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Match against file names."},
                "media_type": {"type": "string", "enum": list(LIBRARY_MEDIA_TYPES)},
                "collection_id": {"type": "string", "description": "Only items in this library collection."},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIBRARY_LIMIT, "default": DEFAULT_LIBRARY_LIMIT},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if context.library_collaborators is None:
            return ToolResult(success=False, data="", error="The library is not available on this server.")
        try:
            raw_limit = kwargs.get("limit")
            limit = DEFAULT_LIBRARY_LIMIT if raw_limit in (None, "") else int(raw_limit)
            offset = max(0, int(kwargs.get("offset") or 0))
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'limit' and 'offset' must be integers.")
        limit = min(max(1, limit), MAX_LIBRARY_LIMIT)
        media_type = kwargs.get("media_type") or None
        if media_type and media_type not in LIBRARY_MEDIA_TYPES:
            return ToolResult(
                success=False, data="", error=f"'media_type' must be one of {', '.join(LIBRARY_MEDIA_TYPES)}.",
            )
        try:
            page = await asyncio.to_thread(
                library_operations.list_items,
                context.library_collaborators,
                context.user_id,
                media_type=media_type,
                collection_id=kwargs.get("collection_id") or None,
                search=kwargs.get("search") or None,
                limit=limit,
                offset=offset,
            )
        except ValueError as e:
            return ToolResult(success=False, data="", error=str(e))
        except Exception as e:
            logger.error("list_library_items failed: %s", e)
            return ToolResult(success=False, data="", error=unexpected("list_library_items", "list", e))

        items = page.items
        collections = {}
        if context.collection_repository is not None and items:
            try:
                collections = context.collection_repository.get_for_uploads([i.id for i in items], context.user_id)
            except Exception:
                logger.warning("could not read library collections", exc_info=True)
        states = _content_states(context, [_upload_path(i.filename) for i in items])

        entries = []
        hidden = 0
        withheld = 0
        for item in items:
            state = None if states is None else states.get(_upload_path(item.filename))
            if states is not None and state == "flagged":
                hidden += 1
                continue
            if states is not None and state != "safe":
                withheld += 1
                entries.append({"id": item.id, "media_type": item.media_type, "content_state": "unrated"})
                continue
            entry = {
                "id": item.id,
                "file_name": item.original_filename or item.filename,
                "media_type": item.media_type,
                "mime_type": item.mime_type,
                "size": item.size,
                "width": item.width,
                "height": item.height,
                "duration_seconds": item.duration_seconds,
                "path": _upload_path(item.filename),
                "created_at": item.created_at,
                "is_favorite": item.is_favorite,
                "collections": [{"id": c.id, "name": c.name} for c in collections.get(item.id, [])],
            }
            entries.append({k: v for k, v in entry.items() if v is not None})

        payload: Dict[str, Any] = {
            "items": entries,
            "total": page.total,
            "offset": page.offset,
            "limit": page.limit,
            "has_more": page.offset + len(items) < page.total,
        }
        if hidden or withheld:
            payload["note"] = (
                f"{hidden} item(s) are hidden and {withheld} are shown without details because they "
                "have not been rated safe for this account."
            )
        if not page.total:
            payload["message"] = "No library items matched." if kwargs.get("search") or media_type else "The library is empty."
        return ToolResult(success=True, data=json.dumps(payload))
