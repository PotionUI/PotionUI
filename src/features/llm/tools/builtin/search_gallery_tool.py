"""Search gallery tool: free-text visual search over the user's generations."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult
from src.features.llm.tools.errors import unexpected

logger = logging.getLogger(__name__)

_VISUAL_UNAVAILABLE_NOTE = (
    "Visual search is unavailable until the gallery vision embedder is installed, so these "
    "matches come from a text search over your generation prompts, preset and model names."
)
_VISUAL_FAILED_NOTE = (
    "Visual search failed on the server, so these matches come from a text search over your "
    "generation prompts, preset and model names."
)


class SearchGalleryTool(BaseTool):
    """Finds past generations by describing what the images show."""

    modes = ["generation"]
    icon = "image"

    MAX_QUERIES = 6
    MAX_LIMIT = 25

    @property
    def name(self) -> str:
        return "search_gallery"

    @property
    def group(self) -> str:
        return "Generation"

    @property
    def user_description(self) -> str:
        return "Finds images in your gallery by describing what they show."

    @property
    def hint(self) -> str:
        return (
            "Use when the user refers to images they generated before ('the castle from "
            "yesterday', 'my cyberpunk portraits') or wants to find, revisit, compare or build "
            "on past results. Describe the VISUAL CONTENT of the wanted image as ATOMIC concepts "
            "(subject, setting, style), one per element of `queries` — compound phrases like "
            "'red fox in a snowy forest at night' match worse than 'red fox' + 'snowy forest' + "
            "'night' searched together. Each result carries a `path` you pass verbatim to a "
            "media form field to reuse that exact file."
        )

    @property
    def description(self) -> str:
        return (
            "Semantic visual search over the user's generated image gallery. Each element of "
            "`queries` is one ATOMIC visual concept (e.g. [\"red fox\", \"snowy forest\"]); "
            "compound phrases match poorly. Each match carries a `path` - the file's real "
            "storage-root-relative path, which is exactly what an image/video/audio/media "
            "form field takes; pass it through verbatim and never construct a path from a "
            "generation id. `thumbnail` is a preview only. Use it to find images the "
            "user made before, based on what the images show rather than their prompt text. "
            "When the server has no vision embedder installed, it falls back to a text search "
            "over the user's generation prompts, preset and model names; the result then has "
            "search_mode 'text' and a note saying so."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of ATOMIC visual concepts to search, one per element "
                        "(e.g. [\"red fox\", \"snowy forest\", \"night\"]). Do NOT combine "
                        "concepts into one element — the search is semantic and compound "
                        f"phrases match poorly. At most {self.MAX_QUERIES} concepts are searched."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": f"Number of results to return per concept (default 5, max {self.MAX_LIMIT}).",
                    "default": 5,
                },
            },
            "required": ["queries"],
        }

    @staticmethod
    def _normalize_queries(kwargs: Dict[str, Any]) -> List[str]:
        """Coerce `queries` (or a legacy single `query`) into a clean list."""
        raw = kwargs.get("queries")
        if raw is None:
            raw = kwargs.get("query")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(q).strip() for q in raw if q and str(q).strip()]

    @staticmethod
    def _visual_available(indexer: Any) -> bool:
        embedder = getattr(indexer, "vision_embedder", None)
        if embedder is None:
            return True
        try:
            return bool(embedder.is_available())
        except Exception:
            logger.warning("search_gallery could not check the vision embedder", exc_info=True)
            return False

    async def _visual_results(self, context: ToolContext, queries: List[str], limit: int) -> List[Dict[str, Any]]:
        indexer = context.media_indexer
        results = []
        seen: set = set()
        for query in queries:
            hits = await asyncio.to_thread(indexer.search_gallery, context.user_id, query)
            hits = hits[:limit]
            summaries = indexer.describe_files([hit["file_id"] for hit in hits])
            matches = []
            for hit in hits:
                generation_id = hit.get("generation_id")
                key = generation_id or hit["file_id"]
                if key in seen:
                    continue
                seen.add(key)
                summary = summaries.get(hit["file_id"], {})
                entry = {
                    "generation_id": generation_id,
                    "file_id": hit["file_id"],
                    "similarity": round(float(hit.get("similarity", 0.0)), 4),
                    "media_type": summary.get("file_type"),
                    "path": summary.get("file_path"),
                    "thumbnail": summary.get("thumbnail") or summary.get("file_path"),
                }
                matches.append({k: v for k, v in entry.items() if v is not None})
            results.append({"query": query, "matches": matches})
        return results

    @staticmethod
    def _pick_file(files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        usable = [f for f in files if f.get("file_path")]
        finals = [f for f in usable if f.get("is_final") and not f.get("is_derived")]
        return (finals or usable or [None])[0]

    @staticmethod
    def _prompt_text(form_data: Any) -> Optional[str]:
        prompt = form_data.get("prompt") if isinstance(form_data, dict) else None
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        text = " ".join(prompt.split())
        return text[:160] + ("..." if len(text) > 160 else "")

    async def _text_results(self, context: ToolContext, queries: List[str], limit: int) -> List[Dict[str, Any]]:
        history = context.generation_history_facade
        results = []
        seen: set = set()
        for query in queries:
            page = await history.get_history_async(
                user_id=context.user_id, search=query, status="completed",
                limit=limit, offset=0, include_tags=False,
            )
            matches = []
            for generation in page.get("generations") or []:
                generation_id = generation.get("id")
                picked = self._pick_file(generation.get("files") or [])
                if picked is None or generation_id in seen:
                    continue
                seen.add(generation_id)
                entry = {
                    "generation_id": generation_id,
                    "file_id": picked.get("id"),
                    "media_type": picked.get("file_type"),
                    "path": picked.get("file_path"),
                    "thumbnail": picked.get("thumbnail_medium") or picked.get("file_path"),
                    "prompt": self._prompt_text(generation.get("form_data")),
                    "created_at": generation.get("created_at"),
                }
                matches.append({k: v for k, v in entry.items() if v is not None})
            results.append({"query": query, "matches": matches})
        return results

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        indexer = context.media_indexer
        history = context.generation_history_facade
        if indexer is None and history is None:
            return ToolResult(success=False, data="", error="Gallery search is not available on this server.")

        queries = self._normalize_queries(kwargs)
        if not queries:
            return ToolResult(
                success=False, data="",
                error="queries is required (a list of atomic visual concepts)",
            )

        truncated = len(queries) > self.MAX_QUERIES
        queries = queries[:self.MAX_QUERIES]

        try:
            limit = min(max(1, int(kwargs.get("limit", 5))), self.MAX_LIMIT)
        except (TypeError, ValueError):
            limit = 5

        note = None
        results = None
        if indexer is not None and self._visual_available(indexer):
            try:
                results = await self._visual_results(context, queries, limit)
            except Exception as e:
                logger.warning("search_gallery visual search failed, falling back to text: %s", e)
                note = _VISUAL_FAILED_NOTE
        else:
            note = _VISUAL_UNAVAILABLE_NOTE

        if results is None:
            if history is None:
                return ToolResult(success=False, data="", error=f"{note} Text search is not available either.")
            try:
                results = await self._text_results(context, queries, limit)
            except Exception as e:
                logger.error("search_gallery text search failed: %s", e)
                return ToolResult(success=False, data="", error=unexpected("search_gallery", "text search", e, context.is_admin))

        payload: Dict[str, Any] = {"search_mode": "text" if note else "visual", "results": results}
        if note:
            payload["note"] = note
        if not any(group["matches"] for group in results):
            payload["message"] = (
                "No generations matched these words in their prompts, preset or model names."
                if note else
                "No visually matching generations found. The gallery index may still "
                "be catching up on recent generations."
            )
        if truncated:
            payload["truncated"] = f"Only the first {self.MAX_QUERIES} concepts were searched."
        return ToolResult(success=True, data=json.dumps(payload))
