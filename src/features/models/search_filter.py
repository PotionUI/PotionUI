import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

REGEX_MAX_LENGTH = 200
USAGE_STATES = ("any", "used", "never")
USAGE_SORT_FIELDS = {
    "uses": "COALESCE(gu.use_count, 0)",
    "last_used": "gu.last_used_at",
}
USAGE_JOIN = (
    " LEFT JOIN (SELECT model_id, COUNT(*) AS use_count, MAX(datetime(created_at)) AS last_used_at"
    " FROM generation_models GROUP BY model_id) gu ON gu.model_id = m.id"
)
USAGE_SELECT = ", COALESCE(gu.use_count, 0) AS use_count, gu.last_used_at AS last_used_at"


class InvalidModelSearch(ValueError):
    pass


@dataclass(frozen=True)
class ModelSearchFilter:
    regex: Optional[str] = None
    indexed_from: Optional[date] = None
    indexed_to: Optional[date] = None
    used: str = "any"
    min_uses: Optional[int] = None
    last_used_from: Optional[date] = None
    last_used_to: Optional[date] = None

    @property
    def needs_usage(self) -> bool:
        return (
            self.used != "any"
            or self.min_uses is not None
            or self.last_used_from is not None
            or self.last_used_to is not None
        )

    @property
    def is_empty(self) -> bool:
        return not self.regex and self.indexed_from is None and self.indexed_to is None and not self.needs_usage


def _parse_day(value: Optional[str], label: str) -> Optional[date]:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise InvalidModelSearch(f"{label} must be a date in YYYY-MM-DD form")


def _check_range(start: Optional[date], end: Optional[date], label: str) -> None:
    if start and end and start > end:
        raise InvalidModelSearch(f"{label}: the start date is after the end date")


def parse_model_search(
    *,
    search: Optional[str],
    q_mode: Optional[str],
    indexed_from: Optional[str] = None,
    indexed_to: Optional[str] = None,
    used: Optional[str] = None,
    min_uses: Optional[int] = None,
    last_used_from: Optional[str] = None,
    last_used_to: Optional[str] = None,
) -> Tuple[Optional[str], ModelSearchFilter]:
    mode = (q_mode or "substring").strip().lower()
    if mode not in ("substring", "regex"):
        raise InvalidModelSearch("q_mode must be 'substring' or 'regex'")

    regex = None
    if mode == "regex" and search:
        if len(search) > REGEX_MAX_LENGTH:
            raise InvalidModelSearch(f"Regular expression is longer than {REGEX_MAX_LENGTH} characters")
        try:
            re.compile(search)
        except re.error as exc:
            raise InvalidModelSearch(f"Invalid regular expression: {exc}")
        regex = search
        search = None

    usage = (used or "any").strip().lower()
    if usage not in USAGE_STATES:
        raise InvalidModelSearch("used must be one of: any, used, never")
    if min_uses is not None and min_uses < 1:
        raise InvalidModelSearch("min_uses must be at least 1")
    if usage == "never" and min_uses is not None:
        raise InvalidModelSearch("min_uses cannot be combined with used=never")

    parsed_indexed_from = _parse_day(indexed_from, "indexed_from")
    parsed_indexed_to = _parse_day(indexed_to, "indexed_to")
    _check_range(parsed_indexed_from, parsed_indexed_to, "Indexed")
    parsed_last_from = _parse_day(last_used_from, "last_used_from")
    parsed_last_to = _parse_day(last_used_to, "last_used_to")
    _check_range(parsed_last_from, parsed_last_to, "Last used")
    if usage == "never" and (parsed_last_from or parsed_last_to):
        raise InvalidModelSearch("A last-used range cannot be combined with used=never")

    return search, ModelSearchFilter(
        regex=regex,
        indexed_from=parsed_indexed_from,
        indexed_to=parsed_indexed_to,
        used=usage,
        min_uses=min_uses,
        last_used_from=parsed_last_from,
        last_used_to=parsed_last_to,
    )


def _day_start(day: date) -> str:
    return datetime(day.year, day.month, day.day).strftime("%Y-%m-%d %H:%M:%S")


def _day_after(day: date) -> str:
    return _day_start(day + timedelta(days=1))


def search_filter_clauses(search_filter: Optional[ModelSearchFilter], has_library: bool) -> Tuple[List[str], List]:
    clauses: List[str] = []
    params: List = []
    if search_filter is None:
        return clauses, params

    if search_filter.regex:
        alternatives = [
            "m.filename REGEXP ?",
            "EXISTS (SELECT 1 FROM providers pr WHERE pr.model_id = m.id AND pr.name REGEXP ?)",
        ]
        params.extend([search_filter.regex, search_filter.regex])
        if has_library:
            alternatives.append("umm.custom_name REGEXP ?")
            params.append(search_filter.regex)
        clauses.append("(" + " OR ".join(alternatives) + ")")

    if search_filter.indexed_from:
        clauses.append("datetime(m.indexed_at) >= ?")
        params.append(_day_start(search_filter.indexed_from))
    if search_filter.indexed_to:
        clauses.append("datetime(m.indexed_at) < ?")
        params.append(_day_after(search_filter.indexed_to))

    if search_filter.used == "never":
        clauses.append("gu.model_id IS NULL")
    elif search_filter.used == "used" or search_filter.min_uses is not None:
        clauses.append("COALESCE(gu.use_count, 0) >= ?")
        params.append(max(1, search_filter.min_uses or 1))

    if search_filter.last_used_from:
        clauses.append("gu.last_used_at >= ?")
        params.append(_day_start(search_filter.last_used_from))
    if search_filter.last_used_to:
        clauses.append("gu.last_used_at < ?")
        params.append(_day_after(search_filter.last_used_to))

    return clauses, params
