from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from src.features.filters.plugin_ops import plugin_filter_ops, plugin_filter_roots
from src.features.filters.schema import (
    BUILTIN_GROUPS,
    ERROR,
    FILTER_FILE,
    NOTE,
    SOURCE_BUILTIN,
    SOURCE_LOCAL,
    SOURCE_PLUGIN,
    FilterDefinition,
    Finding,
    error,
    load_filter_dir,
)
from src.platform.imaging.filters import CORE_OPS, OpSpec, merged_ops

logger = logging.getLogger(__name__)

ROOTS = (("marketplace", SOURCE_BUILTIN), ("local", SOURCE_LOCAL))


def scan_filter_root(
    root: Path,
    source: str,
    filters: Dict[str, FilterDefinition],
    findings: Dict[str, List[Finding]],
    ops: Mapping[str, OpSpec],
    plugin_id: Optional[str] = None,
    label: Optional[str] = None,
) -> List[str]:
    scanned: List[str] = []
    try:
        entries = sorted(entry for entry in root.iterdir() if entry.is_dir() and not entry.name.startswith("."))
    except OSError:
        return scanned
    for directory in entries:
        key = f"{label}/{directory.name}" if label else str(directory)
        scanned.append(key)
        definition, issues = load_filter_dir(directory, source, ops, plugin_id)
        if definition is None:
            findings[key] = issues
            continue
        existing = filters.get(definition.public_id)
        if existing is not None:
            if existing.source == SOURCE_BUILTIN and source == SOURCE_LOCAL:
                definition.overrides = True
                issues = issues + [
                    Finding(NOTE, "id_taken", "id", f"overrides the marketplace filter '{existing.public_id}'")
                ]
            else:
                findings[key] = issues + [
                    error("id_taken", "id", f"duplicate filter id '{definition.public_id}' - already defined by the {existing.source} filter in '{Path(existing.directory).name}'")
                ]
                continue
        filters[definition.public_id] = definition
        if issues:
            findings[key] = issues
    return scanned


class FilterCatalog:
    def __init__(self, filters_dir: str = "content/filters", plugin_registry=None):
        self.filters_dir = Path(filters_dir)
        self.plugin_registry = plugin_registry
        self._lock = threading.RLock()
        self._filters: Dict[str, FilterDefinition] = {}
        self._findings: Dict[str, List[Finding]] = {}
        self._known_ops: Dict[str, OpSpec] = dict(CORE_OPS)
        self._enabled_ops: Dict[str, OpSpec] = dict(CORE_OPS)
        self._signature: Optional[Tuple] = None

    def _enabled_manifests(self) -> List:
        if self.plugin_registry is None:
            return []
        return list(self.plugin_registry.get_enabled_plugins())

    def _all_manifests(self) -> List:
        if self.plugin_registry is None:
            return []
        getter = getattr(self.plugin_registry, "get_all_plugins", None)
        return list(getter()) if callable(getter) else self._enabled_manifests()

    def _compute_signature(self, enabled: List) -> Tuple:
        parts: List[Tuple] = []
        roots = [self.filters_dir / name for name, _ in ROOTS] + [root.path for root in plugin_filter_roots(enabled)]
        for root in roots:
            try:
                root_stat = os.stat(root)
            except OSError:
                parts.append((str(root), None))
                continue
            parts.append((str(root), root_stat.st_mtime_ns))
            try:
                with os.scandir(root) as entries:
                    for entry in sorted(entries, key=lambda e: e.name):
                        if not entry.is_dir():
                            continue
                        for child in sorted(os.scandir(entry.path), key=lambda e: e.name):
                            if child.name == FILTER_FILE or child.name.endswith(".cube"):
                                stat = child.stat()
                                parts.append((child.path, stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
        manifests_part = tuple(
            (getattr(m, "id", ""), repr(getattr(m, "filter_ops", None)), repr(getattr(m, "filters", None)))
            for m in enabled
        )
        return tuple(parts), manifests_part

    def _ensure_fresh(self) -> None:
        with self._lock:
            enabled = self._enabled_manifests()
            signature = self._compute_signature(enabled)
            if signature != self._signature:
                self._reload(enabled, signature)

    def reload(self) -> None:
        with self._lock:
            enabled = self._enabled_manifests()
            self._reload(enabled, self._compute_signature(enabled))

    def _reload(self, enabled: List, signature: Tuple) -> None:
        known = merged_ops(plugin_filter_ops(self._all_manifests()))
        enabled_ops = merged_ops(plugin_filter_ops(enabled))
        filters: Dict[str, FilterDefinition] = {}
        findings: Dict[str, List[Finding]] = {}
        for name, source in ROOTS:
            root = self.filters_dir / name
            if root.is_dir():
                scan_filter_root(root, source, filters, findings, known, label=name)
        for plugin_root in plugin_filter_roots(enabled):
            if plugin_root.path.is_dir():
                scan_filter_root(
                    plugin_root.path, SOURCE_PLUGIN, filters, findings, known, plugin_root.plugin_id, label=plugin_root.plugin_id
                )
        self._filters = filters
        self._findings = findings
        self._known_ops = known
        self._enabled_ops = enabled_ops
        self._signature = signature
        failing = {key: issues for key, issues in findings.items() if any(i.level == ERROR for i in issues)}
        if failing:
            logger.warning("Filter catalog loaded with %d directory(ies) failing validation: %s", len(failing), list(failing))
        logger.info("Filter catalog loaded %d filter(s) from '%s'", len(filters), self.filters_dir)

    @property
    def load_errors(self) -> Dict[str, List[str]]:
        self._ensure_fresh()
        return {
            key: [str(issue) for issue in issues if issue.level == ERROR]
            for key, issues in self._findings.items()
            if any(i.level == ERROR for i in issues)
        }

    @property
    def findings(self) -> Dict[str, List[Finding]]:
        self._ensure_fresh()
        return dict(self._findings)

    def known_ops(self) -> Dict[str, OpSpec]:
        self._ensure_fresh()
        return dict(self._known_ops)

    def enabled_ops(self) -> Dict[str, OpSpec]:
        self._ensure_fresh()
        return dict(self._enabled_ops)

    def list_filters(self) -> List[FilterDefinition]:
        self._ensure_fresh()
        return sorted(self._filters.values(), key=lambda f: (f.order, f.name.casefold(), f.public_id))

    def get_filter(self, public_id: str) -> Optional[FilterDefinition]:
        self._ensure_fresh()
        return self._filters.get(public_id)

    def groups(self, extra: Optional[List[str]] = None) -> List[str]:
        present: List[str] = []
        for definition in self.list_filters():
            if definition.group not in present:
                present.append(definition.group)
        ordered = [g for g in BUILTIN_GROUPS if g in present]
        ordered += [g for g in present if g not in BUILTIN_GROUPS]
        for group in extra or []:
            if group not in ordered:
                ordered.append(group)
        return ordered
