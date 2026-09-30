from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginManifestRoot:
    plugin_id: str
    path: Path


def plugin_manifest_roots(manifests, attr: str) -> List[PluginManifestRoot]:
    roots: List[PluginManifestRoot] = []
    for manifest in manifests:
        entries = getattr(manifest, attr, None) or []
        plugin_dir = getattr(manifest, "plugin_dir", None)
        if not entries or not plugin_dir:
            continue
        base = Path(plugin_dir).resolve()
        plugin_id = getattr(manifest, "id", "") or ""
        for entry in entries:
            path = entry.get("path") if isinstance(entry, dict) else None
            if not path:
                continue
            candidate = (base / path).resolve()
            if candidate != base and base not in candidate.parents:
                logger.warning("Plugin '%s' %s root '%s' escapes the plugin directory; skipped", plugin_id, attr, path)
                continue
            roots.append(PluginManifestRoot(plugin_id=plugin_id, path=candidate))
    return roots
