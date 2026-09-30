from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from src.platform.plugins.manifest_roots import PluginManifestRoot, plugin_manifest_roots

from src.features.model_layouts.schema import (
    MAX_FILE_BYTES,
    SOURCE_LOCAL,
    SOURCE_MARKETPLACE,
    SOURCE_PLUGIN,
    ModelLayout,
    parse_layout,
    validate_layout_dict,
)

logger = logging.getLogger(__name__)

_ROOTS = ("marketplace", "local")


def plugin_model_layout_roots(manifests) -> List[PluginManifestRoot]:
    return plugin_manifest_roots(manifests, "model_layouts")


def read_layout_file(
    path: Path, source: str, plugin_id: Optional[str] = None
) -> "tuple[Optional[ModelLayout], List[str]]":
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, [f"Could not read file: {exc}"]
    if size > MAX_FILE_BYTES:
        return None, [f"File is {size} bytes; the limit is {MAX_FILE_BYTES}"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return None, [f"Could not parse YAML: {exc}"]
    issues = validate_layout_dict(data)
    if issues:
        return None, issues
    if path.stem != data["id"]:
        return None, [f"id: file name '{path.stem}' must equal the layout id '{data['id']}'"]
    try:
        return parse_layout(data, source_path=str(path), source=source, plugin_id=plugin_id), []
    except Exception as exc:
        return None, [f"Failed to parse a validated layout: {exc}"]


def scan_layout_root(
    root: Path,
    source: str,
    layouts: Dict[str, ModelLayout],
    errors: Dict[str, List[str]],
    plugin_id: Optional[str] = None,
) -> List[str]:
    scanned: List[str] = []
    for path in sorted(root.glob("*.yml")):
        key = str(path)
        scanned.append(key)
        layout, issues = read_layout_file(path, source, plugin_id)
        if layout is None:
            errors[key] = issues
            continue
        existing = layouts.get(layout.id)
        if existing is not None:
            errors[key] = [f"Duplicate layout id '{layout.id}' - already defined by {existing.source_path}"]
            continue
        layouts[layout.id] = layout
    return scanned


class ModelLayoutCatalog:
    def __init__(self, layouts_dir: str = "content/model-layouts", plugin_registry=None):
        self.layouts_dir = Path(layouts_dir)
        self.plugin_registry = plugin_registry
        self._layouts: Dict[str, ModelLayout] = {}
        self._load_errors: Dict[str, List[str]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()

    @property
    def load_errors(self) -> Dict[str, List[str]]:
        self._ensure_loaded()
        return self._load_errors

    def reload(self) -> None:
        layouts: Dict[str, ModelLayout] = {}
        errors: Dict[str, List[str]] = {}

        for root_name in _ROOTS:
            root = self.layouts_dir / root_name
            if root.is_dir():
                scan_layout_root(
                    root, layouts=layouts, errors=errors,
                    source=SOURCE_LOCAL if root_name == "local" else SOURCE_MARKETPLACE,
                )

        if self.plugin_registry is not None:
            for plugin_root in plugin_model_layout_roots(self.plugin_registry.get_enabled_plugins()):
                if plugin_root.path.is_dir():
                    scan_layout_root(
                        plugin_root.path, layouts=layouts, errors=errors,
                        source=SOURCE_PLUGIN, plugin_id=plugin_root.plugin_id,
                    )

        self._layouts = layouts
        self._load_errors = errors
        self._loaded = True

        if errors:
            logger.warning("Model layout catalog loaded with %d file(s) failing validation: %s", len(errors), list(errors))
        logger.info("Model layout catalog loaded %d layout(s) from '%s'", len(layouts), self.layouts_dir)

    def list_layouts(self) -> List[ModelLayout]:
        self._ensure_loaded()
        return sorted(self._layouts.values(), key=lambda layout: layout.id)

    def get_layout(self, layout_id: str) -> Optional[ModelLayout]:
        self._ensure_loaded()
        return self._layouts.get(layout_id)
