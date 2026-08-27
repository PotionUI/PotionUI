"""Search gallery tool: free-text visual search over the user's generations."""

import asyncio
import json
import logging
from typing import Any, Dict, List

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


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
            "user made before, based on what the images show rather than their prompt text."
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

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        indexer = context.media_indexer
        if indexer is None:
            return ToolResult(success=False, data="", error="Gallery search not available")

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

        try:
            results = []
            seen: set = set()
            total_found = 0

            for query in queries:
                hits = await asyncio.to_thread(
                    indexer.search_gallery, context.user_id, query
                )
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
                        # The real file, as a storage-root-relative path: this is
                        # the value a media form field takes. Without it a model
                        # holding only a generation id constructs
                        # 'generations/<id>/0.png' - wrong twice over, since the
                        # date segment is not derivable and videos have no index
                        # 0. `thumbnail` is a preview, never an input.
                        "path": summary.get("file_path"),
                        "thumbnail": summary.get("thumbnail") or summary.get("file_path"),
                    }
                    matches.append({k: v for k, v in entry.items() if v is not None})

                total_found += len(matches)
                results.append({"query": query, "matches": matches})

            payload: Dict[str, Any] = {"results": results}
            if total_found == 0:
                payload["message"] = (
                    "No visually matching generations found. The gallery index may still "
                    "be catching up on recent generations."
                )
            if truncated:
                payload["truncated"] = f"Only the first {self.MAX_QUERIES} concepts were searched."

            return ToolResult(success=True, data=json.dumps(payload))
        except Exception as e:
            logger.error(f"search_gallery failed: {e}")
            return ToolResult(success=False, data="", error=f"Gallery search failed: {e}")
