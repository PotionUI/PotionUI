"""Reads the tail of the server's rotating log file without loading it whole.

Parses the `timestamp | LEVEL | logger | message` format written by
`src.platform.observability.logger`. A traceback frame or any other line
that doesn't match that shape is a continuation line: it is folded into the
`message` of the entry above it rather than treated as its own record.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import NamedTuple, Optional, TypedDict

DEFAULT_LINES = 500
MAX_LINES = 5000

# Read blocks from the end, doubling until the block holds enough newlines
# (or we've read the whole file) - the common case, a small tail off a large
# file, stays a handful of seeks instead of a full read.
_INITIAL_BLOCK_BYTES = 64 * 1024
_MAX_READ_BYTES = 8 * 1024 * 1024


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


_LEVEL_RANK = {level.value: rank for rank, level in enumerate(LogLevel)}


class LogEntryDict(TypedDict):
    ts: str
    level: str
    logger: str
    message: str


class TailResult(TypedDict):
    lines: list[LogEntryDict]
    truncated: bool
    file: Optional[str]
    size_bytes: int


class _Entry(NamedTuple):
    ts: str
    level: str
    logger: str
    message: str

    def as_dict(self) -> LogEntryDict:
        return {"ts": self.ts, "level": self.level, "logger": self.logger, "message": self.message}


def _read_tail_bytes(path: Path, wanted_lines: int) -> tuple[bytes, bool]:
    """The file's last bytes, read in a growing block from the end.

    Stops as soon as the block holds more newlines than requested, the whole
    file has been read, or the block hits the read cap. Returns the raw bytes
    and whether the block starts after byte 0 (there may be more file above
    it that was never read).
    """
    size = path.stat().st_size
    if size == 0:
        return b"", False

    to_read = min(size, _INITIAL_BLOCK_BYTES)
    while True:
        start = size - to_read
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read(to_read)
        if data.count(b"\n") > wanted_lines or to_read >= size or to_read >= _MAX_READ_BYTES:
            return data, start > 0
        to_read = min(size, to_read * 2)


def _parse_entries(raw_lines: list[str]) -> list[_Entry]:
    entries: list[_Entry] = []
    for raw in raw_lines:
        parts = raw.split(" | ", 3)
        if len(parts) == 4 and parts[1].strip() in _LEVEL_RANK:
            ts, level, logger_name, message = parts
            entries.append(_Entry(ts.strip(), level.strip(), logger_name.strip(), message))
        elif entries:
            entries[-1] = entries[-1]._replace(message=entries[-1].message + "\n" + raw)
        # a continuation line before any parsed entry is a partial line from
        # the start of the read block (or genuine noise); it is dropped.
    return entries


def tail_log(path: Optional[Path], lines: int = DEFAULT_LINES, level: Optional[str] = None) -> TailResult:
    """The last `lines` log entries at `path`, filtered to `level` and above.

    `lines` is clamped to [1, MAX_LINES]. Filtering narrows the same window
    of last-`lines` entries; it never expands the read further back to find
    `lines` worth of matches at the requested level. Returns an empty result
    with `file: None` when `path` is None (file logging off) or missing.
    """
    wanted = max(1, min(lines, MAX_LINES))

    if path is None or not path.exists():
        return {"lines": [], "truncated": False, "file": None, "size_bytes": 0}

    size_bytes = path.stat().st_size
    data, has_more_above = _read_tail_bytes(path, wanted)

    text = data.decode("utf-8", errors="replace")
    raw_lines = text.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()
    if has_more_above and raw_lines:
        # The block's first line was very likely cut mid-line by the seek.
        raw_lines.pop(0)

    entries = _parse_entries(raw_lines)
    truncated = has_more_above or len(entries) > wanted
    entries = entries[-wanted:]

    if level:
        min_rank = _LEVEL_RANK.get(level.upper())
        if min_rank is not None:
            entries = [e for e in entries if _LEVEL_RANK.get(e.level, -1) >= min_rank]

    return {
        "lines": [e.as_dict() for e in entries],
        "truncated": truncated,
        "file": str(path),
        "size_bytes": size_bytes,
    }
