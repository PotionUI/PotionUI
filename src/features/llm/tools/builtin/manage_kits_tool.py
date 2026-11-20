import json
from typing import Any, Dict, Optional

from src.features.kits import citations, operations
from src.features.kits.catalog import KitCatalog
from src.features.kits.mappers import summary_to_dict
from src.features.library import operations as library_operations
from src.features.llm.tools.base import ApprovalTool, ToolApprovalPreview, ToolContext, ToolResult

_READS = ("list", "get")
_WRITES = ("create", "update", "duplicate", "add_item", "update_item", "reorder_items", "remove_item", "delete")
_ACTIONS = _READS + _WRITES
_PREVIEW_CHARS = 120
_LIST_LIMIT = 200

class ManageKitsTool(ApprovalTool):
    repositories = ("kit_repository",)
    unavailable_error = "Kits not available"

    modes = ["mcp"]
    icon = "package"
    context_group = "library"

    name = "manage_kits"
    mcp = True
    group = "Kits"
    user_description = "Lists, creates, edits, fills and deletes kits: named sets of text, images, video and audio cited in prompts."

    @property
    def description(self) -> str:
        return (
            "Manage the user's kits: named sets of text snippets and media that a prompt cites as "
            "@kits.<handle>.<item>. `action`: list; get (needs `kit`, returns items with the exact `citation`); "
            "create (`name`, optional `handle`, `label`); update (`kit`, any of `name`, `handle`, `label`); "
            "duplicate (`kit`); delete (`kit`, removes the kit and its items); add_item (`kit` plus `text`, "
            "or `upload_ids` from list_library_items, or `generation_file_ids` from history; optional "
            "`item_name`); update_item (`kit`, `item`, `item_name` and/or `text`); remove_item (`kit`, `item`); "
            "reorder_items (`kit`, `item_ids` listing every item once). `kit` and `item` take an id or a name."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        text = {"type": "string"}
        ids = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "kit": {**text, "description": "Kit id (kt_...) or handle."},
                "name": {**text, "description": "Kit name for create/update."},
                "handle": {**text, "description": "Kit path name for create/update."},
                "label": {**text, "description": "Kit label for create/update."},
                "item": {**text, "description": "Item id (it_...) or name."},
                "item_name": {**text, "description": "Item name for add_item/update_item."},
                "text": {**text, "description": "Text for a text item."},
                "upload_ids": {**ids, "description": "Library item ids for add_item."},
                "generation_file_ids": {**ids, "description": "History file ids for add_item."},
                "item_ids": {**ids, "description": "Every item id of the kit in the new order."},
            },
            "required": ["action"],
        }

    def approval_required(self, arguments: Dict[str, Any]) -> bool:
        return str((arguments or {}).get("action") or "") in _WRITES

    @staticmethod
    def _missing(field: str) -> ToolResult:
        return ToolResult(success=False, data="", error=f"'{field}' is required for this action")

    @staticmethod
    def _done(payload: Dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, data=json.dumps(payload))

    @staticmethod
    def _kit_row(view) -> Dict[str, Any]:
        kit = view.kit
        return {
            "id": kit.id, "handle": kit.handle, "name": kit.name, "label": kit.label,
            "citation": f"@kits.{kit.handle}", "item_count": len(view.items),
            "items": [ManageKitsTool._item_row(item) for item in view.items],
        }

    @staticmethod
    def _item_row(view) -> Dict[str, Any]:
        item = view.item
        row = {
            "id": item.id, "name": item.name, "kind": item.kind,
            "citation": f"@kits.{view.kit.handle}.{item.name}",
        }
        if item.is_text:
            text = item.text or ""
            row["text"] = text if len(text) <= _PREVIEW_CHARS else text[:_PREVIEW_CHARS] + "..."
        elif view.missing:
            row["missing"] = True
        return row

    @staticmethod
    def _find_kit(context: ToolContext, reference: str):
        catalog = KitCatalog(context.kit_repository, context.user_id)
        stable = citations.parse_stable(reference)
        view = catalog.by_id(stable.kit_id) if stable else catalog.by_handle(reference.strip().lstrip("@").removeprefix("kits."))
        if view is None:
            kits, _total = context.kit_repository.list_kits(context.user_id, limit=_LIST_LIMIT)
            named = next((kit for kit in kits if kit.name.lower() == reference.strip().lower()), None)
            view = catalog.by_id(named.id) if named else None
        if view is None:
            raise ValueError(f"No kit '{reference}' of yours. Use action 'list' to see your kits.")
        return view

    @staticmethod
    def _find_item(view, reference: str):
        wanted = reference.strip()
        found = next((item for item in view.items if wanted in (item.item.id, item.item.name)), None)
        if found is None:
            raise ValueError(f"No item '{reference}' in kit '{view.kit.handle}'. Use action 'get' to see its items.")
        return found

    def _kit_ref(self, context: ToolContext, kwargs: Dict[str, Any]):
        reference = (kwargs.get("kit") or "").strip()
        if not reference:
            return self._missing("kit")
        return self._find_kit(context, reference)

    def _read(self, context: ToolContext, action: str, kwargs: Dict[str, Any]) -> ToolResult:
        repository = context.kit_repository
        if action == "list":
            kits, total = repository.list_kits(context.user_id, limit=_LIST_LIMIT)
            counts = repository.counts_for([kit.id for kit in kits])
            rows = []
            for kit in kits:
                summary = summary_to_dict(kit, counts[kit.id], [])
                rows.append({
                    "id": kit.id, "handle": kit.handle, "name": kit.name, "label": kit.label,
                    "citation": f"@kits.{kit.handle}", "item_count": summary["item_count"],
                })
            return self._done({"kits": rows, "total": total})
        found = self._kit_ref(context, kwargs)
        if isinstance(found, ToolResult):
            return found
        return self._done({"kit": self._kit_row(found)})

    def _preview(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        action = kwargs.get("action")
        if action not in _ACTIONS:
            return ToolResult(success=False, data="", error=f"'action' must be one of: {', '.join(_ACTIONS)}")
        if action in _READS:
            return self._read(context, action, kwargs)
        plan = self._plan(context, action, kwargs)
        if isinstance(plan, ToolResult):
            return plan
        verb, target, items, note = plan
        preview = ToolApprovalPreview(action=verb, target=target, items=items, note=note)
        return ToolResult(success=True, data=json.dumps({
            "action": f"{self.name}.{action}", "proposal": {"action": action, "kit": target},
            "message": f"{verb}{' ' + target if target else ''}. Please confirm.",
        }), preview=preview)

    def _plan(self, context: ToolContext, action: str, kwargs: Dict[str, Any]):
        if action == "create":
            name = (kwargs.get("name") or "").strip()
            if not name:
                return self._missing("name")
            return "Create kit", None, [name], None
        found = self._kit_ref(context, kwargs)
        if isinstance(found, ToolResult):
            return found
        kit = found.kit
        target = f"{kit.name} (@kits.{kit.handle})"
        if action == "update":
            changes = [f"{key}: {kwargs[key]}" for key in ("name", "handle", "label") if kwargs.get(key) is not None]
            if not changes:
                return ToolResult(success=False, data="", error="Give at least one of 'name', 'handle', 'label'")
            return "Update kit", target, changes, "Prompts citing the old handle stop resolving." if kwargs.get("handle") else None
        if action == "duplicate":
            return "Duplicate kit", target, [], None
        if action == "delete":
            return "Delete kit", target, [item.item.name for item in found.items], (
                "The kit and its items are removed; prompts citing it stop resolving. This cannot be undone."
            )
        if action == "add_item":
            sources = [kwargs.get(key) for key in ("text", "upload_ids", "generation_file_ids") if kwargs.get(key)]
            if len(sources) != 1:
                return ToolResult(success=False, data="", error="Give exactly one of 'text', 'upload_ids', 'generation_file_ids'")
            if kwargs.get("text"):
                items = [str(kwargs["text"])[:_PREVIEW_CHARS]]
            else:
                items = list(kwargs.get("upload_ids") or kwargs.get("generation_file_ids"))
            return "Add to kit", target, items, None
        if action in ("update_item", "remove_item"):
            if not (kwargs.get("item") or "").strip():
                return self._missing("item")
            item = self._find_item(found, kwargs["item"])
            if action == "remove_item":
                return "Remove from kit", target, [item.item.name], None
            if kwargs.get("item_name") is None and kwargs.get("text") is None:
                return ToolResult(success=False, data="", error="Give 'item_name' and/or 'text'")
            return "Edit kit item", target, [item.item.name], None
        if not kwargs.get("item_ids"):
            return self._missing("item_ids")
        return "Reorder kit items", target, [], None

    def _apply(self, context: ToolContext, kwargs: Dict[str, Any]) -> ToolResult:
        action = kwargs.get("action")
        if action not in _ACTIONS:
            return ToolResult(success=False, data="", error=f"'action' must be one of: {', '.join(_ACTIONS)}")
        if action in _READS:
            return self._read(context, action, kwargs)
        repository, user_id = context.kit_repository, context.user_id

        if action == "create":
            name = (kwargs.get("name") or "").strip()
            if not name:
                return self._missing("name")
            kit = operations.create_kit(repository, user_id, name, kwargs.get("handle"), kwargs.get("label"))
            return self._created(context, kit.id, "create")

        found = self._kit_ref(context, kwargs)
        if isinstance(found, ToolResult):
            return found
        kit_id = found.kit.id

        if action == "update":
            label = kwargs["label"] if kwargs.get("label") is not None else operations.UNSET
            operations.update_kit(repository, user_id, kit_id, kwargs.get("name"), kwargs.get("handle"), label)
            return self._created(context, kit_id, action)
        if action == "duplicate":
            copy = operations.duplicate_kit(repository, user_id, kit_id)
            return self._created(context, copy.id, action)
        if action == "delete":
            operations.delete_kit(repository, user_id, kit_id)
            return self._done({"action": "delete", "success": True, "kit_id": kit_id})
        if action == "add_item":
            self._add_item(context, kit_id, kwargs)
            return self._created(context, kit_id, action)
        if action == "reorder_items":
            operations.reorder_items(repository, user_id, kit_id, list(kwargs.get("item_ids") or []))
            return self._created(context, kit_id, action)

        reference = (kwargs.get("item") or "").strip()
        if not reference:
            return self._missing("item")
        item_id = self._find_item(found, reference).item.id
        if action == "remove_item":
            operations.remove_item(repository, user_id, kit_id, item_id)
        else:
            operations.update_item(repository, user_id, kit_id, item_id, kwargs.get("item_name"), kwargs.get("text"))
        return self._created(context, kit_id, action)

    def _add_item(self, context: ToolContext, kit_id: str, kwargs: Dict[str, Any]) -> None:
        repository, user_id = context.kit_repository, context.user_id
        sources = [key for key in ("text", "upload_ids", "generation_file_ids") if kwargs.get(key)]
        if len(sources) != 1:
            raise ValueError("Give exactly one of 'text', 'upload_ids', 'generation_file_ids'")
        item_name: Optional[str] = kwargs.get("item_name")
        if sources[0] == "text":
            operations.add_text_item(repository, user_id, kit_id, kwargs["text"], item_name)
        elif sources[0] == "upload_ids":
            ids = list(kwargs["upload_ids"])
            operations.add_library_items(
                repository, user_id, ids, kit_id, names={ids[0]: item_name} if item_name and len(ids) == 1 else None,
            )
        else:
            collaborators = context.library_collaborators
            if collaborators is None:
                raise ValueError("History items cannot be added here")
            ids = list(kwargs["generation_file_ids"])
            operations.add_history_items(
                repository,
                lambda file_id, owner_id: library_operations.copy_generation_file(collaborators, file_id, owner_id).id,
                user_id, ids, kit_id, names={ids[0]: item_name} if item_name and len(ids) == 1 else None,
            )

    def _created(self, context: ToolContext, kit_id: str, action: str) -> ToolResult:
        view = KitCatalog(context.kit_repository, context.user_id).by_id(kit_id)
        return self._done({"action": action, "success": True, "kit": self._kit_row(view)})
