"""The one text matcher shared by phrasebook find and batch replace."""
import re
from dataclasses import dataclass
from typing import Any, List, Tuple

from src.platform.util.safe_regex import InvalidRegex, compile_user_regex

MODES = ("contains", "word", "regex")


class InvalidPattern(ValueError):
    """The query is not a valid pattern (or replacement template) for its mode."""


@dataclass(frozen=True)
class Matcher:
    pattern: Any
    mode: str


def compile_matcher(query: str, mode: str = "contains", case_sensitive: bool = False) -> Matcher:
    if mode not in MODES:
        raise InvalidPattern(f"Unknown mode: {mode}")
    if mode == "regex":
        try:
            return Matcher(pattern=compile_user_regex(query, case_sensitive), mode=mode)
        except InvalidRegex as e:
            raise InvalidPattern(str(e)) from e
    if mode == "contains":
        source = re.escape(query)
    else:
        source = r"(?<!\w)" + re.escape(query) + r"(?!\w)"
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return Matcher(pattern=re.compile(source, flags), mode=mode)
    except re.error as e:
        raise InvalidPattern(str(e)) from e


def find_spans(matcher: Matcher, text: str) -> List[Tuple[int, int]]:
    return [
        (m.start(), m.end())
        for m in matcher.pattern.finditer(text or "")
        if m.end() > m.start()
    ]


def substitute(matcher: Matcher, text: str, replacement: str) -> str:
    if matcher.mode == "regex":
        try:
            return matcher.pattern.sub(replacement, text or "")
        except (re.error, IndexError, ValueError) as e:
            raise InvalidPattern(str(e)) from e
    return matcher.pattern.sub(lambda _m: replacement, text or "")
