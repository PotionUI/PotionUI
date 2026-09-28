import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

from src.platform.filesystem.model_roots import (
    default_case_insensitive,
    paths_overlap,
    probe_case_insensitive,
    root_path_key,
)
from src.platform.filesystem.model_types import (
    DIRECTORY_TO_MODEL_TYPE,
    MODEL_DIRECTORY_ALIASES,
    MODEL_DIRECTORY_NAMES,
    MODEL_TYPES,
    SUPPORTED_MODEL_EXTENSIONS,
    type_for_folder_name,
)

_ALL_TYPE_FOLDER_NAMES = frozenset(
    n.lower() for n in MODEL_DIRECTORY_NAMES
) | frozenset(
    alias.lower() for aliases in MODEL_DIRECTORY_ALIASES.values() for alias in aliases
)

_FILE_COUNT_LIMIT = 10_000
_FILE_COUNT_TIME_LIMIT_SECONDS = 2.0
_PROBE_TIMEOUT_SECONDS = 2.0

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:[\\/]")


class ResolvedTarget(NamedTuple):
    path: Path
    matched: bool


def resolve_targets(external_path: str, overrides: Dict[str, str]) -> Dict[str, ResolvedTarget]:
    root = _effective_root(Path(external_path))
    resolved: Dict[str, ResolvedTarget] = {}
    for name in MODEL_DIRECTORY_NAMES:
        override = overrides.get(name)
        if override:
            resolved[name] = ResolvedTarget(Path(override), True)
            continue
        resolved[name] = _resolve_one(name, root)
    return resolved


def _resolve_one(name: str, root: Path) -> ResolvedTarget:
    candidates = (name,) + MODEL_DIRECTORY_ALIASES.get(name, ())
    existing = [child for child in (_find_child(root, c) for c in candidates) if child is not None]

    for child in existing:
        if _has_model_file(child):
            return ResolvedTarget(child, True)

    if existing:
        return ResolvedTarget(existing[0], True)

    return ResolvedTarget(root / name, False)


def _effective_root(external_path: Path) -> Path:
    nested = external_path / "models"
    if _root_score(nested) > _root_score(external_path):
        return nested
    return external_path


def _root_score(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    best = 0
    try:
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or child.name.lower() not in _ALL_TYPE_FOLDER_NAMES:
                continue
            if _has_model_file(child):
                return 2
            best = 1
    except OSError:
        return best
    return best


def _find_child(directory: Path, candidate: str) -> Optional[Path]:
    if not directory.is_dir():
        return None
    lowered = candidate.lower()
    try:
        for child in sorted(directory.iterdir()):
            if child.is_dir() and child.name.lower() == lowered:
                return child
    except OSError:
        return None
    return None


def _has_model_file(directory: Path) -> bool:
    try:
        for entry in directory.rglob("*"):
            if entry.is_file() and entry.suffix.lower() in SUPPORTED_MODEL_EXTENSIONS:
                return True
    except OSError:
        return False
    return False


@dataclass(frozen=True)
class DetectionSuggestion:
    model_type: str
    subdir: str
    matched_by: str
    file_count: int
    file_count_truncated: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_type": self.model_type,
            "subdir": self.subdir,
            "matched_by": self.matched_by,
            "file_count": self.file_count,
            "file_count_truncated": self.file_count_truncated,
        }


@dataclass(frozen=True)
class RootConflict:
    root_id: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"root_id": self.root_id, "reason": self.reason}


@dataclass(frozen=True)
class DetectedLayout:
    path: str
    effective_path: str
    state: str
    writable_hint: bool
    case_insensitive: bool
    layout: str
    suggestions: List[DetectionSuggestion]
    single_type_guess: Optional[str]
    conflicts: List[RootConflict]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "effective_path": self.effective_path,
            "state": self.state,
            "writable_hint": self.writable_hint,
            "case_insensitive": self.case_insensitive,
            "layout": self.layout,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "single_type_guess": self.single_type_guess,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "warnings": self.warnings,
        }


def _probe_path(path: Path) -> Tuple[str, Optional[str]]:
    import threading

    result: Dict[str, Any] = {}

    def _target() -> None:
        try:
            result["exists"] = path.is_dir()
        except OSError as error:
            result["error"] = str(error)

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(_PROBE_TIMEOUT_SECONDS)
    if worker.is_alive():
        return "offline", "timed out probing the path"
    if "error" in result:
        return "unreadable", result["error"]
    if result.get("exists"):
        return "online", None
    return "offline", "the path does not exist"


def _count_model_files(directory: Path) -> Tuple[int, bool]:
    start = time.monotonic()
    count = 0
    truncated = False
    try:
        for entry in directory.rglob("*"):
            if count >= _FILE_COUNT_LIMIT or (time.monotonic() - start) > _FILE_COUNT_TIME_LIMIT_SECONDS:
                truncated = True
                break
            if entry.is_file() and entry.suffix.lower() in SUPPORTED_MODEL_EXTENSIONS:
                count += 1
    except OSError:
        pass
    return count, truncated


def _posix_relpath(base: Path, target: Path) -> str:
    try:
        rel = target.relative_to(base)
    except ValueError:
        rel = Path(os.path.relpath(str(target), str(base)))
    return PurePosixPath(str(rel).replace("\\", "/")).as_posix()


def _drive_letter_warning(path_str: str, state: str) -> Optional[str]:
    if not _DRIVE_LETTER_RE.match(path_str):
        return None
    if state == "online":
        return None
    drive = path_str[0].upper()
    return (
        f"Drive {drive}: is not visible to the PotionUI process; mapped drive letters are "
        f"per-user sessions - if PotionUI runs as a service, use \\\\server\\share instead."
    )


def existing_bound_paths(resolver: Any) -> List[Tuple[str, Path]]:
    seen = set()
    pairs: List[Tuple[str, Path]] = []
    for model_type in MODEL_TYPES:
        for type_dir in resolver.type_dirs(model_type, online_only=False):
            key = (type_dir.root_id, str(type_dir.path))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((type_dir.root_id, type_dir.path))
    return pairs


def _find_conflicts(
    candidate_paths: Sequence[Path], case_insensitive: bool, resolver: Optional[Any]
) -> List[RootConflict]:
    if resolver is None:
        return []
    conflicts: List[RootConflict] = []
    for root_id, existing_path in existing_bound_paths(resolver):
        existing_key = root_path_key(existing_path, case_insensitive=case_insensitive)
        for candidate in candidate_paths:
            candidate_key = root_path_key(candidate, case_insensitive=case_insensitive)
            if paths_overlap(candidate_key, existing_key):
                conflicts.append(RootConflict(root_id=root_id, reason=f"overlaps {existing_path}"))
                break
    return conflicts


def detect(path: Union[str, Path], *, resolver: Optional[Any] = None) -> DetectedLayout:
    raw_path = Path(unicodedata.normalize("NFC", str(path)))
    path_str = str(raw_path)

    state, _reason = _probe_path(raw_path)
    warnings: List[str] = []
    drive_warning = _drive_letter_warning(path_str, state)
    if drive_warning:
        warnings.append(drive_warning)

    if state != "online":
        return DetectedLayout(
            path=path_str,
            effective_path=path_str,
            state=state,
            writable_hint=False,
            case_insensitive=default_case_insensitive(),
            layout="empty",
            suggestions=[],
            single_type_guess=None,
            conflicts=[],
            warnings=warnings,
        )

    case_insensitive = probe_case_insensitive(raw_path)
    try:
        writable_hint = os.access(raw_path, os.W_OK)
    except OSError:
        writable_hint = False

    effective = _effective_root(raw_path)

    suggestions: List[DetectionSuggestion] = []
    matched_dirs: List[Path] = []
    for canonical in MODEL_DIRECTORY_NAMES:
        candidates = (canonical,) + MODEL_DIRECTORY_ALIASES.get(canonical, ())
        found = [child for child in (_find_child(effective, c) for c in candidates) if child is not None]
        if not found:
            continue
        matched_dir = next((child for child in found if _has_model_file(child)), found[0])
        matched_name = matched_dir.name.lower()
        matched_by = "canonical" if matched_name == canonical.lower() else "alias"
        file_count, truncated = _count_model_files(matched_dir)
        subdir = _posix_relpath(raw_path, matched_dir)
        suggestions.append(
            DetectionSuggestion(
                model_type=DIRECTORY_TO_MODEL_TYPE[canonical],
                subdir=subdir,
                matched_by=matched_by,
                file_count=file_count,
                file_count_truncated=truncated,
            )
        )
        matched_dirs.append(matched_dir)

    layout = "typed" if suggestions else "empty"
    single_type_guess: Optional[str] = None
    candidate_paths: List[Path] = list(matched_dirs)

    if not suggestions:
        own_count, _own_truncated = _count_model_files(raw_path)
        if own_count > 0:
            layout = "single"
            single_type_guess = type_for_folder_name(raw_path.name)
            candidate_paths = [raw_path]

    conflicts = _find_conflicts(candidate_paths, case_insensitive, resolver)

    return DetectedLayout(
        path=path_str,
        effective_path=str(effective),
        state=state,
        writable_hint=writable_hint,
        case_insensitive=case_insensitive,
        layout=layout,
        suggestions=suggestions,
        single_type_guess=single_type_guess,
        conflicts=conflicts,
        warnings=warnings,
    )
