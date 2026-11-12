import asyncio
from pathlib import PurePosixPath
from typing import Any, Dict

from pydantic import ValidationError

from src.features.generation.exceptions import GenerationDeleteFailedException, GenerationNotFoundException
from src.features.inspirations.dto import PublishInspirationRequest
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.domain_support import (
    DomainTool,
    acting_user,
    bounded_int,
    failure,
    payload,
    required_text,
    run_controller,
)

FEED_DEFAULT = 20
FEED_MAX = 50


class DeleteGenerationTool(BaseTool):
    modes = ["mcp"]
    mcp = True
    destructive = True
    group = "Generation"
    context_group = "library"
    icon = "trash"
    name = "delete_generation"
    user_description = "Permanently deletes one of your generations and its files."

    @property
    def description(self) -> str:
        return (
            "Permanently delete one of the user's generations from history, with all its files. This cannot be "
            "undone. Only the user's own generations can be deleted."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"generation_id": {"type": "string", "description": "A generation id of the user's."}},
            "required": ["generation_id"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        generation_id = required_text(kwargs, "generation_id")
        if not generation_id:
            return failure("'generation_id' is required")
        facade = context.generation_history_facade
        if facade is None:
            return failure("Generation history is not available on this server.")
        try:
            result = await asyncio.to_thread(facade.delete, generation_id, context.user_id)
        except GenerationNotFoundException:
            return failure(f"No generation '{generation_id}' of yours.")
        except GenerationDeleteFailedException as exc:
            return failure(str(exc))
        return payload({
            "generation_id": generation_id,
            "deleted": True,
            "files_removed": result.get("files_deleted_fs", 0),
        })


class DeleteLibraryItemTool(DomainTool):
    domain = "library"
    destructive = True
    group = "Generation"
    context_group = "library"
    icon = "trash"
    name = "delete_library_item"
    user_description = "Permanently deletes one of your library files."

    @property
    def description(self) -> str:
        return (
            "Permanently delete one file from the user's library (an upload or saved file). This cannot be "
            "undone. 'item_id' is the id list_library_items gives."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {"item_id": {"type": "string", "description": "A library item id from list_library_items."}},
            "required": ["item_id"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        item_id = required_text(kwargs, "item_id")
        if not item_id:
            return failure("'item_id' is required")
        return await run_controller(self.service(context).delete_item(item_id, acting_user(context)))


class ListInspirationsTool(DomainTool):
    domain = "inspirations"
    group = "Sharing"
    context_group = "library"
    icon = "sparkles"
    name = "list_inspirations"
    read_only = True
    user_description = "Browses the shared Inspirations feed."

    @property
    def description(self) -> str:
        return (
            "List the shared Inspirations feed, newest first: pictures other users and the user published, with "
            "their titles. 'query' searches titles; 'saved': true shows the ones the user saved; "
            f"'limit' defaults to {FEED_DEFAULT} (at most {FEED_MAX}), 'offset' pages."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "saved": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": FEED_MAX, "default": FEED_DEFAULT},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": [],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        limit = bounded_int(kwargs, "limit", FEED_DEFAULT, 1, FEED_MAX)
        offset = bounded_int(kwargs, "offset", 0, 0, 10_000_000)
        saved = kwargs.get("saved")
        return await run_controller(self.service(context).list_feed(
            acting_user(context), kwargs.get("query") or None, limit, offset, None, None,
            bool(saved) if saved is not None else None, True, False,
        ))


class PublishInspirationTool(DomainTool):
    domain = "inspirations"
    group = "Sharing"
    context_group = "library"
    icon = "share-2"
    name = "publish_inspiration"
    user_description = "Shares pictures from one of your generations in the Inspirations feed."

    @property
    def description(self) -> str:
        return (
            "Share the output of one of the user's generations with the other users of this server by "
            "publishing it to the Inspirations feed, with the settings used. 'filenames' picks the output "
            "files (as named in get_generation_results); leave it out to share every final file."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "generation_id": {"type": "string"},
                "title": {"type": "string", "maxLength": 200},
                "description": {"type": "string"},
                "filenames": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "required": ["generation_id", "title"],
        }

    async def run(self, context: ToolContext, **kwargs) -> ToolResult:
        generation_id, title = required_text(kwargs, "generation_id"), required_text(kwargs, "title")
        if not generation_id or not title:
            return failure("'generation_id' and 'title' are required")
        filenames = kwargs.get("filenames")
        if not filenames:
            facade = context.generation_history_facade
            if facade is None:
                return failure("Generation history is not available on this server.")
            try:
                detail = await facade.get_by_id_async(generation_id, context.user_id, True, False)
            except GenerationNotFoundException:
                return failure(f"No generation '{generation_id}' of yours.")
            filenames = [
                PurePosixPath(file["file_path"]).name
                for file in detail.get("files") or []
                if file.get("is_final") and file.get("file_path")
            ]
        try:
            request = PublishInspirationRequest(
                generation_id=generation_id, filenames=filenames, title=title, description=kwargs.get("description"),
            )
        except ValidationError:
            return failure("The generation has no files to share, or the title is too long.")
        return await run_controller(self.service(context).publish(request, acting_user(context)))


LIBRARY_SOCIAL_TOOLS = (DeleteGenerationTool, DeleteLibraryItemTool, ListInspirationsTool, PublishInspirationTool)
