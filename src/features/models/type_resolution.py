from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from src.platform.filesystem.model_types import MODEL_TYPES, UNDEFINED_MODEL_TYPE

SOURCE_HEADER = "header"
SOURCE_FOLDER = "folder"

_TYPE_ORDER = {model_type: index for index, model_type in enumerate(MODEL_TYPES)}


@dataclass(frozen=True)
class Copy:
    model_type: str
    classifiable: bool


@dataclass(frozen=True)
class Resolution:
    model_type: str
    source: str


def _folder_type(copies: Sequence[Copy]) -> str:
    return min((c.model_type for c in copies), key=lambda t: _TYPE_ORDER.get(t, len(_TYPE_ORDER)))


def resolve_type(
    assertion: Optional[Dict[str, Any]],
    copies: Sequence[Copy],
    verdict: Optional[Dict[str, Any]],
) -> Optional[Resolution]:
    if assertion is not None:
        return Resolution(assertion["model_type"], assertion["source"])
    if not copies:
        return None

    status = verdict["status"] if verdict else None
    decided = status == "decided" and bool(verdict["model_type"])
    scanned = [c for c in copies if c.classifiable]
    unscanned = [c for c in copies if not c.classifiable]

    if scanned and decided:
        return Resolution(verdict["model_type"], SOURCE_HEADER)
    if unscanned:
        if decided and len({c.model_type for c in unscanned}) > 1:
            return Resolution(verdict["model_type"], SOURCE_HEADER)
        return Resolution(_folder_type(unscanned), SOURCE_FOLDER)
    if status == "undecided":
        return Resolution(UNDEFINED_MODEL_TYPE, SOURCE_HEADER)
    return Resolution(_folder_type(copies), SOURCE_FOLDER)
