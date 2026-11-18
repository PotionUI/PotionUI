import math
import posixpath

from src.platform.http.errors import InvalidInputError

EDIT_TOOLS = ("image", "video")
SOURCE_KINDS = ("history", "library", "upload")
MAX_NAME_CHARS = 120
MAX_SOURCE_ID_CHARS = 128
ID_PREFIX = "ed_"


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None

def clip_of(raw):
    if not isinstance(raw, dict):
        return None
    clip = {}
    duration = _positive(raw.get("duration_seconds"))
    if duration is not None:
        clip["duration_seconds"] = round(duration, 3)
    for key in ("width", "height"):
        size = _positive(raw.get(key))
        if size is not None:
            clip[key] = int(size)
    return clip or None

def check_tool(tool: str) -> str:
    if tool not in EDIT_TOOLS:
        raise InvalidInputError("invalid_edit_tool", f"The tool must be one of: {', '.join(EDIT_TOOLS)}")
    return tool


def check_source(kind: str, source_id: str) -> None:
    if kind not in SOURCE_KINDS:
        raise InvalidInputError("invalid_edit_source", f"The source kind must be one of: {', '.join(SOURCE_KINDS)}")
    if not source_id or len(source_id) > MAX_SOURCE_ID_CHARS:
        raise InvalidInputError("invalid_edit_source", f"The source id must be 1 to {MAX_SOURCE_ID_CHARS} characters")


def clean_name(raw):
    if raw is None:
        return None
    name = " ".join(str(raw).split())
    if not name:
        return None
    if len(name) > MAX_NAME_CHARS:
        raise InvalidInputError("invalid_edit_name", f"The name is limited to {MAX_NAME_CHARS} characters")
    return name


def clean_source_name(raw):
    if raw is None:
        return None
    name = " ".join(str(raw).split())
    if not name:
        return None
    return name[:MAX_NAME_CHARS]

def copy_name(name, source_name=None):
    base = name
    if base is None and source_name:
        base = posixpath.splitext(source_name)[0] or source_name
    if base is None:
        return None
    return f"{base} copy"[:MAX_NAME_CHARS]
