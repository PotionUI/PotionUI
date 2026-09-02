"""Free-text search across every phrasebook category and value."""
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.features.phrasebook.operations.matching import Matcher, compile_matcher, find_spans
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)

VALUE_FIELDS = ("label", "value")
CATEGORY_FIELDS = ("name", "path", "description")
SCOPES = ("all", "values", "categories")
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


class InvalidFields(ValueError):
    """`fields` names something other than a value text field."""


def parse_fields(raw: Optional[str]) -> List[str]:
    if raw is None or not raw.strip():
        return list(VALUE_FIELDS)
    names = [f.strip() for f in raw.split(",") if f.strip()]
    unknown = [f for f in names if f not in VALUE_FIELDS]
    if unknown or not names:
        raise InvalidFields(f"Unknown fields: {', '.join(unknown) or raw}")
    return list(dict.fromkeys(names))


def _matches(matcher: Matcher, texts: Dict[str, str]) -> List[Dict[str, int]]:
    return [
        {"field": field, "start": start, "end": end}
        for field, text in texts.items()
        for start, end in find_spans(matcher, text)
    ]


def _rank(matches: Iterable[Dict[str, int]], texts: Dict[str, str]) -> int:
    rank = 2
    for m in matches:
        if m["start"] == 0:
            rank = min(rank, 0 if m["end"] == len(texts[m["field"]]) else 1)
    return rank


def _empty(query: str, mode: str, case_sensitive: bool, scope: str) -> Dict[str, Any]:
    return {
        "query": query,
        "mode": mode,
        "case_sensitive": case_sensitive,
        "scope": scope,
        "categories": [],
        "values": [],
        "total_categories": 0,
        "total_values": 0,
    }


def find_phrasebook(
    category_repository: PhrasebookCategoryRepository,
    value_repository: PhrasebookValueRepository,
    user_id: str,
    query: str,
    *,
    mode: str = "contains",
    case_sensitive: bool = False,
    scope: str = "all",
    include_inactive: bool = True,
    path_prefix: str = "",
    fields: Sequence[str] = VALUE_FIELDS,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Match categories (name, path, description) and values (chosen fields)
    with one compiled matcher, returning per-field match spans, ranked exact →
    prefix → substring. A blank query yields an empty result rather than an
    error; an invalid pattern raises `InvalidPattern`."""
    trimmed = (query or "").strip()
    if scope not in SCOPES:
        raise ValueError(f"Unknown scope: {scope}")
    if not trimmed:
        return _empty(trimmed, mode, case_sensitive, scope)

    matcher = compile_matcher(trimmed, mode, case_sensitive)
    limit = max(1, min(int(limit), MAX_LIMIT))
    prefilter = None if mode == "regex" else trimmed
    result = _empty(trimmed, mode, case_sensitive, scope)

    if scope in ("all", "categories"):
        hits = []
        for category in category_repository.list_for_find(
            user_id, prefilter, path_prefix, include_inactive
        ):
            texts = {f: getattr(category, f) or "" for f in CATEGORY_FIELDS}
            matches = _matches(matcher, texts)
            if matches:
                hits.append((_rank(matches, texts), category.path, {**category.model_dump(), "matches": matches}))
        hits.sort(key=lambda h: (h[0], h[1]))
        result["total_categories"] = len(hits)
        result["categories"] = [h[2] for h in hits[:limit]]

    if scope in ("all", "values"):
        hits = []
        for value in value_repository.list_for_find(user_id, prefilter, path_prefix, include_inactive):
            texts = {f: value.get(f) or "" for f in fields}
            matches = _matches(matcher, texts)
            if matches:
                hits.append((
                    _rank(matches, texts),
                    (value["label"] or "").lower(),
                    value["category_path"] or "",
                    {**value, "matches": matches},
                ))
        hits.sort(key=lambda h: (h[0], h[1], h[2]))
        result["total_values"] = len(hits)
        result["values"] = [h[3] for h in hits[:limit]]

    return result
