"""Model info tool for accessing model metadata."""

import json
import logging
from typing import Any, Dict

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult

# Opt-in payload extras; everything else is the always-returned compact core.
_EXTRA_FIELDS = {"description", "tags", "provider", "model_metadata"}

logger = logging.getLogger(__name__)


class GetModelInfoTool(BaseTool):
    """Gets metadata about a specific AI model."""

    modes = ["generation", "models"]
    icon = "box"

    @property
    def name(self) -> str:
        return "get_model_info"

    @property
    def group(self) -> str:
        return "Models & presets"

    @property
    def user_description(self) -> str:
        return "Looks up details about a specific model you have installed."

    @property
    def hint(self) -> str:
        return (
            "When you need details about a specific model by ID — e.g., to check "
            "a LoRA's trigger words or a checkpoint's style guide. The result includes "
            "admin-authored prompting_guidance when present — consult it before writing "
            "or rewriting prompts for that model."
            "{{#if get_active_models}} For the user's currently active models, prefer "
            "get_active_models instead.{{/if}}"
        )

    @property
    def description(self) -> str:
        return (
            "Get information about a specific AI model by its ID. By default "
            "returns a compact core (id, filename, type, trigger words and "
            "prompting guidance when set). Pass fields to request more: "
            "'description' (can be long), 'tags', 'provider', 'model_metadata' "
            "(per-type custom attributes, e.g. a LoRA's default strength)."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "The model ID or file path to look up.",
                },
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["description", "tags", "provider", "model_metadata"],
                    },
                    "description": (
                        "Extra fields to include beyond the compact core. "
                        "Request 'description' only when you actually need the "
                        "long-form text. 'model_metadata' returns the model's "
                        "per-type custom attributes (e.g. a LoRA's default strength)."
                    ),
                },
            },
            "required": ["model_id"],
        }

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.model_index_manager:
            return ToolResult(success=False, data="", error="Model index manager not available")

        model_id = kwargs.get("model_id")
        if not model_id:
            return ToolResult(success=False, data="", error="model_id is required")

        raw_fields = kwargs.get("fields") or []
        fields = {f for f in raw_fields if isinstance(f, str)} & _EXTRA_FIELDS

        try:
            model_data = context.model_index_manager.get_model_by_id(model_id)
            return ToolResult(success=True, data=json.dumps(self._summarize(model_data, fields)))
        except Exception:
            logger.debug(f"get_model_by_id failed for '{model_id}', trying path-based lookup")

        # Fallback: treat model_id as a file path
        try:
            repo = context.model_index_manager.model_repo
            model = repo.get_by_file_path(model_id, include_providers=True)
            if model:
                return ToolResult(
                    success=True,
                    data=json.dumps(self._model_obj_to_summary(model, fields)),
                )
        except Exception as e:
            logger.debug(f"Path lookup failed for '{model_id}': {e}")

        # Fallback: search by filename
        try:
            filename = model_id.rsplit("/", 1)[-1]
            repo = context.model_index_manager.model_repo
            models = repo.get_all(
                search=filename, limit=1,
                include_providers=True, include_tags=True,
            )
            if models:
                return ToolResult(
                    success=True,
                    data=json.dumps(self._model_obj_to_summary(models[0], fields)),
                )
        except Exception as e:
            logger.debug(f"Filename search failed for '{model_id}': {e}")

        return ToolResult(
            success=False, data="",
            error=f"Model '{model_id}' not found by ID, path, or filename.",
        )

    @staticmethod
    def _build_summary(info: dict, fields: set) -> dict:
        """One summary shape for BOTH lookup paths (maintainer 2026-07-16: the
        compact core is always returned; long-form fields only on request —
        small chat models drown in unconditional descriptions)."""
        summary: Dict[str, Any] = {
            "id": info.get("id", ""),
            "filename": info.get("filename", ""),
            "type": info.get("type", ""),
        }
        if info.get("triggers"):
            summary["trigger_words"] = info["triggers"]
        if info.get("prompting_guidance"):
            summary["prompting_guidance"] = info["prompting_guidance"]
        if "description" in fields and info.get("description"):
            summary["description"] = info["description"]
        if "tags" in fields and info.get("tags"):
            summary["tags"] = info["tags"]
        if "provider" in fields and info.get("provider"):
            summary["provider"] = info["provider"]
        if "model_metadata" in fields and info.get("model_metadata"):
            summary["model_metadata"] = info["model_metadata"]
        return summary

    @classmethod
    def _summarize(cls, model_data: dict, fields: set) -> dict:
        """Summary from a get_model_by_id result dict."""
        model_info = model_data.get("model", model_data)
        provider_info = model_info.get("provider_info")
        info = {
            "id": model_info.get("id", ""),
            "filename": model_info.get("filename", ""),
            "type": model_info.get("type", ""),
            "description": model_info.get("description", ""),
            "tags": model_info.get("tags", []),
            "triggers": model_info.get("triggers"),
            "prompting_guidance": model_info.get("prompting_guidance"),
            "provider": {
                "name": provider_info.get("name", ""),
                "description": provider_info.get("description", ""),
            } if provider_info else None,
            "model_metadata": model_info.get("model_metadata"),
        }
        return cls._build_summary(info, fields)

    @classmethod
    def _model_obj_to_summary(cls, model, fields: set) -> dict:
        """Summary from a model record object (path/filename fallback paths)."""
        d = model.to_dict(include_providers=True, include_tags=True)
        tags = [
            (t.get("name", "") if isinstance(t, dict) else str(t))
            for t in d.get("tags", [])
        ]
        provider = None
        for p in d.get("providers", []):
            if isinstance(p, dict) and p.get("description"):
                provider = {"name": p.get("name", ""), "description": p.get("description", "")}
                break
        info = {
            "id": d.get("id", ""),
            "filename": d.get("filename", ""),
            "type": d.get("model_type", d.get("type", "")),
            "description": d.get("description", ""),
            "tags": tags,
            "triggers": d.get("triggers"),
            "prompting_guidance": d.get("prompting_guidance"),
            "provider": provider,
            "model_metadata": d.get("model_metadata"),
        }
        return cls._build_summary(info, fields)
