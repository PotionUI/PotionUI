import re

GENERIC_FAILURE = (
    "Something went wrong on the server. Try again, and ask an administrator "
    "to check the server log if it keeps happening."
)

_ABSOLUTE_PATH = re.compile(r"(?<![\w.:/\\-])(?:/(?:[\w.\-]+/)+[\w.\-]*|[A-Za-z]:\\[^\s'\"]+)")
_ENDPOINT = re.compile(
    r"(?P<at>\s+at)?\s+(?:(?:[a-z][a-z0-9+.-]*://)[^\s'\"<>]+|(?<![\w.:<-])(?=[\w.-]*[A-Za-z.])\w[\w.-]*:\d{2,5}(?!\w))",
    re.IGNORECASE,
)


def scrub_paths(text: str) -> str:
    return _ABSOLUTE_PATH.sub("<server path>", text)


def hide_endpoints(text: str) -> str:
    return _ENDPOINT.sub(lambda match: "\u2026" if match.group("at") else " the server", text)


def basename_paths(text: str) -> str:
    return _ABSOLUTE_PATH.sub(_basename_or_marker, text)


def _basename_or_marker(match: re.Match) -> str:
    return re.split(r"[\\/]", match.group(0).rstrip("\\/"))[-1] or "<server path>"


def failure_detail(error: object, is_admin: bool, plain: str = GENERIC_FAILURE) -> str:
    return scrub_paths(str(error)) if is_admin else plain
