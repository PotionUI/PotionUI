"""List models tool for browsing available models."""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.features.content_safety.restrict import provider_flags_nsfw
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.builtin.utils import allowed_model_ids, parse_page, viewer_is_restricted
from src.features.models.attributes.well_known import WellKnownModelAttribute
from src.features.models.type_repository import ModelTypeRepository

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100


def visible_models(
    context: ToolContext, model_type: Optional[str], query: Optional[str], limit: int,
    offset: int = 0, probe_more: bool = False,
) -> Tuple[List[Any], bool]:
    restricted = viewer_is_restricted(context)
    models = context.model_index_manager.model_repo.get_all(
        model_type=model_type or None,
        search=query or None,
        limit=limit + 1 if probe_more else limit,
        offset=offset,
        include_providers=restricted,
        include_tags=True,
        allowed_model_ids=allowed_model_ids(context),
        include_files=False,
    )
    has_more = len(models) > limit
    models = models[:limit]
    if restricted:
        models = [model for model in models if not provider_flags_nsfw(model.providers)]
    return models, has_more


def _tag_names(model: Any) -> List[str]:
    return [
        (tag.get("name", "") if isinstance(tag, dict) else getattr(tag, "name", str(tag)))
        for tag in (model.tags or [])
    ]


def _families(models: List[Any]) -> Dict[str, Optional[str]]:
    try:
        verdicts = ModelTypeRepository().get_verdicts(model.id for model in models)
    except Exception:
        logger.warning("could not read model families", exc_info=True)
        return {}
    return {model_id: row.get("family") for model_id, row in verdicts.items()}


class ListModelsTool(BaseTool):
    """Lists available models with optional filtering."""

    modes = ["generation", "models"]
    icon = "library"
    context_group = "library"

    name = "list_models"
    mcp = True
    read_only = True
    group = "Models & presets"
    user_description = "Lists the models installed on your server."

    @property
    def description(self) -> str:
        return (
            "List available models, optionally filtered by type or search query. "
            "Returns model names, types, and IDs."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "model_type": {
                    "type": "string",
                    "description": (
                        "Filter by model type: checkpoint, lora, vae, "
                        "embedding, controlnet, upscaler."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "Search filter for model name.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 20).",
                    "default": 20,
                },
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.model_index_manager:
            return ToolResult(success=False, data="", error="Model index manager not available")

        model_type = kwargs.get("model_type")
        query = kwargs.get("query")
        limit = kwargs.get("limit", 20)

        try:
            limit = min(max(1, int(limit)), MAX_SEARCH_LIMIT)
        except (TypeError, ValueError):
            return ToolResult(success=False, data="", error="'limit' must be an integer.")

        models, _ = visible_models(context, model_type, query, limit)

        results = []
        for model in models:
            d = model.to_dict(include_providers=False, include_tags=True)
            tags = [
                (t.get("name", "") if isinstance(t, dict) else str(t))
                for t in d.get("tags", [])
            ]
            entry: Dict[str, Any] = {
                "id": d.get("id", ""),
                "filename": d.get("filename", ""),
                "type": d.get("model_type", d.get("type", "")),
            }
            if tags:
                entry["tags"] = tags
            desc = d.get("description", "")
            if desc:
                entry["description"] = desc[:100] + ("..." if len(desc) > 100 else "")
            results.append(entry)

        return ToolResult(
            success=True,
            data=json.dumps({"models": results, "count": len(results)}),
        )


class SearchModelsTool(BaseTool):
    modes = ["generation", "models"]
    icon = "search"

    name = "search_models"
    mcp = True
    read_only = True
    group = "Models & presets"
    user_description = "Searches the models you can use, with their trigger words."

    @property
    def description(self) -> str:
        return (
            "Search the models the user can see (their model access and content rules apply). "
            "Each entry has id, filename, name, type, base_model (the detected family, when "
            "known) and trigger_words. 'query' matches the model name and filename; 'type' "
            "filters by model type (checkpoint, lora, vae, embedding, controlnet, upscaler, "
            "text_encoder). Paged with 'offset'/'limit' "
            f"(default {DEFAULT_SEARCH_LIMIT}, max {MAX_SEARCH_LIMIT}); check has_more. Pass an "
            "id to get_model_info for the description and prompting guidance."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Match against model name and filename."},
                "type": {"type": "string", "description": "Model type, e.g. checkpoint or lora."},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_LIMIT, "default": DEFAULT_SEARCH_LIMIT},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            "required": [],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.model_index_manager:
            return ToolResult(success=False, data="", error="Models are not available on this server.")
        try:
            offset, limit = parse_page(kwargs, DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT)
        except ValueError as exc:
            return ToolResult(success=False, data="", error=str(exc))
        query = (kwargs.get("query") or "").strip()
        model_type = (kwargs.get("type") or kwargs.get("model_type") or "").strip()
        models, has_more = visible_models(context, model_type, query, limit, offset, probe_more=True)
        families = _families(models)
        entries = []
        for model in models:
            metadata = model.model_metadata or {}
            entry: Dict[str, Any] = {
                "id": model.id,
                "filename": model.filename,
                "name": model.display_name,
                "type": model.model_type,
                "base_model": families.get(model.id),
                "trigger_words": metadata.get(WellKnownModelAttribute.TRIGGERS) or [],
            }
            tags = _tag_names(model)
            if tags:
                entry["tags"] = tags
            entries.append(entry)
        payload: Dict[str, Any] = {
            "models": entries, "count": len(entries), "offset": offset, "limit": limit, "has_more": has_more,
        }
        if not entries and not has_more:
            payload["message"] = (
                "No models matched." if query or model_type
                else "No models are available to you yet. An admin can assign models to you or your group."
            )
        return ToolResult(success=True, data=json.dumps(payload))
