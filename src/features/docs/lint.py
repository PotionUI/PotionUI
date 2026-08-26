"""Typed-documentation linter core (Docs 2.0).

Validates every ``type: technique`` / ``type: model`` doc under ``docs/techniques``
and ``docs/models``: schema errors, unknown family_keys, bad enum values, broken
``related:`` slugs, malformed arxiv ids, and unknown-key / missing-recommended
warnings. Lives in core so BOTH the CLI (``scripts/docs_lint.py``) and the
developer API endpoint (``GET /api/developer/docs/lint``) share one implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from src.features.docs.frontmatter import parse_frontmatter
from src.features.docs.typed import (
    ALL_NATIVE,
    KNOB_SURFACES,
    MODEL,
    MODEL_ENGINES,
    TECHNIQUE,
    TECHNIQUE_CATEGORY_GROUPS,
    TECHNIQUE_STATUSES,
    ModelMeta,
    TechniqueMeta,
    parse_typed,
)

_ARXIV_RE = re.compile(r"^(arxiv:)?\d{4}\.\d{4,5}(v\d+)?$", re.IGNORECASE)

ERROR = "error"
WARNING = "warning"


@dataclass
class LintIssue:
    level: str      # "error" | "warning"
    path: str       # doc path (posix)
    message: str


@dataclass
class LintReport:
    issues: List[LintIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[LintIssue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> List[LintIssue]:
        return [i for i in self.issues if i.level == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        """API-friendly shape, mirroring get_presets_lint's conventions."""
        return {
            "issues": [{"level": i.level, "path": i.path, "message": i.message} for i in self.issues],
            "total_errors": len(self.errors),
            "total_warnings": len(self.warnings),
        }


def known_family_keys() -> Set[str]:
    """Valid ``family_key`` values: the native detect-registry families + sdxl."""
    from src.platform.runtime.native.detect.registry import arch_registry

    return {spec.family for spec in arch_registry.all()} | {"sdxl"}


def _schema_fields(doc_type: str) -> Set[str]:
    model = TechniqueMeta if doc_type == TECHNIQUE else ModelMeta
    return set(model.model_fields)


def _iter_typed_docs(docs_root: Path) -> List[Tuple[Path, str, Dict[str, Any]]]:
    out: List[Tuple[Path, str, Dict[str, Any]]] = []
    for subdir in ("techniques", "models"):
        section = docs_root / subdir
        if not section.is_dir():
            continue
        for md in sorted(set(section.glob("*.md")) | set(section.glob("*/*.md"))):
            frontmatter, _body = parse_frontmatter(md.read_text(encoding="utf-8"))
            doc_type = frontmatter.get("type")
            if doc_type in (TECHNIQUE, MODEL):
                out.append((md, doc_type, frontmatter))
    return out


def lint_docs(docs_root: str | Path) -> LintReport:
    """Lint every typed doc under ``docs_root``; return a :class:`LintReport`."""
    docs_root = Path(docs_root)
    report = LintReport()
    families = known_family_keys()

    docs = _iter_typed_docs(docs_root)
    technique_slugs = {md.stem for md, dt, _ in docs if dt == TECHNIQUE}

    for md, doc_type, fm in docs:
        rel = md.as_posix()

        def err(msg: str) -> None:
            report.issues.append(LintIssue(ERROR, rel, msg))

        def warn(msg: str) -> None:
            report.issues.append(LintIssue(WARNING, rel, msg))

        _dt, meta, parse_errors = parse_typed(fm)
        for e in parse_errors:
            err(f"schema: {e}")
        if meta is None:
            continue

        for key in fm:
            if key not in _schema_fields(doc_type):
                warn(f"unknown frontmatter key '{key}'")

        if doc_type == TECHNIQUE:
            _lint_technique(meta, families, technique_slugs, err, warn)
        else:
            _lint_model(meta, families, err)

    return report


def _lint_technique(meta, families, technique_slugs, err, warn) -> None:
    if meta["category_group"] not in TECHNIQUE_CATEGORY_GROUPS:
        err(f"category_group '{meta['category_group']}' not in {list(TECHNIQUE_CATEGORY_GROUPS)}")
    if meta["status"] not in TECHNIQUE_STATUSES:
        err(f"status '{meta['status']}' not in {list(TECHNIQUE_STATUSES)}")
    for fam in meta.get("families", []):
        if fam != ALL_NATIVE and fam not in families:
            err(f"unknown family_key '{fam}' (valid: {sorted(families) + [ALL_NATIVE]})")
    if not meta.get("families"):
        warn("technique lists no families")
    for knob in meta.get("knobs", []):
        if knob.get("surface") not in KNOB_SURFACES:
            err(f"knob '{knob.get('key')}' surface '{knob.get('surface')}' not in {list(KNOB_SURFACES)}")
    paper = meta.get("paper")
    if paper and paper.get("arxiv") and not _ARXIV_RE.match(str(paper["arxiv"])):
        err(f"paper.arxiv '{paper['arxiv']}' is not a valid arxiv id (e.g. 2505.21179)")
    for slug in meta.get("related", []):
        if slug not in technique_slugs:
            err(f"related slug '{slug}' does not resolve to a technique doc")
    if not meta.get("authors"):
        warn("no authors listed (recommended)")
    if not meta.get("paper"):
        warn("no paper reference (recommended)")


def _lint_model(meta, families, err) -> None:
    if meta["family_key"] not in families:
        err(f"unknown family_key '{meta['family_key']}' (valid: {sorted(families)})")
    engine = (meta.get("spec") or {}).get("engine", "native")
    if engine not in MODEL_ENGINES:
        err(f"spec.engine '{engine}' not in {list(MODEL_ENGINES)}")
