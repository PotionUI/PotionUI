"""Search prompts tool for finding community prompt examples."""

import json
import logging
from typing import Any, Dict, Optional

from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult, ToolSource
from src.features.llm.tools.builtin.utils import resolve_active_model_id, extract_model_path

logger = logging.getLogger(__name__)


class SearchModelPromptsTool(BaseTool):
    """Searches community prompts for inspiration and reference."""

    modes = ["generation", "prompts"]
    icon = "search"

    # Upper bound on how many concepts a single call will search, to bound work.
    MAX_QUERIES = 6

    @property
    def name(self) -> str:
        return "search_model_prompts"

    @property
    def group(self) -> str:
        return "Prompt writing"

    @property
    def user_description(self) -> str:
        return "Finds example prompts known to work well with your model."

    @property
    def hint(self) -> str:
        return (
            "ALWAYS call this BEFORE you write, improve, or suggest any prompt — you do not need the "
            "user to ask. Ground every suggestion in real, proven community prompts. "
            "Decompose the desired image into ATOMIC concepts (subject, environment, style, lighting, "
            "composition, mood) and pass them all as separate elements of `queries` in a single call — "
            "the search is semantic, so compound phrases like 'fox in forest' match poorly while "
            "'fox' and 'forest' searched separately match well. "
            "{{#if get_active_models}}First call get_active_models to get the model IDs currently "
            "selected, then pass a model_id here for model-specific results. {{/if}}"
            "Also use when the user needs ideas, is stuck, asks 'what should I generate', or switches models."
        )

    @property
    def description(self) -> str:
        return (
            "Search community prompts for inspiration and reference. Returns top-rated prompts with "
            "generation parameters, grouped per query concept. "
            "Pass MULTIPLE atomic concepts in `queries` (one per element) rather than a single combined "
            "phrase — the search is semantic and compound phrases match poorly. "
            "{{#if get_active_models}}Best workflow: call get_active_models first to get model IDs, then "
            "pass the relevant model_id here for model-specific results. {{/if}}"
            "Use proactively when writing prompts to improve quality with real community examples."
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
                        "List of ATOMIC concepts to search, one per element "
                        "(e.g. [\"red fox\", \"pine forest\", \"golden hour lighting\"]). "
                        "Do NOT combine concepts into one element like \"fox in forest\" — the search is "
                        "semantic and compound phrases match poorly. Break the desired image into "
                        "subject / environment / style / lighting / composition / mood and pass each separately. "
                        f"At most {self.MAX_QUERIES} concepts are searched."
                    ),
                },
                "model_id": {
                    "type": "string",
                    "description": (
                        "Model ID to search prompts for."
                        "{{#if get_active_models}} Get this from get_active_models.{{/if}} "
                        "If omitted, auto-resolves from the currently active model in the form."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results to return per concept (default 3).",
                    "default": 3,
                },
            },
            "required": ["queries"],
        }

    @staticmethod
    def _normalize_queries(kwargs: Dict[str, Any]) -> list:
        """Coerce the `queries` (or legacy `query`) argument into a clean list of concepts.

        Tolerant of weaker models that may emit a single `query` string or pass `queries`
        as a string instead of an array.
        """
        raw = kwargs.get("queries")
        if raw is None:
            raw = kwargs.get("query")  # leniency for models that emit the old single-query arg
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(q).strip() for q in raw if q and str(q).strip()]

    @staticmethod
    def _prompt_entry(p: Any) -> Dict[str, Any]:
        """Build the cleaned per-prompt dict the LLM consumes."""
        entry = {
            "prompt_id": p.id,
            "name": p.name,
            "flattened_text": p.flattened_text[:500] if p.flattened_text else "",
            "usage_hint": p.usage_hint,
            "segments": [segment.model_dump() for segment in p.segments],
            "model_name": p.model_name,
            "base_model": p.base_model,
            "cfg_scale": p.cfg_scale,
            "steps": p.steps,
            "sampler": p.sampler,
            "source_url": p.source_url,
            "source_provider": p.source_provider,
            "reactions": {
                "heart": p.heart_count,
                "like": p.like_count,
                "laugh": p.laugh_count,
            },
        }
        # Remove None values for cleaner output
        return {k: v for k, v in entry.items() if v is not None}

    @staticmethod
    def _dedupe_key(p: Any) -> str:
        """Stable identity for a prompt, used to dedupe across query groups."""
        return p.source_url or (p.flattened_text or "")

    async def execute(self, context: ToolContext, **kwargs) -> ToolResult:
        if not context.prompt_database_manager:
            return ToolResult(success=False, data="", error="Prompt database not available")

        queries = self._normalize_queries(kwargs)
        if not queries:
            return ToolResult(success=False, data="", error="queries is required (a list of atomic concepts)")

        truncated = len(queries) > self.MAX_QUERIES
        queries = queries[:self.MAX_QUERIES]

        limit = kwargs.get("limit", 3)
        model_id = kwargs.get("model_id")

        # Auto-resolve model_id from form state if not explicitly provided
        if not model_id:
            model_id = resolve_active_model_id(
                context.session_metadata.get("form_state"), context.model_index_manager
            )

        try:
            results = []
            sources = []
            seen: set = set()  # dedupe prompts across query groups
            total_found = 0

            for query in queries:
                search_kwargs: Dict[str, Any] = {
                    "user_id": context.user_id,
                    "query": query,
                    "limit": limit,
                }
                if model_id:
                    search_kwargs["model_id"] = model_id

                prompts = await context.prompt_database_manager.search(**search_kwargs)

                group_entries = []
                for p in prompts or []:
                    key = self._dedupe_key(p)
                    if key in seen:
                        continue
                    seen.add(key)
                    group_entries.append(self._prompt_entry(p))

                    total_reactions = (p.heart_count or 0) + (p.like_count or 0) + (p.laugh_count or 0)
                    sources.append(ToolSource(
                        source_type="prompt",
                        title=p.display_name,
                        subtitle=f"{p.model_name or 'Unknown model'} / {p.base_model or ''}".strip(" /"),
                        description=f"{total_reactions} reactions" if total_reactions > 0 else None,
                        url=p.source_url,
                    ))

                total_found += len(group_entries)
                results.append({"query": query, "prompts": group_entries})

            payload: Dict[str, Any] = {"results": results}
            if total_found == 0:
                payload["message"] = "No matching prompts found"
            if truncated:
                payload["truncated"] = f"Only the first {self.MAX_QUERIES} concepts were searched."

            return ToolResult(success=True, data=json.dumps(payload), sources=sources)
        except Exception as e:
            logger.error(f"search_model_prompts failed: {e}")
            return ToolResult(success=False, data="", error=f"Search failed: {e}")
