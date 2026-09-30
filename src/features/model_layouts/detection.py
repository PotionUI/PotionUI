from __future__ import annotations

import os
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.features.model_layouts.readers.base import ReaderResult, find_child_ci
from src.features.model_layouts.readers.registry import run_reader
from src.features.model_layouts.schema import GENERIC_LAYOUT_ID, LayoutFolder, ModelLayout
from src.features.models import root_detection
from src.platform.filesystem.model_roots import (
    paths_overlap,
    probe_case_insensitive,
    root_path_key,
)
from src.platform.filesystem.model_types import SUPPORTED_MODEL_EXTENSIONS, binding_scans_headers_by_default

DETECTION_BUDGET_SECONDS = 6.0
COUNT_LIMIT = 10_000
COUNT_TIME_LIMIT_SECONDS = 2.0
PRESENCE_ENTRY_LIMIT = 5_000
PRESENCE_CLOCK_INTERVAL = 16
EVIDENCE_LIMIT = 12
GENERIC_LABEL = "Generic (folder names)"


class UnknownLayoutError(Exception):
    code = "model_layout_unknown"

    def __init__(self, layout_id: str):
        super().__init__(f"Unknown model layout '{layout_id}'")
        self.layout_id = layout_id


@dataclass
class _Run:
    clock: Callable[[], float]
    deadline: float
    translator: Any = None
    resolver: Any = None

    def remaining(self) -> float:
        return self.deadline - self.clock()


@dataclass
class _Score:
    layout: ModelLayout
    install: Path
    models: Optional[Path]
    marker_score: int = 0
    folder_evidence: int = 0
    evidence: List[str] = field(default_factory=list)
    confidence: Optional[str] = None

    @property
    def total(self) -> int:
        return self.marker_score + self.folder_evidence

    def rank_key(self) -> Tuple[int, int, int, str]:
        return (0 if self.confidence == "strong" else 1, -self.total, -self.layout.priority, self.layout.id)


@dataclass
class _Sug:
    model_type: str
    path: Path
    subdir: str
    label: str
    scan_headers: bool
    matched_by: str
    source: str
    write: bool = False
    file_count: int = 0
    file_count_truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_type": self.model_type,
            "subdir": self.subdir,
            "label": self.label,
            "write": self.write,
            "scan_headers": self.scan_headers,
            "matched_by": self.matched_by,
            "source": self.source,
            "file_count": self.file_count,
            "file_count_truncated": self.file_count_truncated,
        }


def _fold(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _fold_parts(path: Path) -> Tuple[str, ...]:
    return tuple(_fold(part) for part in path.parts)


def _segments(value: str) -> Tuple[str, ...]:
    return tuple(_fold(part) for part in value.split("/") if part)


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _inside(base: Path, target: Path) -> bool:
    anchor = os.path.normcase(os.path.normpath(str(base)))
    other = os.path.normcase(os.path.normpath(str(target)))
    try:
        return os.path.commonpath([anchor, other]) == anchor
    except ValueError:
        return False


def _posix_rel(base: Path, target: Path) -> str:
    return PurePosixPath(os.path.relpath(str(target), str(base)).replace("\\", "/")).as_posix()


def _has_model_file(run: "_Run", directory: Path) -> bool:
    seen = 0
    try:
        for entry in directory.rglob("*"):
            seen += 1
            if seen > PRESENCE_ENTRY_LIMIT:
                return False
            if seen % PRESENCE_CLOCK_INTERVAL == 0 and run.remaining() <= 0:
                return False
            if entry.is_file() and entry.suffix.lower() in SUPPORTED_MODEL_EXTENSIONS:
                return True
    except OSError:
        return False
    return False


def _count_files(run: _Run, directory: Path) -> Tuple[int, bool]:
    remaining = run.remaining()
    if remaining <= 0:
        return 0, True
    limit = min(COUNT_TIME_LIMIT_SECONDS, remaining)
    start = run.clock()
    count = 0
    try:
        for entry in directory.rglob("*"):
            if count >= COUNT_LIMIT or (run.clock() - start) > limit:
                return count, True
            if entry.is_file() and entry.suffix.lower() in SUPPORTED_MODEL_EXTENSIONS:
                count += 1
    except OSError:
        pass
    return count, False


def _dir_variants(parent: Path, relative: str) -> List[Path]:
    segments = [part for part in relative.split("/") if part]
    if not segments:
        return []
    current = find_child_ci(parent, "/".join(segments[:-1])) if len(segments) > 1 else parent
    if current is None or not current.is_dir():
        return []
    last = segments[-1]
    found: List[Path] = []
    exact = current / last
    if exact.is_dir():
        found.append(exact)
    wanted = _fold(last)
    try:
        for child in sorted(current.iterdir()):
            if child.is_dir() and _fold(child.name) == wanted and not any(_same(child, item) for item in found):
                found.append(child)
    except OSError:
        pass
    return found


def _models_dir(layout: ModelLayout, install: Path) -> Optional[Path]:
    for candidate in layout.models_root:
        if candidate == ".":
            return install
        found = find_child_ci(install, candidate)
        if found is not None and found.is_dir():
            return found
    return None


def _anchors(layout: ModelLayout, root: Path) -> List[Tuple[Path, Optional[Path]]]:
    anchors: List[Tuple[Path, Optional[Path]]] = []

    def add(install: Path, models: Optional[Path]) -> None:
        if not any(_same(install, i) and ((m is None) == (models is None)) and (m is None or _same(m, models)) for i, m in anchors):
            anchors.append((install, models))

    for directory in layout.install_dirs:
        install = root if directory == "." else find_child_ci(root, directory)
        if install is not None and install.is_dir():
            add(install, _models_dir(layout, install))

    parts = _fold_parts(root)
    for candidate in layout.models_root:
        if candidate == ".":
            continue
        wanted = _segments(candidate)
        if wanted and len(parts) > len(wanted) and parts[-len(wanted):] == wanted:
            install = root
            for _ in wanted:
                install = install.parent
            add(install, root)

    if not anchors:
        add(root, None)
    return anchors


def _folder_parent(folder: LayoutFolder, install: Path, models: Optional[Path]) -> Optional[Path]:
    return install if folder.base == "install" else models


def _score_anchor(run: _Run, layout: ModelLayout, install: Path, models: Optional[Path]) -> _Score:
    score = _Score(layout=layout, install=install, models=models)
    for marker in layout.markers:
        found = find_child_ci(install, marker.path)
        if found is None:
            continue
        if (marker.kind == "dir" and found.is_dir()) or (marker.kind == "file" and found.is_file()):
            score.marker_score += marker.weight
            score.evidence.append(marker.path)
    for folder in layout.folders:
        parent = _folder_parent(folder, install, models)
        if parent is None:
            continue
        variants = _dir_variants(parent, folder.path)
        if not variants:
            continue
        score.folder_evidence += folder.weight + (1 if _has_model_file(run, variants[0]) else 0)
        score.evidence.append(folder.path)
    if layout.markers and score.marker_score >= layout.min_marker_score:
        score.confidence = "strong"
    elif layout.match_on_folders and score.folder_evidence >= layout.min_folder_evidence:
        score.confidence = "weak"
    return score


def _best_score(run: _Run, layout: ModelLayout, root: Path) -> _Score:
    scores = [_score_anchor(run, layout, install, models) for install, models in _anchors(layout, root)]
    return max(scores, key=lambda s: s.total)


def _rank(run: _Run, layouts: Sequence[ModelLayout], root: Path) -> List[_Score]:
    matches = [s for s in (_best_score(run, layout, root) for layout in layouts) if s.confidence is not None]
    return sorted(matches, key=lambda s: s.rank_key())


def _variant_label(score: _Score) -> Optional[str]:
    for variant in score.layout.variants:
        if all(find_child_ci(score.install, marker.path) is not None for marker in variant.markers):
            return variant.label
    return None


def _profile_dict(score: _Score) -> Dict[str, Any]:
    layout = score.layout
    return {
        "id": layout.id,
        "label": layout.label,
        "variant": _variant_label(score),
        "confidence": score.confidence,
        "score": score.total,
        "source": layout.source,
        "install_path": str(score.install),
        "models_path": str(score.models) if score.models is not None else None,
        "evidence": score.evidence[:EVIDENCE_LIMIT],
    }


def _alternative(score: _Score) -> Dict[str, Any]:
    return {
        "id": score.layout.id,
        "label": score.layout.label,
        "confidence": score.confidence,
        "score": score.total,
    }


def _generic_alternative() -> Dict[str, Any]:
    return {"id": GENERIC_LAYOUT_ID, "label": GENERIC_LABEL, "confidence": None, "score": 0}


def _assign_writes(suggestions: List[_Sug], flagged: Sequence[Path]) -> None:
    by_type: Dict[str, List[_Sug]] = {}
    for suggestion in suggestions:
        by_type.setdefault(suggestion.model_type, []).append(suggestion)
    for group in by_type.values():
        chosen = next((s for s in group if any(_same(s.path, path) for path in flagged)), group[0])
        chosen.write = True


def _folder_suggestions(
    run: _Run,
    layout: ModelLayout,
    install: Path,
    models: Optional[Path],
    root: Path,
    warnings: List[str],
    source: str = "profile",
) -> Tuple[List[_Sug], List[Dict[str, Any]]]:
    suggestions: List[_Sug] = []
    outside: List[Dict[str, Any]] = []
    flagged: List[Path] = []
    seen_real: List[str] = []

    for folder in layout.folders:
        parent = _folder_parent(folder, install, models)
        if parent is None:
            continue
        variants = _dir_variants(parent, folder.path)
        if not variants:
            continue
        primary = variants[0]
        kept = [primary] + [v for v in variants[1:] if _has_model_file(run, v)]
        if len(kept) > 1:
            warnings.append(
                f"'{folder.path}' exists in more than one spelling ({', '.join(v.name for v in kept)}); all are offered"
            )
        for index, directory in enumerate(kept):
            real = os.path.normcase(os.path.realpath(str(directory)))
            if real in seen_real:
                continue
            seen_real.append(real)
            if not _inside(root, directory) or _same(root, directory):
                outside.append(
                    {
                        "model_type": folder.model_type,
                        "path": str(directory),
                        "label": folder.label,
                        "install_path": str(install),
                    }
                )
                continue
            subdir = _posix_rel(root, directory)
            scan = (
                folder.scan_headers
                if folder.scan_headers is not None
                else binding_scans_headers_by_default(folder.model_type, subdir)
            )
            suggestions.append(
                _Sug(
                    model_type=folder.model_type,
                    path=directory,
                    subdir=subdir,
                    label=folder.label if index == 0 else directory.name,
                    scan_headers=scan,
                    matched_by="profile",
                    source=source,
                )
            )
            if folder.write and index == 0:
                flagged.append(directory)
    _assign_writes(suggestions, flagged)
    return suggestions, outside


def _config_results(run: _Run, layout: ModelLayout, install: Path, root: Path) -> List[ReaderResult]:
    results: List[ReaderResult] = []
    for ref in layout.config_readers:
        results.append(
            run_reader(ref.kind, install, root=root, file=ref.file, translator=run.translator)
        )
    return results


def _group_root(paths: Sequence[Path]) -> Path:
    try:
        common = Path(os.path.commonpath([str(p) for p in paths]))
    except ValueError:
        return paths[0].parent
    return common.parent if any(_same(common, p) for p in paths) else common


def _extra_roots_from_entries(
    run: _Run,
    result: ReaderResult,
    root: Path,
    layout: ModelLayout,
    inside: List[_Sug],
    warnings: List[str],
) -> List[Dict[str, Any]]:
    groups: Dict[Optional[str], List[Any]] = {}
    for entry in result.entries:
        path = Path(entry.path)
        if not path.is_dir():
            warnings.append(f"{Path(result.source_file or result.kind).name} points at {entry.path}, which does not exist")
            continue
        if entry.outside_root:
            groups.setdefault(entry.section, []).append(entry)
        elif not any(_same(s.path, path) for s in inside):
            subdir = _posix_rel(root, path)
            inside.append(
                _Sug(
                    model_type=entry.model_type,
                    path=path,
                    subdir=subdir,
                    label=path.name,
                    scan_headers=binding_scans_headers_by_default(entry.model_type, subdir),
                    matched_by="config",
                    source="config",
                )
            )
    extras: List[Dict[str, Any]] = []
    root_key = root_path_key(root)
    for section, entries in groups.items():
        base = _group_root([Path(e.path) for e in entries])
        if paths_overlap(root_path_key(base), root_key):
            continue
        source_name = Path(result.source_file).name if result.source_file else result.kind
        suggestions: List[_Sug] = []
        for entry in entries:
            path = Path(entry.path)
            subdir = _posix_rel(base, path)
            suggestions.append(
                _Sug(
                    model_type=entry.model_type,
                    path=path,
                    subdir=subdir,
                    label=path.name,
                    scan_headers=binding_scans_headers_by_default(entry.model_type, subdir),
                    matched_by="config",
                    source="config",
                )
            )
        _assign_writes(suggestions, [Path(e.path) for e in entries if e.is_default])
        extras.append(_extra_dict(run, base, base.name, f"{source_name} › {section}" if section else source_name, False, layout, suggestions))
    return extras


def _extra_dict(
    run: _Run,
    path: Path,
    label: str,
    source: str,
    primary: bool,
    layout: ModelLayout,
    suggestions: List[_Sug],
) -> Dict[str, Any]:
    for suggestion in suggestions:
        suggestion.file_count, suggestion.file_count_truncated = _count_files(run, suggestion.path)
    return {
        "path": str(path),
        "label": label,
        "source": source,
        "primary": primary,
        "profile_id": layout.id,
        "suggestions": [s.to_dict() for s in suggestions],
    }


def _extra_roots_from_primary(
    run: _Run, result: ReaderResult, score: _Score, warnings: List[str]
) -> List[Dict[str, Any]]:
    extras: List[Dict[str, Any]] = []
    source_name = Path(result.source_file).name if result.source_file else result.kind
    candidates = []
    if result.primary_root and result.primary_outside_root:
        candidates.append((result.primary_root, True))
    candidates.extend((path, False) for path in result.extra_roots)
    for raw, primary in candidates:
        path = Path(raw)
        if not path.is_dir():
            warnings.append(f"{source_name} points at {raw}, which does not exist")
            continue
        suggestions, _ = _folder_suggestions(run, score.layout, score.install, path, path, warnings, source="profile")
        extras.append(
            _extra_dict(
                run,
                path,
                f"{score.layout.label} keeps its models here" if primary else path.name,
                source_name,
                primary,
                score.layout,
                suggestions,
            )
        )
    return extras


def _conflicts(run: _Run, candidates: Sequence[Path], case_insensitive: bool) -> List[Dict[str, Any]]:
    if run.resolver is None:
        return []
    conflicts: List[Dict[str, Any]] = []
    for root_id, existing in root_detection.existing_bound_paths(run.resolver):
        existing_key = root_path_key(existing, case_insensitive=case_insensitive)
        existing_real = root_path_key(os.path.realpath(str(existing)), case_insensitive=case_insensitive)
        for candidate in candidates:
            if paths_overlap(root_path_key(candidate, case_insensitive=case_insensitive), existing_key):
                conflicts.append({"root_id": root_id, "reason": f"overlaps {existing}"})
                break
            real_key = root_path_key(os.path.realpath(str(candidate)), case_insensitive=case_insensitive)
            if paths_overlap(real_key, existing_real):
                conflicts.append({"root_id": root_id, "reason": f"same folder as {existing} via a link"})
                break
    return conflicts


def _expand_delegate(score: _Score) -> List[Path]:
    delegate = score.layout.delegate
    if delegate is None:
        return []
    found: List[Path] = []
    seen: List[str] = []
    for pattern in delegate.search:
        try:
            matches = sorted(score.install.glob(pattern))
        except (OSError, ValueError, NotImplementedError):
            continue
        for match in matches:
            if not match.is_dir():
                continue
            real = os.path.normcase(os.path.realpath(str(match)))
            if any(real == item or real.startswith(item + os.sep) for item in seen):
                continue
            seen.append(real)
            found.append(match)
            if len(found) >= delegate.max_candidates:
                return found
    return found


def _profiled(
    run: _Run,
    raw_path: Path,
    score: _Score,
    alternatives: List[Dict[str, Any]],
    state: str,
    warnings: List[str],
    forced: bool,
) -> Dict[str, Any]:
    layout = score.layout
    if forced and score.confidence is None:
        warnings.append(f"No {layout.label} markers found here")

    case_insensitive = probe_case_insensitive(raw_path)
    try:
        writable_hint = os.access(raw_path, os.W_OK)
    except OSError:
        writable_hint = False

    models = score.models
    results = _config_results(run, layout, score.install, raw_path)
    for result in results:
        warnings.extend(result.warnings)
        if result.primary_root and not result.primary_outside_root and Path(result.primary_root).is_dir():
            models = Path(result.primary_root)

    suggestions, outside = _folder_suggestions(run, layout, score.install, models, raw_path, warnings)

    extras: List[Dict[str, Any]] = []
    for result in results:
        extras.extend(_extra_roots_from_entries(run, result, raw_path, layout, suggestions, warnings))
        extras.extend(_extra_roots_from_primary(run, result, score, warnings))

    for suggestion in suggestions:
        suggestion.file_count, suggestion.file_count_truncated = _count_files(run, suggestion.path)

    conflicts = _conflicts(run, [s.path for s in suggestions], case_insensitive)
    profile = _profile_dict(score)
    profile["models_path"] = str(models) if models is not None else None
    return {
        "path": str(raw_path),
        "root_path": str(raw_path),
        "effective_path": str(models) if models is not None else str(raw_path),
        "state": state,
        "writable_hint": writable_hint,
        "case_insensitive": case_insensitive,
        "layout": "typed" if suggestions else "empty",
        "profile": profile,
        "alternatives": alternatives + [_generic_alternative()],
        "suggestions": [s.to_dict() for s in suggestions],
        "outside_folders": outside,
        "extra_roots": extras,
        "delegated": [],
        "single_type_guess": None,
        "conflicts": conflicts,
        "warnings": warnings,
    }


def _generic(
    raw_path: Path, run: _Run, alternatives: List[Dict[str, Any]], warnings: List[str]
) -> Dict[str, Any]:
    result = root_detection.detect(raw_path, resolver=run.resolver).to_dict()
    seen_types: set = set()
    for suggestion in result["suggestions"]:
        suggestion["label"] = suggestion["subdir"].rsplit("/", 1)[-1]
        suggestion["write"] = suggestion["model_type"] not in seen_types
        suggestion["source"] = "generic"
        seen_types.add(suggestion["model_type"])
    result["warnings"] = list(result["warnings"]) + warnings
    result["root_path"] = result["path"]
    result["profile"] = None
    result["alternatives"] = alternatives + [_generic_alternative()]
    result["outside_folders"] = []
    result["extra_roots"] = []
    result["delegated"] = []
    return result


def detect_layout(
    path: str,
    *,
    catalog: Any,
    profile: Optional[str] = None,
    resolver: Any = None,
    translator: Any = None,
    clock: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    layouts: List[ModelLayout] = catalog.list_layouts() if catalog is not None else []
    forced_layout: Optional[ModelLayout] = None
    if profile and profile != GENERIC_LAYOUT_ID:
        forced_layout = next((layout for layout in layouts if layout.id == profile), None)
        if forced_layout is None:
            raise UnknownLayoutError(profile)

    raw_path = Path(unicodedata.normalize("NFC", str(path)))
    run = _Run(clock=clock, deadline=clock() + DETECTION_BUDGET_SECONDS, translator=translator, resolver=resolver)

    state, _reason = root_detection.probe_path(raw_path)
    if state != "online":
        return _generic(raw_path, run, [], [])

    warnings: List[str] = []
    drive_warning = root_detection.drive_letter_warning(str(raw_path), state)
    if drive_warning:
        warnings.append(drive_warning)

    ranked = _rank(run, layouts, raw_path)

    if profile == GENERIC_LAYOUT_ID:
        return _generic(raw_path, run, [_alternative(s) for s in ranked], [])

    if forced_layout is not None:
        chosen = next((s for s in ranked if s.layout.id == forced_layout.id), None) or _best_score(
            run, forced_layout, raw_path
        )
        forced = True
    elif ranked:
        chosen, forced = ranked[0], False
    else:
        return _generic(raw_path, run, [], warnings)

    alternatives = [_alternative(s) for s in ranked if s.layout.id != chosen.layout.id]

    if chosen.layout.delegate is not None:
        return _delegated(run, raw_path, chosen, layouts, alternatives, state, warnings, forced)
    return _profiled(run, raw_path, chosen, alternatives, state, warnings, forced)


def _delegated(
    run: _Run,
    raw_path: Path,
    chosen: _Score,
    layouts: Sequence[ModelLayout],
    alternatives: List[Dict[str, Any]],
    state: str,
    warnings: List[str],
    forced: bool,
) -> Dict[str, Any]:
    inner = [layout for layout in layouts if layout.delegate is None]
    found: List[Tuple[Path, _Score]] = []
    for candidate in _expand_delegate(chosen):
        matches = [s for s in _rank(run, inner, candidate) if s.confidence == "strong"]
        if matches:
            found.append((candidate, matches[0]))
    found.sort(key=lambda item: (-item[1].total, item[0].name))

    delegated = [
        {
            "path": str(candidate),
            "label": candidate.name,
            "profile": {"id": s.layout.id, "label": s.layout.label, "confidence": s.confidence},
        }
        for candidate, s in found
    ]

    if not found:
        warnings.append(f"No installed apps found in this {chosen.layout.label} folder")
        result = _profiled(run, raw_path, chosen, alternatives, state, warnings, forced)
        result["delegated"] = []
        return result

    best_path, best_score = found[0]
    inner_alternatives = [_alternative(s) for s in _rank(run, inner, best_path) if s.layout.id != best_score.layout.id]
    result = _profiled(run, best_path, best_score, inner_alternatives, state, warnings, False)
    result["path"] = str(raw_path)
    result["profile"] = _profile_dict(chosen)
    result["alternatives"] = alternatives + [_generic_alternative()]
    result["delegated"] = delegated
    return result
