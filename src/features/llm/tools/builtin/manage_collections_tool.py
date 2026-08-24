"""Collection CRUD and membership tool: creates/curates the folders a user
groups generations, library items, or saved prompts into. Collections are
per-surface (migration 137, `src.features.collections.dto.ALLOWED_SCOPES`) -
a 'history' collection only ever holds generations, a 'library' collection
only ever holds library items, a 'prompts' collection only ever holds saved
prompts - so every operation here takes an explicit `scope` and
add_items/remove_items reject an id kind that does not match it. All
operations act on `context.collection_manager`, scoped to `context.user_id`
and `scope` exactly like the `/api/collections` HTTP route.
"""

import json
import logging
from typing import Any, Dict, Tuple, Union

from src.features.collections.dto import ALLOWED_SCOPES
from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.errors import unexpected

logger = logging.getLogger(__name__)

_OPERATIONS = ("list", "create", "rename", "delete", "add_items", "remove_items")

# Which id kind a given scope's membership calls accept, and the manager
# methods that add/remove that kind.
_SCOPE_ID_KIND = {"history": "generation_ids", "library": "upload_ids", "prompts": "prompt_ids"}
_ID_KIND_SCOPE = {kind: scope for scope, kind in _SCOPE_ID_KIND.items()}
_ID_KIND_METHODS = {
    "generation_ids": ("add_members", "remove_members"),
    "upload_ids": ("add_upload_members", "remove_upload_members"),
    "prompt_ids": ("add_prompt_members", "remove_prompt_members"),
}
_ID_KIND_LABEL = {"generation_ids": "generation", "upload_ids": "upload", "prompt_ids": "prompt"}


class ManageCollectionsTool(BaseTool):
    """Create, rename, delete collections and move generations/library items/prompts in/out of them."""

    modes = ["generation", "history"]
    icon = "folder"

    @property
    def name(self) -> str:
        return "manage_collections"

    @property
    def group(self) -> str:
        return "Collections"

    @property
    def user_description(self) -> str:
        return "Creates, renames, deletes collections and adds/removes generations, library items, or prompts from them."

    @property
    def hint(self) -> str:
        return "When the user wants to organize generations, library items, or prompts into named collections/folders."

    @property
    def description(self) -> str:
        return (
            "Manage the user's collections (named, optionally nested folders) and their membership. "
            "Collections are per-surface: a 'history'-scope collection holds only generations and a "
            "'library'-scope collection holds only library items - these are the two primary surfaces "
            "users organize. A 'prompts'-scope collection (saved prompts) also exists. Every operation "
            "needs `scope` ('history', 'library', or 'prompts') - there is no default. `operation` "
            "selects what happens: 'list' returns every collection in `scope` with its item count; "
            "'create' needs `name` (and optional `parent_id` to nest it under a collection in the SAME "
            "scope); 'rename' needs `collection_id` and `name`; 'delete' needs `collection_id` and "
            "cascades its memberships; 'add_items'/'remove_items' need `collection_id` and exactly one "
            "of `generation_ids` (only valid with scope='history', e.g. ids from organize_gallery's "
            "list_recent), `upload_ids` (only valid with scope='library', distinct from generation "
            "ids), or `prompt_ids` (only valid with scope='prompts') - never more than one kind in the "
            "same call. Example: "
            '{"operation": "add_items", "scope": "history", "collection_id": "col_123", '
            '"generation_ids": ["gen_abc"]}'
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
                "scope": {
                    "type": "string",
                    "enum": list(ALLOWED_SCOPES),
                    "description": (
                        "Which collection tree this call operates on. 'history' for generation "
                        "folders and 'library' for library-item folders are the two primary surfaces; "
                        "'prompts' collections (saved prompts) also exist. Required for every operation, "
                        "no default."
                    ),
                },
                "collection_id": {"type": "string", "description": "Required for rename/delete/add_items/remove_items."},
                "name": {"type": "string", "description": "Required for create/rename."},
                "parent_id": {"type": "string", "description": "Optional parent collection id for create (nests it; must be the same scope; omit for a root collection)."},
                "generation_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Generation history ids. Only valid with scope='history'. Exactly one of generation_ids/upload_ids/prompt_ids is required for add_items/remove_items - never combine kinds in one call.",
                },
                "upload_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Library item ids (distinct from generation ids). Only valid with scope='library'. Exactly one of generation_ids/upload_ids/prompt_ids is required for add_items/remove_items - never combine kinds in one call.",
                },
                "prompt_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Saved prompt ids. Only valid with scope='prompts'. Exactly one of generation_ids/upload_ids/prompt_ids is required for add_items/remove_items - never combine kinds in one call.",
                },
            },
            "required": ["operation", "scope"],
        }

    @staticmethod
    def _missing(field: str) -> ToolResult:
        return ToolResult(success=False, data="", error=f"'{field}' is required for this operation")

    @staticmethod
    def _resolve_membership_kind(kwargs: Dict[str, Any], scope: str) -> Union[Tuple[str, list], ToolResult]:
        """Resolves the single id kind an add_items/remove_items call supplies.

        Returns `(kind, ids)` for the one populated kind, or a teaching
        ToolResult error if zero/multiple kinds were supplied, or the
        supplied kind doesn't belong to `scope`.
        """
        present = {
            kind: ids for kind, ids in (
                ("generation_ids", kwargs.get("generation_ids") or []),
                ("upload_ids", kwargs.get("upload_ids") or []),
                ("prompt_ids", kwargs.get("prompt_ids") or []),
            ) if ids
        }
        if not present:
            return ToolResult(
                success=False, data="",
                error="At least one of 'generation_ids'/'upload_ids'/'prompt_ids' is required for this operation",
            )
        if len(present) > 1:
            return ToolResult(
                success=False, data="",
                error=(
                    "Collections are per-surface now — make one call per surface, with the scope "
                    "matching the collection ('history' for generations, 'library' for uploads, "
                    "'prompts' for prompts)."
                ),
            )
        kind, ids = next(iter(present.items()))
        expected_scope = _ID_KIND_SCOPE[kind]
        if scope != expected_scope:
            return ToolResult(
                success=False, data="",
                error=(
                    f"'{kind}' belongs to scope='{expected_scope}', not scope='{scope}'. Use "
                    f"scope='{expected_scope}' for {kind}, or switch to the id kind that matches "
                    f"scope='{scope}'."
                ),
            )
        return kind, ids

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        """Preview of the proposed action; performs no mutation. `list` has no
        mutation to preview, so it returns the real listing directly."""
        manager = context.collection_manager
        if manager is None:
            return ToolResult(success=False, data="", error="Collections not available")

        operation = kwargs.get("operation")
        if operation not in _OPERATIONS:
            return ToolResult(success=False, data="", error=f"'operation' must be one of {list(_OPERATIONS)}")

        scope = kwargs.get("scope")
        if scope not in ALLOWED_SCOPES:
            return ToolResult(success=False, data="", error=f"'scope' is required and must be one of {list(ALLOWED_SCOPES)}")

        try:
            if operation == "list":
                return self._list(manager, context.user_id, scope)

            if operation == "create":
                name = (kwargs.get("name") or "").strip()
                if not name:
                    return self._missing("name")
                parent_id = kwargs.get("parent_id")
                preview = ToolApprovalPreview(
                    action="Create collection", items=[name],
                    target=f"under {parent_id}" if parent_id else None,
                )
                return ToolResult(success=True, data=json.dumps({
                    "action": "create_collection",
                    "proposal": {"name": name, "scope": scope, "parent_id": parent_id},
                    "message": f"Collection '{name}' will be created. Please confirm.",
                }), preview=preview)

            if operation == "rename":
                collection_id = kwargs.get("collection_id")
                name = (kwargs.get("name") or "").strip()
                if not collection_id:
                    return self._missing("collection_id")
                if not name:
                    return self._missing("name")
                existing = manager.get_collection(collection_id, context.user_id, scope)
                preview = ToolApprovalPreview(
                    action="Rename collection", items=[f"{existing.name} -> {name}"],
                )
                return ToolResult(success=True, data=json.dumps({
                    "action": "rename_collection",
                    "proposal": {"collection_id": collection_id, "scope": scope, "old_name": existing.name, "new_name": name},
                    "message": f"Collection '{existing.name}' will be renamed to '{name}'. Please confirm.",
                }), preview=preview)

            if operation == "delete":
                collection_id = kwargs.get("collection_id")
                if not collection_id:
                    return self._missing("collection_id")
                existing = manager.get_collection(collection_id, context.user_id, scope)
                preview = ToolApprovalPreview(
                    action="Delete collection", items=[existing.name],
                    note=f"{existing.item_count or 0} membership(s) will also be removed",
                )
                return ToolResult(success=True, data=json.dumps({
                    "action": "delete_collection",
                    "proposal": {"collection_id": collection_id, "scope": scope, "name": existing.name, "item_count": existing.item_count},
                    "message": f"Collection '{existing.name}' will be permanently deleted. Please confirm.",
                }), preview=preview)

            # add_items / remove_items
            collection_id = kwargs.get("collection_id")
            if not collection_id:
                return self._missing("collection_id")
            resolved = self._resolve_membership_kind(kwargs, scope)
            if isinstance(resolved, ToolResult):
                return resolved
            kind, ids = resolved
            existing = manager.get_collection(collection_id, context.user_id, scope)
            verb = "Add" if operation == "add_items" else "Remove"
            label = _ID_KIND_LABEL[kind]
            items = [f"{label}:{i}" for i in ids]
            preview = ToolApprovalPreview(
                action=f"{verb} items", items=items, target=f"{'to' if verb == 'Add' else 'from'} '{existing.name}'",
            )
            return ToolResult(success=True, data=json.dumps({
                "action": operation,
                "proposal": {
                    "collection_id": collection_id, "collection_name": existing.name, "scope": scope, kind: ids,
                },
                "message": (
                    f"{len(ids)} {label}(s) will be "
                    f"{'added to' if verb == 'Add' else 'removed from'} '{existing.name}'. Please confirm."
                ),
            }), preview=preview)
        except ValueError as e:
            return ToolResult(success=False, data="", error=str(e))
        except Exception as e:
            logger.error(f"manage_collections preview failed: {e}")
            return ToolResult(success=False, data="", error=unexpected("manage_collections", f"preview {operation}", e))

    @staticmethod
    def _list(manager, user_id: str, scope: str) -> ToolResult:
        collections = manager.list_collections(user_id, scope)
        return ToolResult(success=True, data=json.dumps({
            "collections": [c.to_dict() for c in collections],
            "total": len(collections),
        }))

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        manager = context.collection_manager
        if manager is None:
            return ToolResult(success=False, data="", error="Collections not available")

        operation = kwargs.get("operation")
        if operation not in _OPERATIONS:
            return ToolResult(success=False, data="", error=f"'operation' must be one of {list(_OPERATIONS)}")

        scope = kwargs.get("scope")
        if scope not in ALLOWED_SCOPES:
            return ToolResult(success=False, data="", error=f"'scope' is required and must be one of {list(ALLOWED_SCOPES)}")

        try:
            if operation == "list":
                return self._list(manager, context.user_id, scope)

            if operation == "create":
                name = (kwargs.get("name") or "").strip()
                if not name:
                    return self._missing("name")
                collection = manager.create_collection(name, context.user_id, scope, kwargs.get("parent_id"))
                return ToolResult(success=True, data=json.dumps({
                    "action": "create_collection", "success": True, "collection": collection.to_dict(),
                }))

            if operation == "rename":
                collection_id = kwargs.get("collection_id")
                name = (kwargs.get("name") or "").strip()
                if not collection_id:
                    return self._missing("collection_id")
                if not name:
                    return self._missing("name")
                collection = manager.rename_collection(collection_id, name, context.user_id, scope)
                return ToolResult(success=True, data=json.dumps({
                    "action": "rename_collection", "success": True, "collection": collection.to_dict(),
                }))

            if operation == "delete":
                collection_id = kwargs.get("collection_id")
                if not collection_id:
                    return self._missing("collection_id")
                manager.delete_collection(collection_id, context.user_id, scope)
                return ToolResult(success=True, data=json.dumps({
                    "action": "delete_collection", "success": True, "collection_id": collection_id,
                }))

            collection_id = kwargs.get("collection_id")
            if not collection_id:
                return self._missing("collection_id")
            resolved = self._resolve_membership_kind(kwargs, scope)
            if isinstance(resolved, ToolResult):
                return resolved
            kind, ids = resolved
            add_method, remove_method = _ID_KIND_METHODS[kind]
            method = getattr(manager, add_method if operation == "add_items" else remove_method)
            changed = method(collection_id, ids, context.user_id, scope)
            return ToolResult(success=True, data=json.dumps({
                "action": operation, "success": True, "collection_id": collection_id,
                "scope": scope, "changed": changed,
            }))
        except ValueError as e:
            return ToolResult(success=False, data="", error=str(e))
        except Exception as e:
            logger.error(f"manage_collections failed: {e}")
            return ToolResult(success=False, data="", error=unexpected("manage_collections", operation, e))
