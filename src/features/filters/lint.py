from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from src.features.filters.catalog import scan_filter_root
from src.features.filters.plugin_ops import plugin_filter_roots
from src.features.filters.schema import (
    BUILTIN_GROUPS,
    ERROR,
    NOTE,
    SOURCE_BUILTIN,
    SOURCE_LOCAL,
    SOURCE_PLUGIN,
    WARNING,
    FilterDefinition,
    Finding,
    warning,
)
from src.platform.imaging.filters import OpSpec, clipped_fraction, read_cube

GAMUT_WARN_FRACTION = 0.05

Root = Tuple[Path, str, str]


@dataclass
class LintReport:
    scanned: List[str] = field(default_factory=list)
    findings: Dict[str, List[Finding]] = field(default_factory=dict)

    @property
    def errors(self) -> List[Tuple[str, Finding]]:
        return [(path, f) for path, items in self.findings.items() for f in items if f.level == ERROR]

    @property
    def warnings(self) -> List[Tuple[str, Finding]]:
        return [(path, f) for path, items in self.findings.items() for f in items if f.level == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors


def _gamut_findings(definition: FilterDefinition, ops: Mapping[str, OpSpec]) -> List[Finding]:
    cube = None
    if definition.lut_path is not None:
        try:
            cube = read_cube(definition.lut_path)
        except Exception:
            return []
    try:
        fraction = clipped_fraction(definition.steps, cube, ops)
    except Exception:
        return []
    if fraction > GAMUT_WARN_FRACTION:
        return [
            warning(
                "preview_gamut",
                "steps",
                f"the compiled LUT clips {fraction:.0%} of its nodes; the extremes of the range are lost",
            )
        ]
    return []


def lint_roots(roots: Sequence[Root], ops: Mapping[str, OpSpec]) -> LintReport:
    report = LintReport()
    filters: Dict[str, FilterDefinition] = {}
    for path, source, plugin_id in roots:
        if path.is_dir():
            report.scanned.extend(
                scan_filter_root(path, source, filters, report.findings, ops, plugin_id or None)
            )

    group_counts: Dict[str, int] = {}
    for definition in filters.values():
        group_counts[definition.group] = group_counts.get(definition.group, 0) + 1
    for definition in filters.values():
        extra: List[Finding] = []
        if definition.group not in BUILTIN_GROUPS and group_counts[definition.group] == 1:
            extra.append(
                warning("group_new", "group", f"group '{definition.group}' is not used by any other filter; check for a typo")
            )
        extra.extend(_gamut_findings(definition, ops))
        if extra:
            report.findings.setdefault(definition.directory, []).extend(extra)
    return report


def format_finding(finding: Finding) -> str:
    level = {ERROR: "ERROR", WARNING: "WARN", NOTE: "NOTE"}.get(finding.level, finding.level.upper())
    where = f"{finding.path}: " if finding.path else ""
    return f"{level} filter.{finding.rule} {where}{finding.message}"


def default_roots(content_root: Path, manifests) -> List[Root]:
    roots: List[Root] = [
        (content_root / "marketplace", SOURCE_BUILTIN, ""),
        (content_root / "local", SOURCE_LOCAL, ""),
    ]
    for plugin_root in plugin_filter_roots(manifests):
        roots.append((plugin_root.path, SOURCE_PLUGIN, plugin_root.plugin_id))
    return roots
