"""@models resource provider.

Paths: ``models.<model_type>.<name>`` (e.g. ``models.lora.detailer``).
Model filenames may themselves contain dots, so everything after the type
segment is re-joined with dots before matching.
"""

import logging
from typing import Any, Dict, List, Optional

from src.platform.resources.base import (
    BaseResourceProvider,
    ResolvedResource,
    ResourceContext,
    ResourceSuggestion,
    stem,
)
from src.platform.filesystem.model_types import MODEL_TYPES as DEPOT_MODEL_TYPES
from src.platform.security.user import AccountType, User

logger = logging.getLogger(__name__)

MODEL_TYPES = list(DEPOT_MODEL_TYPES)

MAX_CONTENT_CHARS = 4000
MAX_PROVIDER_DESC_CHARS = 600
MAX_TAGS = 20
SEARCH_TYPE_ORDER = ["lora"]
_TRIGGERS_KEY = "triggers"
_STRENGTH_KEY = "strength"
_BASE_MODEL_KEYS = ("base_model", "architecture")


def _normalize_type(segment: str) -> Optional[str]:
    segment = segment.lower()
    if segment in MODEL_TYPES:
        return segment
    # Accept plural aliases: "loras" -> "lora"
    if segment.endswith("s") and segment[:-1] in MODEL_TYPES:
        return segment[:-1]
    return None


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _display_name(model: Any) -> str:
    return _text(getattr(model, "display_name", None)) or stem(model.filename) or str(model.id)


def _allowed_ids(ctx: ResourceContext) -> Optional[List[str]]:
    stand_in = User(
        username="", email="", password_hash="",
        id=ctx.user_id,
        account_type=AccountType.ADMIN if ctx.is_admin else AccountType.USER,
    )
    return ctx.model_index_manager.access.get_allowed_model_ids(stand_in, all_models=True)


def is_visible(ctx: ResourceContext, model: Any) -> bool:
    allowed = _allowed_ids(ctx)
    return allowed is None or getattr(model, "id", None) in allowed


def user_overlay(ctx: ResourceContext, model_id: str) -> Dict[str, Any]:
    try:
        overlay = ctx.model_index_manager.catalog.user_attributes.get_maps(ctx.user_id, [model_id])
    except Exception as e:
        logger.debug(f"User attribute overlay unavailable for {model_id}: {e}")
        return {}
    entry = overlay.get(model_id) if isinstance(overlay, dict) else None
    return entry if isinstance(entry, dict) else {}


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
        allowed = _allowed_ids(ctx)
        return [self._model_suggestion(m) for m in self._find(ctx, allowed, model_type, search, limit)]

    async def search(
        self,
        query: str,
        ctx: ResourceContext,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        if not ctx.model_index_manager or limit <= 0:
            return []
        allowed = _allowed_ids(ctx)
        found = []
        for model_type in SEARCH_TYPE_ORDER:
            found += self._find(ctx, allowed, model_type, query, limit)
        if len(found) < limit:
            seen = {m.id for m in found}
            found += [
                m for m in self._find(ctx, allowed, None, query, limit + len(seen))
                if m.id not in seen and m.model_type not in SEARCH_TYPE_ORDER and m.model_type in MODEL_TYPES
            ]
        return [self._model_suggestion(m) for m in found[:limit]]

    def _find(
        self,
        ctx: ResourceContext,
        allowed: Optional[List[str]],
        model_type: Optional[str],
        search: Optional[str],
        limit: int,
    ) -> List[Any]:
        if allowed is not None and not allowed:
            return []
        return ctx.model_index_manager.model_repo.get_all(
            model_type=model_type,
            search=search,
            limit=limit,
            include_providers=True,
            include_tags=False,
            include_files=False,
            allowed_model_ids=allowed,
            library_user_id=ctx.user_id,
        )

    def _model_suggestion(self, model: Any) -> ResourceSuggestion:
        name = _display_name(model)
        file_stem = stem(model.filename)
        model_type = model.model_type or "model"
        return ResourceSuggestion(
            uri=f"models.{model_type}.{model.id}",
            label=name,
            kind=model_type,
            description=file_stem if file_stem and file_stem != name else None,
            has_children=False,
            icon=self.icon,
            badge=model_type,
        )

    async def resolve(self, path: List[str], ctx: ResourceContext) -> Optional[ResolvedResource]:
        if not ctx.model_index_manager or not path:
            return None

        model_type = _normalize_type(path[0])
        if model_type is None or len(path) < 2:
            return None

        name = ".".join(path[1:])
        allowed = _allowed_ids(ctx)
        if allowed is not None and not allowed:
            return None
        repo = ctx.model_index_manager.model_repo

        model = None
        alternatives: List[str] = []
        by_id = repo.get_by_id(name, include_providers=True, include_tags=True, library_user_id=ctx.user_id)
        if by_id is not None and by_id.model_type == model_type and (allowed is None or by_id.id in allowed):
            model = by_id
        else:
            candidates = repo.get_all(
                model_type=model_type,
                search=name,
                limit=10,
                include_providers=True,
                include_tags=True,
                include_files=False,
                allowed_model_ids=allowed,
                library_user_id=ctx.user_id,
            )
            if not candidates:
                return None
            exact = [m for m in candidates if stem(m.filename).lower() == name.lower()]
            model = exact[0] if exact else candidates[0]
            alternatives = [stem(m.filename) for m in candidates if m.id != model.id]

        content = self._render(model, model_type, alternatives, overlay=user_overlay(ctx, model.id))
        return ResolvedResource(
            uri=f"models.{path[0]}.{name}",
            namespace=self.namespace,
            kind=model_type,
            title=_display_name(model),
            content=content[:MAX_CONTENT_CHARS],
            metadata={
                "model_id": model.id,
                "model_type": model.model_type,
                "filename": model.filename,
            },
        )

    @staticmethod
    def _render(
        model: Any,
        model_type: str,
        alternatives: List[str],
        overlay: Optional[Dict[str, Any]] = None,
    ) -> str:
        metadata = {**(model.model_metadata or {}), **(overlay or {})}
        resolved_type = model.model_type or model_type
        lines = [f"## Model: {stem(model.filename)}", ""]
        name = _text(getattr(model, "display_name", None))
        if name and name != stem(model.filename):
            lines.append(f"- Name: {name}")
        lines.append(f"- Type: {resolved_type}")
        if model.filename:
            lines.append(f"- File: {model.filename}")
        for key in _BASE_MODEL_KEYS:
            base = _text(metadata.get(key))
            if base:
                lines.append(f"- Base model: {base}")
                break

        triggers = list(dict.fromkeys(
            (metadata.get(_TRIGGERS_KEY) or [])
            + [tag for info in (model.providers or []) for tag in (info.tags or [])]
        ))
        if triggers:
            lines.append(f"- Trigger words: {', '.join(triggers[:50])}")

        strength = metadata.get(_STRENGTH_KEY)
        if isinstance(strength, (int, float)) and not isinstance(strength, bool):
            lines.append(f"- Recommended strength: {float(strength):g}")

        tags = [
            t for t in (
                _text(tag.get("name") if isinstance(tag, dict) else getattr(tag, "name", None))
                for tag in (model.tags if isinstance(getattr(model, "tags", None), list) else [])
            ) if t
        ]
        if tags:
            lines.append(f"- Tags: {', '.join(tags[:MAX_TAGS])}")

        if isinstance(getattr(model, "id", None), str) and model.id:
            ref = f"model:{model.id}"
            if resolved_type == "lora":
                lines.append(
                    f"- Form reference: `{ref}` (a LoRA field row is "
                    f'`{{"model": "{ref}", "strength": <weight>}}`)'
                )
            else:
                lines.append(f"- Form reference: `{ref}` (the value a model field takes)")

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
