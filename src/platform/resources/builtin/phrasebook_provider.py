"""@phrasebook resource provider.

Paths mirror the phrasebook category tree's dotted ``path`` field:
``phrasebook.camera.angles`` resolves to that category's orientation
snapshot (state, total value count, subcategories, and a small sample);
``phrasebook.camera.angles.<value label>`` resolves to a single value.

A category can hold hundreds of values, so resolving it never dumps the
full value list into the conversation — that would blow up context for no
benefit. The resolved content is for orientation only; the LLM pages through
the actual data with the ``list_phrasebook_values`` tool (search +
offset/limit) when it needs more than the sample.
"""

import logging
from typing import Any, Dict, List, Optional

from src.platform.resources.base import (
    BaseResourceProvider,
    ResolvedResource,
    ResourceContext,
    ResourceSuggestion,
)

logger = logging.getLogger(__name__)

SAMPLE_SIZE = 20


class PhrasebookResourceProvider(BaseResourceProvider):
    """Exposes phrasebook categories and their values."""

    icon = "list"

    @property
    def namespace(self) -> str:
        return "phrasebook"

    async def suggest(
        self,
        path: List[str],
        partial: str,
        ctx: ResourceContext,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        if not ctx.phrasebook_manager:
            return []

        query = ".".join(path + [partial]) if (path or partial) else ""
        result = ctx.phrasebook_manager.search_phrasebook(
            query, ctx.user_id, limit=limit
        )

        suggestions: List[ResourceSuggestion] = []
        current = result.get("current_category")

        if current:
            # Every phrasebook category is resolvable at its own path (see
            # resolve()), so the just-typed category itself must be offered as
            # a selectable entry — otherwise typing the full path only ever
            # surfaces its children/values and there is no way to attach the
            # category as a whole. Listed first so it's the obvious default.
            suggestions.append(ResourceSuggestion(
                uri=f"phrasebook.{current['path']}",
                label=current["name"],
                kind="category",
                description="Attach this whole category",
                has_children=True,
                attachable=True,
                icon=self.icon,
            ))

        for cat in result.get("child_categories", []):
            # NOT attachable: these are being browsed, not yet the typed path
            # (root-level matches, or a just-typed category's subcategories).
            # Clicking one must still navigate deeper, exactly as before —
            # otherwise a user drilling toward a nested value can never get
            # past the first level, since every click along the way would
            # attach instead of descending. A child becomes attachable once
            # the user has actually navigated to it (it's then the exact
            # match and gets its own self-entry above).
            suggestions.append(ResourceSuggestion(
                uri=f"phrasebook.{cat['path']}",
                label=cat["name"],
                kind="category",
                description=cat.get("description"),
                has_children=True,
                attachable=False,
                icon=self.icon,
            ))
        if current:
            for val in result.get("values", [])[:limit]:
                suggestions.append(ResourceSuggestion(
                    uri=f"phrasebook.{current['path']}.{val['label']}",
                    label=val["label"],
                    kind="value",
                    description=val.get("value"),
                    has_children=False,
                ))
        return suggestions[:limit]

    async def resolve(self, path: List[str], ctx: ResourceContext) -> Optional[ResolvedResource]:
        if not ctx.phrasebook_manager or not path:
            return None

        manager = ctx.phrasebook_manager
        full_path = ".".join(path)

        # Exact category → orientation snapshot (state, counts, sample)
        category = manager.categories.get_by_path(full_path, ctx.user_id)
        if category:
            values = manager.values.get_by_category(category.id, ctx.user_id)
            children = manager.categories.get_children(category.id, ctx.user_id)
            children_summary = [
                {
                    "name": child.name,
                    "path": child.path,
                    "value_count": len(manager.values.get_by_category(child.id, ctx.user_id)),
                }
                for child in children
            ]
            return self._category_resource(full_path, category, values, children_summary)

        # Parent category + trailing value label
        if len(path) >= 2:
            parent_path = ".".join(path[:-1])
            label = path[-1]
            category = manager.categories.get_by_path(parent_path, ctx.user_id)
            if category:
                values = manager.values.get_by_category(category.id, ctx.user_id)
                matches = [v for v in values if v.label.lower() == label.lower()]
                if matches:
                    value = matches[0]
                    state = "active" if getattr(value, "is_active", True) else "inactive"
                    return ResolvedResource(
                        uri=f"phrasebook.{full_path}",
                        namespace=self.namespace,
                        kind="value",
                        title=value.label,
                        content=(
                            f"Phrasebook value from category '{parent_path}':\n"
                            f"- id={value.id} {value.label}: {value.value} ({state})"
                        ),
                        metadata={"category_id": category.id, "value_id": value.id},
                    )
        return None

    def _category_resource(
        self, full_path: str, category: Any, values: List[Any], children_summary: List[Dict[str, Any]]
    ) -> ResolvedResource:
        cat_state = "active" if getattr(category, "is_active", True) else "inactive"
        lines = [
            f"## Phrasebook category: {full_path}",
            f"State: {cat_state}",
            f"Total values: {len(values)}",
        ]
        if category.description:
            lines.append(category.description)
        if children_summary:
            lines.append("")
            lines.append(f"Subcategories ({len(children_summary)}):")
            for child in children_summary:
                lines.append(f"- {child['name']} ({child['path']}): {child['value_count']} values")

        sample = values[:SAMPLE_SIZE]
        lines.append("")
        if sample:
            lines.append(f"Sample of {len(sample)} (of {len(values)}) values (id — label: value [state]):")
            for val in sample:
                val_state = "active" if getattr(val, "is_active", True) else "inactive"
                lines.append(f"- id={val.id} {val.label}: {val.value} ({val_state})")
        if len(values) > len(sample):
            lines.append(
                f"…and {len(values) - len(sample)} more. This is a sample, not the full list — use "
                "list_phrasebook_values (with search or offset/limit paging) to see the rest."
            )

        return ResolvedResource(
            uri=f"phrasebook.{full_path}",
            namespace=self.namespace,
            kind="category",
            title=category.name,
            content="\n".join(lines),
            metadata={
                "category_id": category.id,
                "value_count": len(values),
                "sample_count": len(sample),
                "subcategory_count": len(children_summary),
            },
        )
