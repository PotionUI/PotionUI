"""@presets resource provider.

Paths: ``presets.<preset_id>[.<form_field>]``. Preset ids are ULIDs (dot-free),
so dot-splitting is safe; the optional trailing segment is a form field name
whose option list becomes the resolved content.
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

MAX_OPTIONS = 50


class PresetsResourceProvider(BaseResourceProvider):
    """Exposes presets and their form-field option lists."""

    icon = "settings-2"

    @property
    def namespace(self) -> str:
        return "presets"

    async def suggest(
        self,
        path: List[str],
        partial: str,
        ctx: ResourceContext,
        limit: int = 15,
    ) -> List[ResourceSuggestion]:
        if not ctx.preset_manager:
            return []

        if not path:
            needle = partial.lower()
            presets = ctx.preset_manager.file_repo.list_all_presets()
            return [
                ResourceSuggestion(
                    uri=f"presets.{p['id']}",
                    label=p.get("name") or p["id"],
                    kind="preset",
                    description=p.get("description"),
                    has_children=True,
                    icon=self.icon,
                )
                for p in presets
                if not needle
                or needle in (p.get("name") or "").lower()
                or needle in p["id"].lower()
            ][:limit]

        preset = self._get_preset(ctx, path[0])
        if not preset:
            return []
        needle = partial.lower()
        suggestions = []
        for field_item in self._option_fields(preset):
            name = field_item.get("name", "")
            if needle and needle not in name.lower() and needle not in (field_item.get("label") or "").lower():
                continue
            suggestions.append(ResourceSuggestion(
                uri=f"presets.{path[0]}.{name}",
                label=field_item.get("label") or name,
                kind="form_field",
                description=f"{len(field_item.get('options') or [])} options",
                has_children=False,
            ))
        return suggestions[:limit]

    async def resolve(self, path: List[str], ctx: ResourceContext) -> Optional[ResolvedResource]:
        if not ctx.preset_manager or not path:
            return None

        preset = self._get_preset(ctx, path[0])
        if not preset:
            return None

        if len(path) == 1:
            return self._preset_summary(path[0], preset)

        field_name = ".".join(path[1:])
        for field_item in self._option_fields(preset):
            if field_item.get("name") == field_name:
                return self._field_options(path[0], preset, field_item)
        return None

    @staticmethod
    def _get_preset(ctx: ResourceContext, preset_id: str) -> Optional[Dict[str, Any]]:
        try:
            data = ctx.preset_manager.get_preset(preset_id)
            return data.get("preset", data)
        except Exception:
            logger.debug(f"Preset '{preset_id}' not found for @resource resolution")
            return None

    @staticmethod
    def _option_fields(preset: Dict[str, Any]) -> List[Dict[str, Any]]:
        form = preset.get("form")
        if not isinstance(form, list):
            return []
        return [f for f in form if isinstance(f.get("options"), list) and f["options"]]

    def _preset_summary(self, preset_id: str, preset: Dict[str, Any]) -> ResolvedResource:
        lines = [f"## Preset: {preset.get('name', preset_id)}"]
        if preset.get("description"):
            lines.append(preset["description"])
        modes = preset.get("modes")
        if modes:
            mode_names = list(modes.keys()) if isinstance(modes, dict) else modes
            lines.append(f"- Modes: {', '.join(str(m) for m in mode_names)}")
        option_fields = self._option_fields(preset)
        if option_fields:
            lines.append("- Form fields with options:")
            for f in option_fields:
                lines.append(f"  - {f.get('name')}: {len(f.get('options') or [])} options")
        return ResolvedResource(
            uri=f"presets.{preset_id}",
            namespace=self.namespace,
            kind="preset",
            title=preset.get("name", preset_id),
            content="\n".join(lines),
            metadata={"preset_id": preset_id},
        )

    def _field_options(
        self, preset_id: str, preset: Dict[str, Any], field_item: Dict[str, Any]
    ) -> ResolvedResource:
        name = field_item.get("name", "")
        options = field_item.get("options") or []
        lines = [
            f"## Preset '{preset.get('name', preset_id)}' — field '{field_item.get('label') or name}' options",
            "",
        ]
        for opt in options[:MAX_OPTIONS]:
            if isinstance(opt, dict):
                lines.append(f"- {opt.get('label', '')}: {opt.get('value', '')}")
            else:
                lines.append(f"- {opt}")
        if len(options) > MAX_OPTIONS:
            lines.append(f"…and {len(options) - MAX_OPTIONS} more")
        return ResolvedResource(
            uri=f"presets.{preset_id}.{name}",
            namespace=self.namespace,
            kind="form_field",
            title=f"{preset.get('name', preset_id)} · {field_item.get('label') or name}",
            content="\n".join(lines),
            metadata={"preset_id": preset_id, "field": name, "option_count": len(options)},
        )
