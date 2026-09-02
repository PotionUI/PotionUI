"""The one text matcher shared by phrasebook find and batch replace."""
import re
from dataclasses import dataclass
from typing import List, Tuple

MODES = ("contains", "word", "regex")


class InvalidPattern(ValueError):
    """The query is not a valid pattern (or replacement template) for its mode."""


@dataclass(frozen=True)
class Matcher:
    pattern: re.Pattern
    mode: str


def compile_matcher(query: str, mode: str = "contains", case_sensitive: bool = False) -> Matcher:
    if mode not in MODES:
        raise InvalidPattern(f"Unknown mode: {mode}")
    if mode == "contains":
        source = re.escape(query)
    elif mode == "word":
        source = r"(?<!\w)" + re.escape(query) + r"(?!\w)"
    else:
        source = query
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
        except (re.error, IndexError) as e:
            raise InvalidPattern(str(e)) from e
    return matcher.pattern.sub(lambda _m: replacement, text or "")
