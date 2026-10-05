"""Segment tools for accessing user's prompt segments."""

import json
import logging
from typing import Any, Dict, Optional

from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolContext, ToolResult
from src.features.llm.tools.builtin.manage_prompts_tool import RICH_SEGMENT_SCHEMA
from src.features.llm.tools.errors import unexpected
from src.features.segments import operations
from src.features.segments.dto import SavedSegmentRequest, SegmentCategoryRequest, SegmentTemplateRequest

logger = logging.getLogger(__name__)


class ListSegmentCategoriesTool(BaseTool):
    """Lists all segment categories available to the user."""

    modes = ["generation"]
    icon = "layers"

    @property
    def name(self) -> str:
        return "list_segment_categories"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Lists the categories of your saved segment library."

    @property
    def hint(self) -> str:
        return (
            "When helping the user build or organize their prompt, call this "
            "to see what reusable segment categories exist."
        )

    @property
    def description(self) -> str:
        return (
            "List all segment categories available to the user. "
            "Segments are reusable prompt building blocks organized by category "
            "(e.g., 'Character', 'Environment', 'Style'). "
            "Use this to discover what prompt segments the user has created."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.segment_category_repository:
            return ToolResult(success=False, data="", error="Segment manager not available")

        try:
            categories = context.segment_category_repository.get_all(context.user_id)
            result = []
            for cat in categories:
                result.append({
                    "id": cat.id,
                    "name": cat.name,
                    "description": getattr(cat, 'description', ''),
                    "color": getattr(cat, 'color', ''),
                })
            return ToolResult(
                success=True,
                data=json.dumps({"categories": result, "count": len(result)}),
            )
        except Exception as e:
            logger.error(f"Error listing segment categories: {e}")
            return ToolResult(success=False, data="", error=str(e))


class GetSavedSegmentsTool(BaseTool):
    """Gets reusable single saved Segments, optionally by category."""

    modes = ["generation"]
    icon = "layers"

    @property
    def name(self) -> str:
        return "get_saved_segments"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Fetches reusable prompt segments from your library."

    @property
    def hint(self) -> str:
        return "Use when the user wants one reusable prompt card from their categorized library."

    @property
    def description(self) -> str:
        return (
            "Get saved Segments: named, categorized single rich prompt cards. "
            "These are distinct from multi-segment Templates."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category_id": {
                    "type": "string",
                    "description": "Optional Segment Category ID.",
                }
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.saved_segment_repository:
            return ToolResult(success=False, data="", error="Segment manager not available")
        try:
            category_id = kwargs.get("category_id")
            if category_id:
                operations.get_category(context.segment_category_repository, category_id, context.user_id)
            segments = context.saved_segment_repository.get_all(context.user_id, category_id)
            return ToolResult(success=True, data=json.dumps({
                "segments": [item.model_dump(mode="json") for item in segments],
                "count": len(segments),
            }))
        except Exception as e:
            logger.error("Error getting saved Segments: %s", e)
            return ToolResult(success=False, data="", error=str(e))


class GetSegmentTemplatesTool(BaseTool):
    """Gets ordered multi-segment Templates."""

    modes = ["generation"]
    icon = "layout-template"

    @property
    def name(self) -> str:
        return "get_segment_templates"

    @property
    def group(self) -> str:
        return "Form & segments"

    @property
    def user_description(self) -> str:
        return "Fetches your reusable multi-segment prompt templates."

    @property
    def hint(self) -> str:
        return (
            "When helping build prompts, use this to find reusable templates "
            "the user has created. Good for jumpstarting prompt construction."
        )

    @property
    def description(self) -> str:
        return (
            "Get Segment Templates. Each Template contains an ordered array of "
            "one or more rich segment slots and is not assigned to a Segment Category."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.segment_template_repository:
            return ToolResult(success=False, data="", error="Segment manager not available")

        try:
            templates = context.segment_template_repository.get_all(context.user_id)
            result = [tmpl.model_dump(mode="json") for tmpl in templates]
            return ToolResult(
                success=True,
                data=json.dumps({"templates": result, "count": len(result)}),
            )
        except Exception as e:
            logger.error(f"Error getting segment templates: {e}")
            return ToolResult(success=False, data="", error=str(e))


def _truncate(text: str, limit: int = 90) -> str:
    return text[:limit] + ("..." if len(text) > limit else "")


def _fail(error: str) -> ToolResult:
    return ToolResult(success=False, data="", error=error)


_SEGMENT_FIELDS = (
    "name", "category_id", "content", "chips", "resources", "enabled",
    "color", "description", "prefix", "suffix", "tags",
)
_TEMPLATE_FIELDS = ("name", "description", "tags", "segments")
_CATEGORY_FIELDS = ("name", "description", "color")

_TEMPLATE_SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        **RICH_SEGMENT_SCHEMA["properties"],
        "prefix": {"type": "string"},
        "suffix": {"type": "string"},
        "resources": {"type": "object", "description": "Resource references keyed by id."},
    },
}

_SEGMENT_PROPERTIES = {
    "name": {"type": "string"},
    "category_id": {"type": "string", "description": "Category id from list_segment_categories."},
    "content": {"type": "string"},
    "chips": RICH_SEGMENT_SCHEMA["properties"]["chips"],
    "resources": {"type": "object", "description": "Resource references keyed by id."},
    "enabled": {"type": "boolean"},
    "color": {"type": "string"},
    "description": {"type": "string"},
    "prefix": {"type": "string"},
    "suffix": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
}

_TEMPLATE_PROPERTIES = {
    "name": {"type": "string"},
    "description": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
    "segments": {"type": "array", "items": _TEMPLATE_SEGMENT_SCHEMA, "minItems": 1},
}

_CATEGORY_PROPERTIES = {
    "name": {"type": "string"},
    "description": {"type": "string"},
    "color": {"type": "string", "description": "Hex color such as #3B82F6."},
}


def _merged(existing, fields, kwargs) -> Dict[str, Any]:
    values = {field: getattr(existing, field) for field in fields}
    if "chips" in values:
        values["chips"] = {key: chip.model_dump(mode="json") for key, chip in existing.chips.items()}
    if "resources" in values:
        values["resources"] = {key: ref.model_dump(mode="json") for key, ref in existing.resources.items()}
    if "segments" in values:
        values["segments"] = [segment.model_dump(mode="json") for segment in existing.segments]
    values.update({field: kwargs[field] for field in fields if field in kwargs})
    return values


def _supplied(fields, kwargs) -> Dict[str, Any]:
    return {field: kwargs[field] for field in fields if field in kwargs}


def _category_payload(category) -> Dict[str, Any]:
    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "color": category.color,
    }


def _preview_fields(request, existing, labels) -> list:
    fields = []
    for label, field in labels:
        value = getattr(request, field)
        old = getattr(existing, field) if existing is not None else None
        if existing is not None and value == old:
            continue
        if value in (None, "", [], {}):
            continue
        shown = ", ".join(value) if isinstance(value, list) and all(isinstance(v, str) for v in value) else str(value)
        row = {"label": label, "value": _truncate(shown)}
        if existing is not None:
            row["old"] = _truncate(", ".join(old) if isinstance(old, list) else str(old or ""))
        fields.append(row)
    return fields


class _SegmentWriteTool(BaseTool):
    modes = ["generation"]
    icon = "layers"
    repositories: tuple = ()

    @property
    def group(self): return "Form & segments"

    @property
    def requires_approval(self): return True

    def _available(self, context: ToolContext) -> Optional[ToolResult]:
        if context.plugin_registry is None or any(
            getattr(context, attr) is None for attr in self.repositories
        ):
            return _fail("Segment manager not available")
        return None

    def _missing_id(self, key: str, kwargs) -> Optional[ToolResult]:
        if not kwargs.get(key):
            return _fail(f"{key} is required")
        return None

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        unavailable = self._available(context)
        if unavailable:
            return unavailable
        try:
            return self._preview(context, kwargs)
        except ValueError as exc:
            return _fail(str(exc))
        except Exception as exc:
            logger.error("%s preview failed: %s", self.name, exc)
            return _fail(unexpected(self.name, "preview", exc))

    async def execute_confirmed(self, context: ToolContext, **kwargs) -> ToolResult:
        unavailable = self._available(context)
        if unavailable:
            return unavailable
        try:
            return self._apply(context, kwargs)
        except ValueError as exc:
            return _fail(str(exc))
        except Exception as exc:
            logger.error("%s failed: %s", self.name, exc)
            return _fail(unexpected(self.name, "save", exc))

    def _proposal(self, preview: ToolApprovalPreview, payload: Dict[str, Any], message: str) -> ToolResult:
        return ToolResult(
            success=True,
            data=json.dumps({"action": self.name, "proposal": payload, "message": message}),
            preview=preview,
        )


class _SavedSegmentTool(_SegmentWriteTool):
    repositories = ("saved_segment_repository", "segment_category_repository")

    def _existing(self, context, kwargs):
        error = self._missing_id("segment_id", kwargs)
        if error:
            raise ValueError(error.error)
        return operations.get_segment(context.saved_segment_repository, kwargs["segment_id"], context.user_id)


class CreateSavedSegmentTool(_SavedSegmentTool):
    @property
    def name(self): return "create_saved_segment"

    @property
    def user_description(self): return "Saves a new reusable segment to your library."

    @property
    def hint(self): return "When the user wants to keep a reusable prompt card in a category"

    @property
    def description(self):
        return (
            "Create a saved Segment: one named, reusable rich prompt card filed under a category. "
            "category_id comes from list_segment_categories; names must be unique. "
            "Requires user approval."
        )

    @property
    def parameters(self):
        return {"type": "object", "properties": dict(_SEGMENT_PROPERTIES), "required": ["name", "category_id"]}

    def _preview(self, context, kwargs):
        request = SavedSegmentRequest(**_supplied(_SEGMENT_FIELDS, kwargs))
        category = operations.get_category(context.segment_category_repository, request.category_id, context.user_id)
        if context.saved_segment_repository.get_by_name(request.name, context.user_id):
            raise ValueError("Saved Segment with this name already exists")
        preview = ToolApprovalPreview(
            action="Create saved segment",
            target=request.name,
            kind="text_edit",
            summary=_truncate(request.content) or request.name,
            fields=[{"label": "Category", "value": category.name}]
            + _preview_fields(request, None, (("Prefix", "prefix"), ("Suffix", "suffix"), ("Tags", "tags"))),
            text_blocks=[{"label": "Content", "text": request.content}],
        )
        return self._proposal(preview, request.model_dump(mode="json"), "This saved Segment will be created. Please confirm.")

    def _apply(self, context, kwargs):
        request = SavedSegmentRequest(**_supplied(_SEGMENT_FIELDS, kwargs))
        created = operations.create_segment(
            context.saved_segment_repository, context.segment_category_repository,
            context.plugin_registry, request, context.user_id,
        )
        return ToolResult(success=True, data=json.dumps(created.model_dump(mode="json")))


class UpdateSavedSegmentTool(_SavedSegmentTool):
    @property
    def name(self): return "update_saved_segment"

    @property
    def user_description(self): return "Edits a saved segment in your library."

    @property
    def hint(self): return "When the user wants to change a saved segment's text, name, category or tags"

    @property
    def description(self):
        return (
            "Update a saved Segment. segment_id comes from get_saved_segments. Fields have the same "
            "shape get_saved_segments returns; omitted fields keep their current value. "
            "category_id comes from list_segment_categories. Requires user approval."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {"segment_id": {"type": "string"}, **_SEGMENT_PROPERTIES},
            "required": ["segment_id"],
        }

    def _request(self, existing, kwargs):
        return SavedSegmentRequest(**_merged(existing, _SEGMENT_FIELDS, kwargs))

    def _preview(self, context, kwargs):
        existing = self._existing(context, kwargs)
        if not _supplied(_SEGMENT_FIELDS, kwargs):
            raise ValueError("No saved Segment fields were supplied")
        request = self._request(existing, kwargs)
        block = {"label": "Content", "text": request.content}
        if request.content != existing.content:
            block["old_text"] = existing.content
        preview = ToolApprovalPreview(
            action="Update saved segment",
            target=existing.name,
            kind="text_edit",
            summary=_truncate(request.content) or request.name,
            fields=_preview_fields(request, existing, (
                ("Name", "name"), ("Category", "category_id"), ("Prefix", "prefix"),
                ("Suffix", "suffix"), ("Tags", "tags"),
            )),
            text_blocks=[block],
        )
        return self._proposal(
            preview,
            {"segment_id": existing.id, "new": request.model_dump(mode="json")},
            "This saved Segment will be updated. Please confirm.",
        )

    def _apply(self, context, kwargs):
        existing = self._existing(context, kwargs)
        updated = operations.update_segment(
            context.saved_segment_repository, context.segment_category_repository,
            context.plugin_registry, existing.id, self._request(existing, kwargs), context.user_id,
        )
        return ToolResult(success=True, data=json.dumps(updated.model_dump(mode="json")))


class DeleteSavedSegmentTool(_SavedSegmentTool):
    icon = "trash-2"

    @property
    def name(self): return "delete_saved_segment"

    @property
    def user_description(self): return "Removes a saved segment from your library."

    @property
    def hint(self): return "When the user wants to permanently remove a saved segment"

    @property
    def description(self):
        return "Delete one saved Segment. segment_id comes from get_saved_segments. Requires user approval."

    @property
    def parameters(self):
        return {"type": "object", "properties": {"segment_id": {"type": "string"}}, "required": ["segment_id"]}

    def _preview(self, context, kwargs):
        existing = self._existing(context, kwargs)
        preview = ToolApprovalPreview(
            action="Delete saved segment", target=existing.name, summary=_truncate(existing.content),
        )
        return self._proposal(
            preview, {"segment_id": existing.id, "name": existing.name},
            "This saved Segment will be permanently deleted. Please confirm.",
        )

    def _apply(self, context, kwargs):
        existing = self._existing(context, kwargs)
        operations.delete_segment(
            context.saved_segment_repository, context.plugin_registry, existing.id, context.user_id,
        )
        return ToolResult(success=True, data=json.dumps({"deleted": True, "id": existing.id, "name": existing.name}))


class _TemplateTool(_SegmentWriteTool):
    icon = "layout-template"
    repositories = ("segment_template_repository",)

    def _existing(self, context, kwargs):
        error = self._missing_id("template_id", kwargs)
        if error:
            raise ValueError(error.error)
        return operations.get_template(context.segment_template_repository, kwargs["template_id"], context.user_id)


class CreateSegmentTemplateTool(_TemplateTool):
    @property
    def name(self): return "create_segment_template"

    @property
    def user_description(self): return "Saves a new multi-segment template to your library."

    @property
    def hint(self): return "When the user wants to keep an ordered set of prompt segments as a template"

    @property
    def description(self):
        return (
            "Create a Segment Template: a named, ordered array of one or more rich segments, "
            "not tied to a category. Names must be unique. Requires user approval."
        )

    @property
    def parameters(self):
        return {"type": "object", "properties": dict(_TEMPLATE_PROPERTIES), "required": ["name", "segments"]}

    def _preview(self, context, kwargs):
        request = SegmentTemplateRequest(**_supplied(_TEMPLATE_FIELDS, kwargs))
        if context.segment_template_repository.get_by_name(request.name, context.user_id):
            raise ValueError("Segment Template with this name already exists")
        text = "\n".join(segment.content for segment in request.segments)
        preview = ToolApprovalPreview(
            action="Create segment template",
            target=request.name,
            kind="text_edit",
            summary=f"{len(request.segments)} segments",
            fields=_preview_fields(request, None, (("Description", "description"), ("Tags", "tags"))),
            text_blocks=[{"label": "Segments", "text": text}],
        )
        return self._proposal(preview, request.model_dump(mode="json"), "This Segment Template will be created. Please confirm.")

    def _apply(self, context, kwargs):
        request = SegmentTemplateRequest(**_supplied(_TEMPLATE_FIELDS, kwargs))
        created = operations.create_template(
            context.segment_template_repository, context.plugin_registry, request, context.user_id,
        )
        return ToolResult(success=True, data=json.dumps(created.model_dump(mode="json")))


class UpdateSegmentTemplateTool(_TemplateTool):
    @property
    def name(self): return "update_segment_template"

    @property
    def user_description(self): return "Edits a segment template in your library."

    @property
    def hint(self): return "When the user wants to change a segment template"

    @property
    def description(self):
        return (
            "Update a Segment Template. template_id comes from get_segment_templates. Fields have the "
            "same shape get_segment_templates returns; omitted fields keep their current value. "
            "A supplied segments array replaces the whole ordered list. Requires user approval."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {"template_id": {"type": "string"}, **_TEMPLATE_PROPERTIES},
            "required": ["template_id"],
        }

    def _request(self, existing, kwargs):
        return SegmentTemplateRequest(**_merged(existing, _TEMPLATE_FIELDS, kwargs))

    def _preview(self, context, kwargs):
        existing = self._existing(context, kwargs)
        if not _supplied(_TEMPLATE_FIELDS, kwargs):
            raise ValueError("No Segment Template fields were supplied")
        request = self._request(existing, kwargs)
        new_text = "\n".join(segment.content for segment in request.segments)
        old_text = "\n".join(segment.content for segment in existing.segments)
        block = {"label": "Segments", "text": new_text}
        if new_text != old_text:
            block["old_text"] = old_text
        preview = ToolApprovalPreview(
            action="Update segment template",
            target=existing.name,
            kind="text_edit",
            summary=f"{len(request.segments)} segments",
            fields=_preview_fields(request, existing, (
                ("Name", "name"), ("Description", "description"), ("Tags", "tags"),
            )),
            text_blocks=[block],
        )
        return self._proposal(
            preview,
            {"template_id": existing.id, "new": request.model_dump(mode="json")},
            "This Segment Template will be updated. Please confirm.",
        )

    def _apply(self, context, kwargs):
        existing = self._existing(context, kwargs)
        updated = operations.update_template(
            context.segment_template_repository, context.plugin_registry,
            existing.id, self._request(existing, kwargs), context.user_id,
        )
        return ToolResult(success=True, data=json.dumps(updated.model_dump(mode="json")))


class DeleteSegmentTemplateTool(_TemplateTool):
    icon = "trash-2"

    @property
    def name(self): return "delete_segment_template"

    @property
    def user_description(self): return "Removes a segment template from your library."

    @property
    def hint(self): return "When the user wants to permanently remove a segment template"

    @property
    def description(self):
        return "Delete one Segment Template. template_id comes from get_segment_templates. Requires user approval."

    @property
    def parameters(self):
        return {"type": "object", "properties": {"template_id": {"type": "string"}}, "required": ["template_id"]}

    def _preview(self, context, kwargs):
        existing = self._existing(context, kwargs)
        preview = ToolApprovalPreview(
            action="Delete segment template", target=existing.name,
            summary=f"{len(existing.segments)} segments",
        )
        return self._proposal(
            preview, {"template_id": existing.id, "name": existing.name},
            "This Segment Template will be permanently deleted. Please confirm.",
        )

    def _apply(self, context, kwargs):
        existing = self._existing(context, kwargs)
        operations.delete_template(
            context.segment_template_repository, context.plugin_registry, existing.id, context.user_id,
        )
        return ToolResult(success=True, data=json.dumps({"deleted": True, "id": existing.id, "name": existing.name}))


class _CategoryTool(_SegmentWriteTool):
    repositories = ("segment_category_repository",)

    def _existing(self, context, kwargs):
        error = self._missing_id("category_id", kwargs)
        if error:
            raise ValueError(error.error)
        return operations.get_category(context.segment_category_repository, kwargs["category_id"], context.user_id)


class CreateSegmentCategoryTool(_CategoryTool):
    @property
    def name(self): return "create_segment_category"

    @property
    def user_description(self): return "Creates a new category in your segment library."

    @property
    def hint(self): return "When the user needs a new category to file saved segments under"

    @property
    def description(self):
        return "Create a segment category (e.g. 'Character', 'Style'). Names must be unique. Requires user approval."

    @property
    def parameters(self):
        return {"type": "object", "properties": dict(_CATEGORY_PROPERTIES), "required": ["name"]}

    def _preview(self, context, kwargs):
        request = SegmentCategoryRequest(**_supplied(_CATEGORY_FIELDS, kwargs))
        if context.segment_category_repository.get_by_name(request.name, context.user_id):
            raise ValueError("Category with this name already exists")
        preview = ToolApprovalPreview(
            action="Create segment category", target=request.name,
            summary=request.description or request.name,
        )
        return self._proposal(preview, request.model_dump(mode="json"), "This category will be created. Please confirm.")

    def _apply(self, context, kwargs):
        request = SegmentCategoryRequest(**_supplied(_CATEGORY_FIELDS, kwargs))
        created = operations.create_category(
            context.segment_category_repository, context.plugin_registry, request, context.user_id,
        )
        return ToolResult(success=True, data=json.dumps(_category_payload(created)))


class UpdateSegmentCategoryTool(_CategoryTool):
    @property
    def name(self): return "update_segment_category"

    @property
    def user_description(self): return "Edits a category in your segment library."

    @property
    def hint(self): return "When the user wants to rename or recolor a segment category"

    @property
    def description(self):
        return (
            "Update a segment category. category_id comes from list_segment_categories. Fields have the "
            "same shape list_segment_categories returns; omitted fields keep their current value. "
            "Requires user approval."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {"category_id": {"type": "string"}, **_CATEGORY_PROPERTIES},
            "required": ["category_id"],
        }

    def _request(self, existing, kwargs):
        return SegmentCategoryRequest(**_merged(existing, _CATEGORY_FIELDS, kwargs))

    def _preview(self, context, kwargs):
        existing = self._existing(context, kwargs)
        if not _supplied(_CATEGORY_FIELDS, kwargs):
            raise ValueError("No category fields were supplied")
        request = self._request(existing, kwargs)
        preview = ToolApprovalPreview(
            action="Update segment category",
            target=existing.name,
            summary=request.name,
            fields=_preview_fields(request, existing, (
                ("Name", "name"), ("Description", "description"), ("Color", "color"),
            )),
        )
        return self._proposal(
            preview,
            {"category_id": existing.id, "new": request.model_dump(mode="json")},
            "This category will be updated. Please confirm.",
        )

    def _apply(self, context, kwargs):
        existing = self._existing(context, kwargs)
        updated = operations.update_category(
            context.segment_category_repository, context.plugin_registry,
            existing.id, self._request(existing, kwargs), context.user_id,
        )
        return ToolResult(success=True, data=json.dumps(_category_payload(updated)))


class DeleteSegmentCategoryTool(_CategoryTool):
    icon = "trash-2"

    @property
    def name(self): return "delete_segment_category"

    @property
    def user_description(self): return "Removes an empty category from your segment library."

    @property
    def hint(self): return "When the user wants to remove a segment category that holds no saved segments"

    @property
    def description(self):
        return (
            "Delete a segment category. category_id comes from list_segment_categories. A category that "
            "still holds saved segments is refused. Requires user approval."
        )

    @property
    def parameters(self):
        return {"type": "object", "properties": {"category_id": {"type": "string"}}, "required": ["category_id"]}

    def _preview(self, context, kwargs):
        existing = self._existing(context, kwargs)
        preview = ToolApprovalPreview(action="Delete segment category", target=existing.name, summary=existing.name)
        return self._proposal(
            preview, {"category_id": existing.id, "name": existing.name},
            "This category will be permanently deleted. Please confirm.",
        )

    def _apply(self, context, kwargs):
        existing = self._existing(context, kwargs)
        operations.delete_category(
            context.segment_category_repository, context.plugin_registry, existing.id, context.user_id,
        )
        return ToolResult(success=True, data=json.dumps({"deleted": True, "id": existing.id, "name": existing.name}))
