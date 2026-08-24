"""@generations resource provider.

Paths: ``generations.recent`` (summary of the user's latest generations) and
``generations.<id>`` (one generation's prompt, parameters, and models).
Generation ids are ULIDs (dot-free). All lookups are scoped to the requesting
user.
"""

import json
import logging
from typing import Any, List, Optional

from src.platform.resources.base import (
    BaseResourceProvider,
    ResolvedResource,
    ResourceContext,
    ResourceSuggestion,
)

logger = logging.getLogger(__name__)

RECENT_LIMIT = 5
MAX_FORM_DATA_CHARS = 1500

# form_data keys that carry prompt text, listed first in rendered content
PROMPT_KEYS = ("prompt", "positive_prompt", "negative_prompt", "segments")


class GenerationsResourceProvider(BaseResourceProvider):
    """Exposes the user's past generations."""

    icon = "history"

    @property
    def namespace(self) -> str:
        return "generations"

    async def suggest(
        self,
        path: List[str],
        partial: str,
        ctx: ResourceContext,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        if not ctx.generation_repository or path:
            return []

        suggestions = [ResourceSuggestion(
            uri="generations.recent",
            label="Recent generations",
            kind="listing",
            description=f"Your last {RECENT_LIMIT} generations",
            has_children=False,
            icon=self.icon,
        )]
        needle = partial.lower()
        generations = ctx.generation_repository.get_all(
            user_id=ctx.user_id, limit=limit
        )
        for gen in generations:
            if needle and needle not in gen.id.lower() and needle != "recent"[:len(needle)]:
                continue
            created = gen.created_at.strftime("%Y-%m-%d %H:%M") if gen.created_at else ""
            suggestions.append(ResourceSuggestion(
                uri=f"generations.{gen.id}",
                label=f"{gen.id[:8]}… · {created}",
                kind="generation",
                description=gen.status,
                has_children=False,
                icon=self.icon,
            ))
        return suggestions[:limit]

    async def resolve(self, path: List[str], ctx: ResourceContext) -> Optional[ResolvedResource]:
        if not ctx.generation_repository or not path:
            return None

        if path[0] == "recent":
            return self._recent(ctx)

        generation = ctx.generation_repository.get_by_id(path[0], user_id=ctx.user_id)
        if not generation:
            return None
        return self._single(generation, ctx)

    def _recent(self, ctx: ResourceContext) -> Optional[ResolvedResource]:
        generations = ctx.generation_repository.get_all(
            user_id=ctx.user_id, limit=RECENT_LIMIT
        )
        if not generations:
            return ResolvedResource(
                uri="generations.recent",
                namespace=self.namespace,
                kind="listing",
                title="Recent generations",
                content="The user has no generations yet.",
            )
        lines = ["## Recent generations", ""]
        for gen in generations:
            created = gen.created_at.strftime("%Y-%m-%d %H:%M") if gen.created_at else "?"
            lines.append(f"### {gen.id} ({created}, {gen.status})")
            lines.append(self._form_data_block(gen))
            lines.append("")
        return ResolvedResource(
            uri="generations.recent",
            namespace=self.namespace,
            kind="listing",
            title="Recent generations",
            content="\n".join(lines),
            metadata={"count": len(generations)},
        )

    def _single(self, generation: Any, ctx: ResourceContext) -> ResolvedResource:
        created = generation.created_at.strftime("%Y-%m-%d %H:%M") if generation.created_at else "?"
        lines = [
            f"## Generation {generation.id}",
            f"- Created: {created}",
            f"- Status: {generation.status}",
        ]
        if generation.preset_id:
            lines.append(f"- Preset: {generation.preset_id}")

        models = self._models_used(generation.id, ctx)
        if models:
            lines.append(f"- Models used: {', '.join(models)}")

        lines += ["", "### Settings", self._form_data_block(generation)]
        return ResolvedResource(
            uri=f"generations.{generation.id}",
            namespace=self.namespace,
            kind="generation",
            title=f"Generation {generation.id[:8]}…",
            content="\n".join(lines),
            metadata={
                "generation_id": generation.id,
                "preset_id": generation.preset_id,
                "status": generation.status,
            },
        )

    @staticmethod
    def _models_used(generation_id: str, ctx: ResourceContext) -> List[str]:
        if not ctx.generation_model_repository:
            return []
        try:
            models = ctx.generation_model_repository.get_by_generation(generation_id)
            return [
                f"{m.filename} ({m.model_type})" if m.model_type else (m.filename or m.id)
                for m in models
            ]
        except Exception as e:
            logger.debug(f"Could not load models for generation {generation_id}: {e}")
            return []

    @staticmethod
    def _form_data_block(generation: Any) -> str:
        form_data = generation.form_data or {}
        prompt_lines = []
        rest = {}
        for key, value in form_data.items():
            if key in PROMPT_KEYS:
                prompt_lines.append(f"- {key}: {json.dumps(value) if not isinstance(value, str) else value}")
            else:
                rest[key] = value
        block = "\n".join(prompt_lines)
        if rest:
            rest_json = json.dumps(rest, default=str)
            if len(rest_json) > MAX_FORM_DATA_CHARS:
                rest_json = rest_json[:MAX_FORM_DATA_CHARS] + "…"
            block += ("\n" if block else "") + f"- other settings: {rest_json}"
        return block or "- (no settings recorded)"
