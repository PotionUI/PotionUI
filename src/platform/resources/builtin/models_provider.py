"""@models resource provider.

Paths: ``models.<model_type>.<name>`` (e.g. ``models.lora.detailer``).
Model filenames may themselves contain dots, so everything after the type
segment is re-joined with dots before matching.
"""

import logging
from typing import Any, List, Optional

from src.platform.resources.base import (
    BaseResourceProvider,
    ResolvedResource,
    ResourceContext,
    ResourceSuggestion,
    stem,
)

logger = logging.getLogger(__name__)

MODEL_TYPES = [
    "checkpoint", "lora", "embedding", "vae",
    "upscaler", "controlnet", "adetailer", "text_encoder",
]

MAX_CONTENT_CHARS = 4000
MAX_PROVIDER_DESC_CHARS = 600


def _normalize_type(segment: str) -> Optional[str]:
    segment = segment.lower()
    if segment in MODEL_TYPES:
        return segment
    # Accept plural aliases: "loras" -> "lora"
    if segment.endswith("s") and segment[:-1] in MODEL_TYPES:
        return segment[:-1]
    return None


class ModelsResourceProvider(BaseResourceProvider):
    """Exposes indexed models (checkpoints, LoRAs, embeddings, ...)."""

    icon = "box"

    @property
    def namespace(self) -> str:
        return "models"

    async def suggest(
        self,
        path: List[str],
        partial: str,
        ctx: ResourceContext,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        if not ctx.model_index_manager:
            return []

        if not path:
            # Level 1: model types
            needle = partial.lower()
            return [
                ResourceSuggestion(
                    uri=f"models.{t}",
                    label=t.title(),
                    kind="model_type",
                    has_children=True,
                    icon=self.icon,
                )
                for t in MODEL_TYPES
                if not needle or t.startswith(needle) or f"{t}s".startswith(needle)
            ]

        model_type = _normalize_type(path[0])
        if model_type is None:
            return []

        # Dots in filenames: re-join any further segments with the partial.
        search = ".".join(path[1:] + [partial]) if (path[1:] or partial) else None
        repo = ctx.model_index_manager.model_repo
        models = repo.get_all(
            model_type=model_type,
            search=search,
            limit=limit,
            include_providers=True,
            include_tags=False,
        )
        suggestions = []
        for model in models:
            provider_name = model.providers[0].name if model.providers else None
            suggestions.append(ResourceSuggestion(
                uri=f"models.{model_type}.{stem(model.filename)}",
                label=stem(model.filename),
                kind="model",
                description=provider_name,
                has_children=False,
                icon=self.icon,
            ))
        return suggestions

    async def resolve(self, path: List[str], ctx: ResourceContext) -> Optional[ResolvedResource]:
        if not ctx.model_index_manager or not path:
            return None

        model_type = _normalize_type(path[0])
        if model_type is None or len(path) < 2:
            return None

        name = ".".join(path[1:])
        repo = ctx.model_index_manager.model_repo
        candidates = repo.get_all(
            model_type=model_type,
            search=name,
            limit=10,
            include_providers=True,
            include_tags=False,
        )
        if not candidates:
            return None

        exact = [m for m in candidates if stem(m.filename).lower() == name.lower()]
        model = exact[0] if exact else candidates[0]
        alternatives = [stem(m.filename) for m in candidates if m.id != model.id]

        content = self._render(model, model_type, alternatives)
        return ResolvedResource(
            uri=f"models.{path[0]}.{name}",
            namespace=self.namespace,
            kind=model_type,
            title=stem(model.filename),
            content=content[:MAX_CONTENT_CHARS],
            metadata={
                "model_id": model.id,
                "model_type": model.model_type,
                "filename": model.filename,
            },
        )

    @staticmethod
    def _render(model: Any, model_type: str, alternatives: List[str]) -> str:
        lines = [f"## Model: {stem(model.filename)}", ""]
        lines.append(f"- Type: {model.model_type or model_type}")
        if model.filename:
            lines.append(f"- File: {model.filename}")

        triggers = list(dict.fromkeys(
            ((model.model_metadata or {}).get("triggers") or [])
            + [tag for info in (model.providers or []) for tag in (info.tags or [])]
        ))
        if triggers:
            lines.append(f"- Trigger words: {', '.join(triggers[:50])}")

        for info in (model.providers or []):
            if info.name:
                lines.append(f"- Provider name: {info.name} ({info.provider})")

        if getattr(model, "prompting_guidance", None):
            lines += ["", "### Prompting guidance", model.prompting_guidance.strip()]

        if model.description:
            lines += ["", "### Description", model.description.strip()]

        for info in (model.providers or []):
            if info.description:
                desc = info.description.strip()
                if len(desc) > MAX_PROVIDER_DESC_CHARS:
                    desc = desc[:MAX_PROVIDER_DESC_CHARS] + "…"
                lines += ["", f"### Provider notes ({info.provider})", desc]

        if alternatives:
            lines += ["", f"(Other partial matches not shown: {', '.join(alternatives[:5])})"]
        return "\n".join(lines)
