"""List models tool for browsing available models."""

import json
import logging
from typing import Any, Dict

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class ListModelsTool(BaseTool):
    """Lists available models with optional filtering."""

    modes = ["generation", "models"]
    icon = "library"

    @property
    def name(self) -> str:
        return "list_models"

    @property
    def group(self) -> str:
        return "Models & presets"

    @property
    def user_description(self) -> str:
        return "Lists the models installed on your server."

    @property
    def hint(self) -> str:
        return (
            "When the user asks what models are available, wants to switch models, "
            "or needs to find a specific model by name or type."
        )

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
            models = context.model_index_manager.model_repo.get_all(
                model_type=model_type,
                search=query,
                limit=limit,
                include_providers=False,
                include_tags=True,
            )

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
        except Exception as e:
            logger.error(f"Error listing models: {e}")
            return ToolResult(success=False, data="", error=str(e))
