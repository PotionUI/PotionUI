import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

MAX_ENTRIES = 5000
MAX_ENTRY_LENGTH = 200
MAX_WILDCARDS = 3
MAX_SCAN_CHARS = 50000
_PREFIX_KEY = 8

_WORD = re.compile(r"\w+")
_PATTERN_WORD = re.compile(r"[\w*]+")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _collapse(token: str) -> str:
    return re.sub(r"\*+", "*", token)


def _entry_tokens(entry: str) -> List[str]:
    tokens = [_collapse(token) for token in _PATTERN_WORD.findall(normalize(entry))]
    return [token for token in tokens if token.strip("*")]


def glob_match(pattern: str, text: str) -> bool:
    p = t = 0
    star = -1
    mark = 0
    while t < len(text):
        if p < len(pattern) and pattern[p] == "*":
            star = p
            mark = t
            p += 1
        elif p < len(pattern) and pattern[p] == text[t]:
            p += 1
            t += 1
        elif star != -1:
            p = star + 1
            mark += 1
            t = mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


class BannedWordsMatcher:
    def __init__(self, entries: Sequence[str]):
        self.entries = tuple(entries)
        self._exact: Dict[str, List[Tuple[int, List[str]]]] = {}
        self._wild: Dict[str, List[Tuple[int, List[str]]]] = {}
        for index, entry in enumerate(self.entries):
            tokens = _entry_tokens(entry)
            if not tokens:
                continue
            if "*" in tokens[0]:
                prefix = tokens[0].split("*", 1)[0][:_PREFIX_KEY]
                self._wild.setdefault(prefix, []).append((index, tokens))
            else:
                self._exact.setdefault(tokens[0], []).append((index, tokens))

    @property
    def empty(self) -> bool:
        return not self._exact and not self._wild

    def _candidates(self, word: str):
        yield from self._exact.get(word, ())
        for length in range(min(len(word), _PREFIX_KEY) + 1):
            yield from self._wild.get(word[:length], ())

    def first_match(self, text: str) -> Optional[int]:
        if self.empty or not text:
            return None
        words = _WORD.findall(normalize(text))
        best: Optional[int] = None
        for position, word in enumerate(words):
            for index, tokens in self._candidates(word):
                if best is not None and index >= best:
                    continue
                if position + len(tokens) > len(words):
                    continue
                if all(glob_match(token, words[position + offset]) for offset, token in enumerate(tokens)):
                    best = index
        return best

    def first_match_in(self, texts: Sequence[str]) -> Optional[int]:
        for text in texts:
            index = self.first_match(text)
            if index is not None:
                return index
        return None


def validate_entries(value: Any) -> Optional[str]:
    if not isinstance(value, list):
        return "must be a list of words or phrases"
    if len(value) > MAX_ENTRIES:
        return f"holds at most {MAX_ENTRIES} entries"
    for entry in value:
        if not isinstance(entry, str):
            return "every entry must be text"
        if len(entry) > MAX_ENTRY_LENGTH:
            return f"entries are at most {MAX_ENTRY_LENGTH} characters"
        if entry.strip() and not _entry_tokens(entry):
            return "an entry cannot be only wildcards"
        if sum(token.count("*") for token in _entry_tokens(entry)) > MAX_WILDCARDS:
            return f"entries hold at most {MAX_WILDCARDS} wildcards"
    return None


def _positive(pair: Any) -> str:
    if isinstance(pair, dict):
        return str(pair.get("positive") or "")
    return str(getattr(pair, "positive", "") or "")


def collect_positive_texts(prompts: Any, form_data: Any) -> List[str]:
    texts: List[str] = [_positive(pair) for pair in prompts or []]
    data = form_data if isinstance(form_data, dict) else {}

    video = data.get("video_director")
    if isinstance(video, dict):
        for segment in video.get("segments") or []:
            if isinstance(segment, dict):
                texts.append(str(segment.get("prompt") or ""))

    music = data.get("music_director")
    if isinstance(music, dict):
        texts.append(str(music.get("description") or ""))
        texts.append(str(music.get("compiled_lyrics") or ""))
        for section in music.get("sections") or []:
            if isinstance(section, dict):
                texts.append(str(section.get("lyrics") or ""))
                texts.append(str(section.get("style_hint") or ""))
                texts.append(str(section.get("description") or ""))

    return [text for text in texts if text]
