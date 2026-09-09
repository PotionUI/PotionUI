"""Checking that every media row in a database still has bytes behind it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.features.backup.paths import ANIMATED_THUMBNAIL_SUFFIX
from src.features.backup.repository import generation_file_rows, upload_rows

GENERATION_KEY_PREFIX = "generations"
UPLOADS_PREFIX = "uploads"

_THUMBNAIL_COLUMNS = ("thumbnail_small", "thumbnail_medium", "thumbnail_large")


@dataclass(frozen=True)
class MissingMedia:
    kind: str
    key: str
    owner: Optional[str] = None

    def describe(self) -> str:
        owner = f" (generation {self.owner})" if self.owner else ""
        return f"{self.kind}: {self.key}{owner}"


@dataclass
class VerifyReport:
    checked: int = 0
    missing: List[MissingMedia] = field(default_factory=list)
    missing_animated: int = 0
    roots: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    def as_dict(self) -> Dict[str, Any]:
        return {
            "checked": self.checked,
            "missing": [{"kind": m.kind, "key": m.key, "owner": m.owner} for m in self.missing],
            "missing_animated": self.missing_animated,
            "roots": self.roots,
        }


def _candidates(roots: Sequence[Path], key: str) -> List[Path]:
    out: List[Path] = []
    for root in roots:
        out.append(root / key)
        # Rows written by the upload path carry the storage directory in the
        # value itself; rows written by the generation path do not.
        if key.startswith("storage/"):
            out.append(root / key.split("/", 1)[1])
    return out


def _exists(roots: Sequence[Path], key: str) -> bool:
    return any(candidate.is_file() for candidate in _candidates(roots, key))


def _animated_sibling(key: str) -> str:
    path = Path(key)
    return str(path.parent / f"{path.stem}{ANIMATED_THUMBNAIL_SUFFIX}")


def verify_media(db_path: Path, roots: Sequence[Path]) -> VerifyReport:
    """Walk generation files and uploads, reporting keys with no bytes behind them.

    A missing animated thumbnail is counted, not listed: it is a derived file
    the thumbnail job rebuilds, and the default backup omits it deliberately.
    """
    roots = [Path(root) for root in roots]
    db_path = Path(db_path)
    report = VerifyReport(roots=[str(root) for root in roots])
    report = _verify_generation_files(generation_file_rows(db_path), roots, report)
    return _verify_uploads(upload_rows(db_path), roots, report)


def _verify_generation_files(rows, roots: Sequence[Path], report: VerifyReport) -> VerifyReport:
    for row in rows:
        owner = row["generation_id"]
        is_video = (row["file_type"] or "").lower() == "video"
        if row["file_path"]:
            report.checked += 1
            if not _exists(roots, row["file_path"]):
                report.missing.append(MissingMedia("generation file", row["file_path"], owner))
        for column in _THUMBNAIL_COLUMNS:
            key = row[column]
            if not key:
                continue
            report.checked += 1
            if not _exists(roots, key):
                report.missing.append(MissingMedia("thumbnail", key, owner))
            if is_video and not _exists(roots, _animated_sibling(key)):
                report.missing_animated += 1
    return report


def _verify_uploads(rows, roots: Sequence[Path], report: VerifyReport) -> VerifyReport:
    for row in rows:
        is_video = (row["media_type"] or "").lower() == "video"
        if row["filename"]:
            key = f"{UPLOADS_PREFIX}/{row['filename']}"
            report.checked += 1
            if not _exists(roots, key):
                report.missing.append(MissingMedia("upload", key))
        for column in _THUMBNAIL_COLUMNS:
            value = row[column]
            if not value:
                continue
            key = f"{UPLOADS_PREFIX}/{value}"
            report.checked += 1
            if not _exists(roots, key):
                report.missing.append(MissingMedia("upload thumbnail", key))
            if is_video and not _exists(roots, _animated_sibling(key)):
                report.missing_animated += 1
    return report
