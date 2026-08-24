"""Phrasebook tools for accessing prompt phrasebook data."""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.features.phrasebook.dto import PhrasebookCategoryRequest, PhrasebookValueRequest
from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_PATH_SEGMENT_RE = re.compile(r'^[A-Za-z0-9_]+( [A-Za-z0-9_]+)*$')


def _format_marker(path: str) -> str:
    """Encode a category/value path as a `#` marker string.

    Mirrors the frontend's `encodePathForText` in richTextUtils.ts:
    bracketed `#[path]` when the path contains spaces, plain `#path` otherwise.
    """
    return f"#[{path}]" if " " in path else f"#{path}"


def _validate_category_path(path: str) -> Optional[str]:
    """Validate a dot-separated category path. Returns an error message, or None if valid."""
    if not path:
        return "'path' is required."
    if path != path.strip():
        return "path must not have leading or trailing whitespace."
    if path.startswith(".") or path.endswith(".") or ".." in path:
        return "path must not start/end with a dot or contain consecutive dots."
    for segment in path.split("."):
        if not _PATH_SEGMENT_RE.match(segment):
            return (
                f"invalid path segment '{segment}'. Each dot-separated segment may only "
                "contain letters, digits, underscores, and single spaces between words."
            )
    return None


def _parent_path(path: str) -> Optional[str]:
    return path.rsplit(".", 1)[0] if "." in path else None


DEFAULT_LIST_LIMIT = 100
MAX_LIST_LIMIT = 200


def _resolve_category_by_id_or_path(context: ToolContext, category_arg: str) -> Tuple[Optional[Any], Optional[str]]:
    """Resolve a category from a single arg that may be an id or a dot-separated path."""
    try:
        category = context.phrasebook_manager.get_category_by_id(
            category_id=category_arg, user_id=context.user_id
        )
        return category, None
    except ValueError:
        pass
    category = context.phrasebook_manager.categories.get_by_path(category_arg, context.user_id)
    if category:
        return category, None
    return None, f"Category '{category_arg}' not found."


class ListPhrasebookCategoriesTool(BaseTool):
    """Lists all phrasebook categories."""

    modes = ["generation", "phrasebook"]
    icon = "list-tree"

    @property
    def name(self) -> str:
        return "list_phrasebook_categories"

    @property
    def group(self) -> str:
        return "Phrasebook vocabulary"

    @property
    def user_description(self) -> str:
        return "Lists your phrasebook vocabulary categories."

    @property
    def hint(self) -> str:
        return (
            "When helping with prompts, call this to discover what model-specific "
            "values are available (art styles, camera angles, lighting, etc.). "
            "These values are what the model actually understands. Each category's "
            "'marker' string can be embedded directly in update_segment content."
        )

    @property
    def description(self) -> str:
        return (
            "List all phrasebook categories available to the user. "
            "Categories organize prompt helpers like art styles, camera angles, "
            "lighting types, emotions, etc. Use this to discover what "
            "phrasebook suggestions are available."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        try:
            categories = context.phrasebook_manager.categories.get_all(user_id=context.user_id)
            result = []
            for cat in categories:
                path = getattr(cat, 'path', '')
                entry = {
                    "id": cat.id,
                    "name": cat.name,
                    "path": path,
                    "description": getattr(cat, 'description', ''),
                    "is_active": getattr(cat, 'is_active', True),
                }
                if path:
                    entry["marker"] = _format_marker(path)
                result.append(entry)
            return ToolResult(
                success=True,
                data=json.dumps({"categories": result, "count": len(result)}),
            )
        except Exception as e:
            logger.error(f"Error listing phrasebook categories: {e}")
            return ToolResult(success=False, data="", error=str(e))


class GetPhrasebookValuesTool(BaseTool):
    """Gets phrasebook values for a specific category."""

    modes = ["generation", "phrasebook"]
    icon = "list"

    @property
    def name(self) -> str:
        return "get_phrasebook_values"

    @property
    def group(self) -> str:
        return "Phrasebook vocabulary"

    @property
    def user_description(self) -> str:
        return "Looks up the saved values in one phrasebook category."

    @property
    def hint(self) -> str:
        return (
            "When writing or improving prompts, use this to get model-specific "
            "values (e.g., exact camera angle names, art style keywords). "
            "Prefer these over generic suggestions — they produce better results. "
            "Returned 'marker' and 'category_marker' strings can be embedded "
            "directly in update_segment content. Categories can hold hundreds of "
            "values, so this only returns a page at a time (default 100, max 200) — "
            "pass 'search' when you know roughly what you're after (cheapest), or "
            "page with 'offset'/'limit' and check 'has_more' rather than assuming "
            "one call returned everything."
        )

    @property
    def description(self) -> str:
        return (
            "Get phrasebook values for a specific category, with optional "
            "server-side 'search' (case-insensitive substring match against label "
            "or value text) and 'offset'/'limit' pagination (limit defaults to 100, "
            "hard max 200). Returns the actual values (e.g., specific camera "
            "angles, art styles, or lighting types) that the model understands, "
            "each with a 'marker' string for embedding in prompts, plus 'total' "
            "and 'has_more' so you know whether to page further."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category_id": {
                    "type": "string",
                    "description": "The category ID to get values for.",
                },
                "search": {
                    "type": "string",
                    "description": "Optional case-insensitive substring match against each value's label or text.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Number of matching values to skip. Defaults to 0.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max values to return. Defaults to {DEFAULT_LIST_LIMIT}, hard max {MAX_LIST_LIMIT}.",
                },
            },
            "required": ["category_id"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        category_id = kwargs.get("category_id")
        if not category_id:
            return ToolResult(success=False, data="", error="category_id is required")

        search = (kwargs.get("search") or "").strip().lower()
        try:
            offset = max(0, int(kwargs.get("offset") or 0))
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'offset' must be an integer.")
        try:
            raw_limit = kwargs.get("limit")
            limit = DEFAULT_LIST_LIMIT if raw_limit in (None, "") else int(raw_limit)
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'limit' must be an integer.")
        limit = min(max(1, limit), MAX_LIST_LIMIT)

        try:
            values = context.phrasebook_manager.values.get_by_category(
                category_id=category_id,
                user_id=context.user_id,
            )
            if search:
                values = [
                    v for v in values
                    if search in (getattr(v, 'label', '') or '').lower()
                    or search in (getattr(v, 'value', '') or '').lower()
                ]

            total = len(values)
            page = values[offset:offset + limit]

            category_path = ""
            try:
                category = context.phrasebook_manager.get_category_by_id(
                    category_id=category_id, user_id=context.user_id
                )
                resolved_path = getattr(category, 'path', '') or ''
                if isinstance(resolved_path, str):
                    category_path = resolved_path
            except Exception as e:
                logger.debug(f"Could not resolve category path for markers: {e}")

            result = []
            for val in page:
                label = getattr(val, 'label', '')
                entry = {
                    "id": val.id,
                    "label": label,
                    "value": getattr(val, 'value', ''),
                }
                if category_path and label:
                    entry["marker"] = _format_marker(f"{category_path}.{label}")
                result.append(entry)

            payload: Dict[str, Any] = {
                "values": result,
                "count": len(result),
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(page) < total,
            }
            if category_path:
                payload["category_path"] = category_path
                payload["category_marker"] = _format_marker(category_path)
                payload["instruction"] = (
                    "These markers can be inserted verbatim into update_segment content. "
                    "category_marker references the whole category (the editor chip shuffles "
                    "a value per generation); a value's marker pins that specific value. "
                    "Use the bracketed #[...] form exactly as given. Leave a space between a "
                    "marker and any following punctuation."
                )

            return ToolResult(
                success=True,
                data=json.dumps(payload),
            )
        except Exception as e:
            logger.error(f"Error getting phrasebook values: {e}")
            return ToolResult(success=False, data="", error=str(e))


class ListPhrasebookValuesTool(BaseTool):
    """Searches and pages through a category's values without dumping it all into context."""

    modes = ["generation", "phrasebook"]
    icon = "search"

    @property
    def name(self) -> str:
        return "list_phrasebook_values"

    @property
    def group(self) -> str:
        return "Phrasebook vocabulary"

    @property
    def user_description(self) -> str:
        return "Browses or searches the values in an phrasebook category."

    @property
    def hint(self) -> str:
        return (
            "Use this to browse or search a category's values — categories can hold hundreds "
            "of entries, so never assume an attached phrasebook resource or a single call "
            "here shows you everything. For a literal ask (e.g. text containing 'cat'), pass "
            "'search' — it's the cheapest way to find matches. For a broader semantic ask (e.g. "
            "\"anything about cats\", which should also catch 'kitten' or 'feline' that a plain "
            "substring search misses), page through with 'offset'/'limit' (default 100, max 200 "
            "per call) and read each page's text yourself, accumulating the ids that match as "
            "you go, until 'has_more' is false. Once you've collected every matching id, make "
            "ONE remove_phrasebook_values call with all of them — never call it repeatedly, "
            "and never request an entire large category in a single call."
        )

    @property
    def description(self) -> str:
        return (
            "List a category's phrasebook values with optional server-side 'search' (case-"
            "insensitive substring match against label or value text) and 'offset'/'limit' "
            "pagination (limit defaults to 100, hard max 200). Returns a compact id/text/active "
            "entry per value plus 'total' and 'has_more' so you know whether to page further. "
            "'category' accepts either a category id or a dot-separated path."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category id or dot-separated path, e.g. 'camera.angles'.",
                },
                "search": {
                    "type": "string",
                    "description": "Optional case-insensitive substring match against each value's label or text.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Number of matching values to skip. Defaults to 0.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Max values to return. Defaults to {DEFAULT_LIST_LIMIT}, hard max {MAX_LIST_LIMIT}.",
                },
            },
            "required": ["category"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        category_arg = (kwargs.get("category") or "").strip()
        if not category_arg:
            return ToolResult(success=False, data="", error="'category' is required.")

        search = (kwargs.get("search") or "").strip().lower()
        try:
            offset = max(0, int(kwargs.get("offset") or 0))
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'offset' must be an integer.")
        try:
            raw_limit = kwargs.get("limit")
            limit = DEFAULT_LIST_LIMIT if raw_limit in (None, "") else int(raw_limit)
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'limit' must be an integer.")
        limit = min(max(1, limit), MAX_LIST_LIMIT)

        try:
            category, error = _resolve_category_by_id_or_path(context, category_arg)
            if error:
                return ToolResult(success=False, data="", error=error)

            all_values = context.phrasebook_manager.values.get_by_category(category.id, context.user_id)
            if search:
                all_values = [
                    v for v in all_values
                    if search in (v.label or "").lower() or search in (v.value or "").lower()
                ]

            total = len(all_values)
            page = all_values[offset:offset + limit]

            payload: Dict[str, Any] = {
                "category_path": getattr(category, "path", ""),
                "category_id": category.id,
                "total": total,
                "offset": offset,
                "limit": limit,
                "returned": len(page),
                "has_more": offset + len(page) < total,
                "values": [
                    {
                        "id": v.id,
                        "text": f"{v.label}: {v.value}",
                        "active": bool(getattr(v, "is_active", True)),
                    }
                    for v in page
                ],
            }
            return ToolResult(success=True, data=json.dumps(payload))
        except Exception as e:
            logger.error(f"Error listing phrasebook values: {e}")
            return ToolResult(success=False, data="", error=str(e))


class CreatePhrasebookCategoryTool(BaseTool):
    """Creates a new phrasebook category. Requires user approval."""

    modes = ["generation", "phrasebook"]
    icon = "folder-plus"

    @property
    def name(self) -> str:
        return "create_phrasebook_category"

    @property
    def group(self) -> str:
        return "Phrasebook vocabulary"

    @property
    def user_description(self) -> str:
        return "Creates a new phrasebook category."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "Use this when the user wants to save a new kind of prompt vocabulary as an "
            "phrasebook category — e.g. \"save these as phrasebook options\" or when a "
            "useful category (camera angles, art styles, etc.) doesn't exist yet. If the "
            "path has a parent (e.g. 'camera.angles' under 'camera'), the parent must "
            "already exist — create it first. The user approves before anything is created."
        )

    @property
    def description(self) -> str:
        return (
            "Create a new phrasebook category at the given dot-separated path (e.g. "
            "'camera.angles'). If the path has a parent segment, that parent category "
            "must already exist. Requires user approval before the category is actually "
            "created."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Dot-separated category path, e.g. 'camera.angles'. Each segment "
                        "may contain letters, digits, underscores, and single spaces."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "Display name for the category. Defaults to the last path segment.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional description of the category.",
                },
            },
            "required": ["path"],
        }

    def _resolve_parent(self, context: ToolContext, path: str) -> Tuple[Optional[str], Optional[str]]:
        """Resolve the parent category id for `path`. Returns (parent_id, error)."""
        parent_path = _parent_path(path)
        if not parent_path:
            return None, None
        parent = context.phrasebook_manager.categories.get_by_path(parent_path, context.user_id)
        if not parent:
            return None, (
                f"Parent category '{parent_path}' does not exist. Create it first with "
                "create_phrasebook_category."
            )
        return parent.id, None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        path = (kwargs.get("path") or "").strip()
        validation_error = _validate_category_path(path)
        if validation_error:
            return ToolResult(success=False, data="", error=validation_error)

        name = (kwargs.get("name") or "").strip() or path.rsplit(".", 1)[-1]
        description = kwargs.get("description") or ""

        try:
            existing = context.phrasebook_manager.categories.get_by_path(path, context.user_id)
            if existing:
                return ToolResult(
                    success=False, data="",
                    error=f"A category with path '{path}' already exists (id: {existing.id}).",
                )

            _, parent_error = self._resolve_parent(context, path)
            if parent_error:
                return ToolResult(success=False, data="", error=parent_error)

            result = {
                "status": "pending_approval",
                "path": path,
                "name": name,
                "description": description,
                "parent_path": _parent_path(path),
                "marker": _format_marker(path),
            }
            preview = ToolApprovalPreview(
                action="Create category",
                target=f"under {_parent_path(path)}" if _parent_path(path) else None,
                items=[path],
                note=description or None,
            )
            return ToolResult(success=True, data=json.dumps(result), preview=preview)
        except Exception as e:
            logger.error(f"Error validating phrasebook category creation: {e}")
            return ToolResult(success=False, data="", error=str(e))

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        path = (kwargs.get("path") or "").strip()
        name = (kwargs.get("name") or "").strip() or path.rsplit(".", 1)[-1]
        description = kwargs.get("description") or ""

        try:
            parent_id, parent_error = self._resolve_parent(context, path)
            if parent_error:
                return ToolResult(success=False, data="", error=parent_error)

            request = PhrasebookCategoryRequest(
                name=name, path=path, parent_id=parent_id, description=description,
            )
            category = context.phrasebook_manager.create_category(request, context.user_id)
            return ToolResult(
                success=True,
                data=json.dumps({
                    "created": True,
                    "category": {"id": category.id, "name": category.name, "path": category.path},
                    "marker": _format_marker(category.path),
                }),
            )
        except ValueError as e:
            return ToolResult(success=False, data="", error=str(e))
        except Exception as e:
            logger.error(f"Error creating phrasebook category: {e}")
            return ToolResult(success=False, data="", error=str(e))


class RemovePhrasebookValuesTool(BaseTool):
    """Deletes phrasebook values by exact id. Requires user approval."""

    modes = ["generation", "phrasebook"]
    icon = "list-minus"

    @property
    def name(self) -> str:
        return "remove_phrasebook_values"

    @property
    def group(self) -> str:
        return "Phrasebook vocabulary"

    @property
    def user_description(self) -> str:
        return "Removes values from your phrasebook vocabulary."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "Use this to delete phrasebook values the user asked to remove — e.g. "
            "\"remove all values about cats\" or \"clean up my camera angles list\". Find every "
            "matching id first with list_phrasebook_values (search, or page through with "
            "offset/limit for semantic asks that a plain search would miss) — an attached "
            "phrasebook resource only shows a small sample, not the whole category, so don't "
            "assume it's complete. Once you've accumulated all the matching ids, call this ONCE "
            "with the exact value_ids you picked. Never guess or invent ids, and never pass a "
            "label/text in place of an id. The user approves before anything is deleted."
        )

    @property
    def description(self) -> str:
        return (
            "Delete one or more phrasebook values, identified by exact 'value_ids' obtained "
            "from list_phrasebook_values or an attached phrasebook resource. Optionally "
            "pass 'category_path' or 'category_id' as a safety check — value_ids that don't "
            "belong to that category are rejected instead of deleted. Requires user approval "
            "before values are actually removed."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "value_ids": {
                    "type": "array",
                    "description": "Exact value ids to delete. Never free-text labels.",
                    "items": {"type": "string"},
                },
                "category_path": {
                    "type": "string",
                    "description": (
                        "Optional dot-separated category path. When given, only value_ids "
                        "belonging to this category are deleted; the rest are reported as "
                        "failed."
                    ),
                },
                "category_id": {
                    "type": "string",
                    "description": "Optional category id. Prefer category_path when possible.",
                },
            },
            "required": ["value_ids"],
        }

    def _resolve_category_scope(
        self, context: ToolContext, kwargs: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[str]]:
        """Resolve an optional category scope. Returns (category_id, error)."""
        category_id = kwargs.get("category_id")
        category_path = kwargs.get("category_path")

        if category_id:
            return category_id, None
        if category_path:
            category = context.phrasebook_manager.categories.get_by_path(category_path, context.user_id)
            if not category:
                return None, f"Category '{category_path}' not found."
            return category.id, None
        return None, None

    def _collect_targets(
        self, context: ToolContext, value_ids: List[str], scope_category_id: Optional[str]
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Resolve each value id to a preview entry. Returns (found, problems)."""
        found: List[Dict[str, str]] = []
        problems: List[Dict[str, str]] = []
        for vid in value_ids:
            try:
                value = context.phrasebook_manager.get_value_by_id(vid, context.user_id)
            except ValueError:
                problems.append({"id": vid, "error": "not found"})
                continue
            if scope_category_id and value.category_id != scope_category_id:
                problems.append({"id": vid, "error": "does not belong to the given category"})
                continue
            found.append({"id": value.id, "label": value.label, "value": value.value})
        return found, problems

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        value_ids = kwargs.get("value_ids")
        if not value_ids:
            return ToolResult(success=False, data="", error="'value_ids' is required and must be a non-empty array.")

        try:
            scope_category_id, scope_error = self._resolve_category_scope(context, kwargs)
            if scope_error:
                return ToolResult(success=False, data="", error=scope_error)

            found, problems = self._collect_targets(context, value_ids, scope_category_id)
            if not found:
                return ToolResult(
                    success=False, data="",
                    error=f"None of the given value_ids could be deleted: {problems}",
                )

            result: Dict[str, Any] = {
                "status": "pending_approval",
                "count": len(found),
                "values": found,
            }
            if problems:
                result["skipped"] = problems

            target_path = self._scope_path(context, kwargs)
            preview = ToolApprovalPreview(
                action="Remove",
                target=f"from category {target_path}" if target_path else None,
                items=[entry["label"] for entry in found],
                note=(
                    f"{len(problems)} id(s) skipped (not found or out of scope)"
                    if problems else None
                ),
            )
            return ToolResult(success=True, data=json.dumps(result), preview=preview)
        except Exception as e:
            logger.error(f"Error validating phrasebook value removal: {e}")
            return ToolResult(success=False, data="", error=str(e))

    def _scope_path(self, context: ToolContext, kwargs: Dict[str, Any]) -> Optional[str]:
        """Best-effort dot-path of the scope category for the approval preview."""
        category_path = kwargs.get("category_path")
        if category_path:
            return category_path
        category_id = kwargs.get("category_id")
        if category_id:
            try:
                category = context.phrasebook_manager.get_category_by_id(
                    category_id=category_id, user_id=context.user_id
                )
                return getattr(category, "path", None) or None
            except Exception:
                return None
        return None

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        value_ids = kwargs.get("value_ids") or []

        try:
            scope_category_id, scope_error = self._resolve_category_scope(context, kwargs)
            if scope_error:
                return ToolResult(success=False, data="", error=scope_error)

            found, problems = self._collect_targets(context, value_ids, scope_category_id)

            deleted = []
            failed = list(problems)
            for entry in found:
                try:
                    context.phrasebook_manager.delete_value(entry["id"], context.user_id)
                    deleted.append(entry)
                except ValueError as e:
                    failed.append({"id": entry["id"], "error": str(e)})

            if not deleted and failed:
                return ToolResult(
                    success=False, data="",
                    error=f"Failed to delete any values: {failed[0]['error']}",
                )

            payload: Dict[str, Any] = {"deleted_count": len(deleted), "values": deleted}
            if failed:
                payload["failed"] = failed
            return ToolResult(success=True, data=json.dumps(payload))
        except Exception as e:
            logger.error(f"Error removing phrasebook values: {e}")
            return ToolResult(success=False, data="", error=str(e))


def _describe_edit(edit: Dict[str, str]) -> str:
    """Render one resolved edit as a human-readable preview line."""
    old_label, new_label = edit["old_label"], edit["new_label"]
    old_value, new_value = edit["old_value"], edit["new_value"]
    label_changed = new_label != old_label
    value_changed = new_value != old_value
    if label_changed and value_changed:
        return f"Label: '{old_label}' → '{new_label}'; value: '{new_value}'"
    if label_changed:
        return f"Label: '{old_label}' → '{new_label}'"
    if value_changed:
        return f"{old_label} — value: '{new_value}'"
    return old_label


class UpdatePhrasebookValuesTool(BaseTool):
    """Edits label/value text of one or more existing phrasebook values. Requires user approval."""

    modes = ["generation", "phrasebook"]
    icon = "list-pen"

    @property
    def name(self) -> str:
        return "update_phrasebook_values"

    @property
    def group(self) -> str:
        return "Phrasebook vocabulary"

    @property
    def user_description(self) -> str:
        return "Edits existing values in your phrasebook vocabulary."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "Use this to rename a value's label or change the prompt text it inserts — e.g. "
            "\"rename 'Wide' to 'Wide angle'\" or \"change what 'Golden hour' actually inserts\". "
            "Get exact ids first with list_phrasebook_values or get_phrasebook_values — never "
            "guess or invent them. Batch every edit you intend to make into ONE call rather than "
            "calling this repeatedly. The user approves before anything is changed."
        )

    @property
    def description(self) -> str:
        return (
            "Edit one or more existing phrasebook values in a category, identified by "
            "'category' (id or dot-separated path) and 'edits' — each with an exact 'id' "
            "(from list_phrasebook_values or get_phrasebook_values) and at least one of "
            "'new_label' or 'new_value'. Renaming onto a label that already exists elsewhere "
            "in the category is skipped and reported, not applied. Requires user approval "
            "before values are actually changed."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category id or dot-separated path, e.g. 'camera.angles'.",
                },
                "edits": {
                    "type": "array",
                    "description": "Edits to apply. Never invent an id — get them from list_phrasebook_values/get_phrasebook_values.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Exact value id to edit."},
                            "new_label": {"type": "string", "description": "New display label."},
                            "new_value": {
                                "type": "string",
                                "description": "New prompt text this value inserts.",
                            },
                        },
                        "required": ["id"],
                    },
                },
            },
            "required": ["category", "edits"],
        }

    def _resolve_edits(
        self, context: ToolContext, category: Any, edits_in: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Resolve each requested edit against current values. Returns (resolved, problems)."""
        existing_values = context.phrasebook_manager.values.get_by_category(
            category_id=category.id, user_id=context.user_id,
        )
        label_owner = {
            (getattr(v, 'label', '') or '').strip().lower(): v.id for v in existing_values
        }

        resolved: List[Dict[str, str]] = []
        problems: List[Dict[str, str]] = []
        for edit in edits_in:
            vid = edit.get("id")
            try:
                value = context.phrasebook_manager.get_value_by_id(vid, context.user_id)
            except ValueError:
                problems.append({"id": vid, "error": "not found"})
                continue
            if value.category_id != category.id:
                problems.append({"id": vid, "error": "does not belong to the given category"})
                continue

            old_label = value.label
            old_value = value.value
            new_label = (edit.get("new_label") or "").strip() or old_label
            new_value = (edit.get("new_value") or "").strip() or old_value

            if new_label.strip().lower() != old_label.strip().lower():
                owner = label_owner.get(new_label.strip().lower())
                if owner and owner != vid:
                    problems.append({
                        "id": vid,
                        "error": f"'{new_label}' already exists in this category and will be skipped.",
                    })
                    continue

            label_owner.pop(old_label.strip().lower(), None)
            label_owner[new_label.strip().lower()] = vid

            resolved.append({
                "id": vid,
                "old_label": old_label,
                "new_label": new_label,
                "old_value": old_value,
                "new_value": new_value,
                "sort_order": getattr(value, "sort_order", 0),
            })
        return resolved, problems

    def _validate_edits(self, edits_in: Any) -> Optional[str]:
        if not edits_in:
            return "'edits' is required and must be a non-empty array."
        for i, edit in enumerate(edits_in):
            if not (edit.get("id") or "").strip():
                return f"edits[{i}] is missing a non-empty 'id'."
            if not (edit.get("new_label") or "").strip() and not (edit.get("new_value") or "").strip():
                return f"edits[{i}] must include at least one of 'new_label' or 'new_value'."
        return None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        category_arg = (kwargs.get("category") or "").strip()
        if not category_arg:
            return ToolResult(success=False, data="", error="'category' is required.")

        edits_in = kwargs.get("edits")
        validation_error = self._validate_edits(edits_in)
        if validation_error:
            return ToolResult(success=False, data="", error=validation_error)

        try:
            category, error = _resolve_category_by_id_or_path(context, category_arg)
            if error:
                return ToolResult(success=False, data="", error=error)

            resolved, problems = self._resolve_edits(context, category, edits_in)
            if not resolved:
                return ToolResult(
                    success=False, data="",
                    error=f"None of the given edits could be applied: {problems}",
                )

            category_path = getattr(category, 'path', '')
            result: Dict[str, Any] = {
                "status": "pending_approval",
                "category_path": category_path,
                "category_id": category.id,
                "count": len(resolved),
                "edits": resolved,
            }
            if problems:
                result["skipped"] = problems

            preview = ToolApprovalPreview(
                action="Edit values",
                target=f"in category {category_path}" if category_path else None,
                items=[_describe_edit(e) for e in resolved],
                note=(
                    f"{len(problems)} id(s) skipped (not found, out of scope, or label conflict)"
                    if problems else None
                ),
            )
            return ToolResult(success=True, data=json.dumps(result), preview=preview)
        except Exception as e:
            logger.error(f"Error validating phrasebook value update: {e}")
            return ToolResult(success=False, data="", error=str(e))

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        category_arg = (kwargs.get("category") or "").strip()
        edits_in = kwargs.get("edits") or []

        try:
            category, error = _resolve_category_by_id_or_path(context, category_arg)
            if error:
                return ToolResult(success=False, data="", error=error)

            resolved, problems = self._resolve_edits(context, category, edits_in)
            category_path = getattr(category, 'path', '')

            updated = []
            failed = list(problems)
            for edit in resolved:
                try:
                    request = PhrasebookValueRequest(
                        category_id=category.id, label=edit["new_label"], value=edit["new_value"],
                        sort_order=edit["sort_order"],
                    )
                    updated_value = context.phrasebook_manager.update_value(
                        edit["id"], request, context.user_id
                    )
                    updated.append({
                        "id": updated_value.id,
                        "label": updated_value.label,
                        "value": updated_value.value,
                        "marker": _format_marker(f"{category_path}.{updated_value.label}"),
                    })
                except ValueError as e:
                    failed.append({"id": edit["id"], "error": str(e)})

            if not updated and failed:
                return ToolResult(
                    success=False, data="",
                    error=f"Failed to update any values: {failed[0]['error']}",
                )

            payload: Dict[str, Any] = {"updated_count": len(updated), "values": updated}
            if failed:
                payload["failed"] = failed
            return ToolResult(success=True, data=json.dumps(payload))
        except Exception as e:
            logger.error(f"Error updating phrasebook values: {e}")
            return ToolResult(success=False, data="", error=str(e))


class CreatePhrasebookValuesTool(BaseTool):
    """Creates one or more phrasebook values in an existing category. Requires user approval."""

    modes = ["generation", "phrasebook"]
    icon = "list-plus"

    @property
    def name(self) -> str:
        return "create_phrasebook_values"

    @property
    def group(self) -> str:
        return "Phrasebook vocabulary"

    @property
    def user_description(self) -> str:
        return "Adds new values to your phrasebook vocabulary."

    @property
    def requires_approval(self) -> bool:
        return True

    @property
    def hint(self) -> str:
        return (
            "Use this to add new concrete values to an existing phrasebook category — "
            "e.g. when the user wants to save specific camera angles, styles, or other "
            "reusable vocabulary."
            "{{#if create_phrasebook_category}} If the category doesn't exist yet, create it "
            "first with create_phrasebook_category.{{/if}}"
            " The user approves before anything is saved."
        )

    @property
    def description(self) -> str:
        return (
            "Create one or more phrasebook values in an existing category, identified by "
            "'category_path' (preferred) or 'category_id'. Each value needs a 'label' and "
            "optionally a distinct 'value' text (defaults to the label). Requires user "
            "approval before values are actually created."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category_path": {
                    "type": "string",
                    "description": "Dot-separated path of the category to add values to, e.g. 'camera.angles'.",
                },
                "category_id": {
                    "type": "string",
                    "description": "Category ID to add values to. Prefer category_path when possible.",
                },
                "values": {
                    "type": "array",
                    "description": "Values to create.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "Display label for the value."},
                            "value": {
                                "type": "string",
                                "description": "Prompt text this value inserts. Defaults to the label.",
                            },
                        },
                        "required": ["label"],
                    },
                },
            },
            "required": ["values"],
        }

    def _resolve_category(self, context: ToolContext, kwargs: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
        """Resolve the target category by id or path. Returns (category, error)."""
        category_id = kwargs.get("category_id")
        category_path = kwargs.get("category_path")

        if category_id:
            try:
                category = context.phrasebook_manager.get_category_by_id(
                    category_id=category_id, user_id=context.user_id
                )
                return category, None
            except Exception:
                return None, (
                    f"Category '{category_id}' not found. Use create_phrasebook_category "
                    "to create it first."
                )

        if category_path:
            category = context.phrasebook_manager.categories.get_by_path(category_path, context.user_id)
            if not category:
                return None, (
                    f"Category '{category_path}' not found. Use create_phrasebook_category "
                    "to create it first."
                )
            return category, None

        return None, "Either 'category_path' or 'category_id' is required."

    def _filter_new_values(
        self, context: ToolContext, category: Any, values_in: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, str]], List[str]]:
        """Exclude values whose label already exists in the category (case-insensitive)."""
        existing_values = context.phrasebook_manager.values.get_by_category(
            category_id=category.id, user_id=context.user_id,
        )
        existing_labels = {getattr(ev, 'label', '').strip().lower() for ev in existing_values}

        warnings: List[str] = []
        to_create: List[Dict[str, str]] = []
        for v in values_in:
            label = (v.get("label") or "").strip()
            if label.lower() in existing_labels:
                warnings.append(f"'{label}' already exists in this category and will be skipped.")
                continue
            to_create.append({"label": label, "value": (v.get("value") or label).strip()})
        return to_create, warnings

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        values_in = kwargs.get("values")
        if not values_in:
            return ToolResult(success=False, data="", error="'values' is required and must be a non-empty array.")

        for i, v in enumerate(values_in):
            if not (v.get("label") or "").strip():
                return ToolResult(success=False, data="", error=f"values[{i}] is missing a non-empty 'label'.")

        try:
            category, error = self._resolve_category(context, kwargs)
            if error:
                return ToolResult(success=False, data="", error=error)

            to_create, warnings = self._filter_new_values(context, category, values_in)

            category_path = getattr(category, 'path', '')
            result: Dict[str, Any] = {
                "status": "pending_approval",
                "category_path": category_path,
                "category_id": category.id,
                "count": len(to_create),
                "values": to_create,
            }
            if warnings:
                result["warnings"] = warnings
            preview = ToolApprovalPreview(
                action="Add values",
                target=f"to category {category_path}" if category_path else None,
                items=[v["label"] for v in to_create],
                note="; ".join(warnings) if warnings else None,
            )
            return ToolResult(success=True, data=json.dumps(result), preview=preview)
        except Exception as e:
            logger.error(f"Error validating phrasebook value creation: {e}")
            return ToolResult(success=False, data="", error=str(e))

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.phrasebook_manager:
            return ToolResult(success=False, data="", error="Phrasebook manager not available")

        values_in = kwargs.get("values") or []

        try:
            category, error = self._resolve_category(context, kwargs)
            if error:
                return ToolResult(success=False, data="", error=error)

            category_path = getattr(category, 'path', '')
            to_create, _ = self._filter_new_values(context, category, values_in)

            created = []
            failed = []
            for v in to_create:
                label = v["label"]
                value_text = v["value"]
                try:
                    request = PhrasebookValueRequest(
                        category_id=category.id, label=label, value=value_text,
                    )
                    created_value = context.phrasebook_manager.create_value(request, context.user_id)
                    created.append({
                        "id": created_value.id,
                        "label": created_value.label,
                        "value": created_value.value,
                        "marker": _format_marker(f"{category_path}.{created_value.label}"),
                    })
                except ValueError as e:
                    failed.append({"label": label, "error": str(e)})

            if not created and failed:
                return ToolResult(
                    success=False, data="",
                    error=f"Failed to create any values: {failed[0]['error']}",
                )

            payload: Dict[str, Any] = {
                "created_count": len(created),
                "values": created,
                "instruction": "These markers can now be embedded directly in update_segment content.",
            }
            if failed:
                payload["failed"] = failed

            return ToolResult(success=True, data=json.dumps(payload))
        except Exception as e:
            logger.error(f"Error creating phrasebook values: {e}")
            return ToolResult(success=False, data="", error=str(e))
