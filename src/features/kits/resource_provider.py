import asyncio
from typing import List, Optional

from src.features.kits import citations
from src.features.kits.catalog import ItemView, KitCatalog, KitView
from src.features.kits.repository import KitRepository
from src.platform.resources.base import (
    BaseResourceProvider,
    ResolvedResource,
    ResourceContext,
    ResourceSuggestion,
)

KIND_ICONS = {"image": "image", "video": "video", "audio": "audio-lines", "text": "type"}
TEXT_PREVIEW_CHARS = 600


def _readable(kit_view: KitView, view: Optional[ItemView] = None) -> str:
    return f"kits.{kit_view.kit.handle}" + (f".{view.item.name}" if view else "")


class KitsResourceProvider(BaseResourceProvider):
    icon = "package"

    def __init__(self, repository: KitRepository):
        self.repository = repository

    @property
    def namespace(self) -> str:
        return "kits"

    @property
    def display_name(self) -> str:
        return "Kits"

    def _lookup(self, segment: str, catalog: KitCatalog) -> Optional[KitView]:
        reference = citations.parse_stable(segment)
        if reference is not None:
            return catalog.by_id(reference.kit_id)
        return catalog.by_handle(segment)

    @staticmethod
    def _item(kit_view: KitView, segment: str) -> Optional[ItemView]:
        if segment.startswith("it_"):
            return kit_view.item(segment)
        return kit_view.item_named(segment)

    def _kit_suggestion(self, kit_view: KitView) -> ResourceSuggestion:
        kit = kit_view.kit
        counts = {}
        for view in kit_view.items:
            counts[view.item.kind] = counts.get(view.item.kind, 0) + 1
        summary = ", ".join(f"{count} {kind}" for kind, count in counts.items())
        return ResourceSuggestion(
            uri=_readable(kit_view),
            label=kit.name,
            kind="kit",
            description=summary or "Empty kit",
            has_children=True,
            attachable=True,
            icon=self.icon,
            badge=kit.label,
            ref=citations.item_key(kit.id),
        )

    def _item_suggestion(self, kit_view: KitView, view: ItemView) -> ResourceSuggestion:
        item = view.item
        description = item.text[:80] if item.is_text and item.text else ("File deleted" if view.missing else item.kind)
        return ResourceSuggestion(
            uri=_readable(kit_view, view),
            label=item.name,
            kind=f"kit_{item.kind}",
            description=description,
            has_children=False,
            icon=KIND_ICONS[item.kind],
            ref=citations.item_key(kit_view.kit.id, item.id),
        )

    def _suggest(self, path: List[str], partial: str, ctx: ResourceContext, limit: int) -> List[ResourceSuggestion]:
        catalog = KitCatalog(self.repository, ctx.user_id)
        needle = partial.lower()
        if not path:
            kits, _total = self.repository.list_kits(ctx.user_id, search=needle or None, limit=limit)
            return [self._kit_suggestion(catalog.by_id(kit.id)) for kit in kits]
        kit_view = self._lookup(path[0], catalog)
        if kit_view is None or len(path) > 1:
            return []
        return [
            self._item_suggestion(kit_view, view) for view in kit_view.items
            if not needle or needle in view.item.name.lower()
        ][:limit]

    async def suggest(
        self, path: List[str], partial: str, ctx: ResourceContext, limit: int = 15,
    ) -> List[ResourceSuggestion]:
        return await asyncio.to_thread(self._suggest, path, partial, ctx, limit)

    def _missing(self, path: List[str], what: str) -> ResolvedResource:
        uri = ".".join(["kits", *path])
        return ResolvedResource(
            uri=uri, namespace=self.namespace, kind="missing", title=uri,
            content=f"@{uri} points to a kit {what} that no longer exists.",
            metadata={"missing": True},
        )

    @staticmethod
    def _describe(view: ItemView) -> str:
        item = view.item
        if item.is_text:
            return (item.text or "")[:TEXT_PREVIEW_CHARS]
        if view.missing:
            return f"{item.kind} (its Library file was deleted)"
        return f"{item.kind}, {view.upload.original_filename or view.upload.filename}"

    def _resolve(self, path: List[str], ctx: ResourceContext) -> Optional[ResolvedResource]:
        if not path:
            return None
        catalog = KitCatalog(self.repository, ctx.user_id)
        kit_view = self._lookup(path[0], catalog)
        if kit_view is None:
            return self._missing(path, "or item")
        kit = kit_view.kit
        base = {"kit_id": kit.id, "handle": kit.handle, "label": kit.label}
        if len(path) == 1:
            lines = [f"## Kit: {kit.name} (@{_readable(kit_view)})" + (f" [{kit.label}]" if kit.label else "")]
            lines += [f"- {view.item.name}: {self._describe(view)}" for view in kit_view.items] or ["(empty)"]
            return ResolvedResource(
                uri=_readable(kit_view), namespace=self.namespace, kind="kit", title=kit.name,
                content="\n".join(lines),
                metadata={**base, "ref": citations.item_key(kit.id), "count": len(kit_view.items)},
            )
        view = self._item(kit_view, path[1])
        if view is None:
            return self._missing(path, "item")
        item = view.item
        return ResolvedResource(
            uri=_readable(kit_view, view), namespace=self.namespace, kind=f"kit_{item.kind}",
            title=f"{kit.name} / {item.name}",
            content=f"## {item.name} ({kit.name})\n{self._describe(view)}",
            metadata={
                **base, "item_id": item.id, "name": item.name, "item_kind": item.kind, "missing": view.missing,
                "ref": citations.item_key(kit.id, item.id),
            },
        )

    async def resolve(self, path: List[str], ctx: ResourceContext) -> Optional[ResolvedResource]:
        return await asyncio.to_thread(self._resolve, path, ctx)
