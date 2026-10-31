import re

GENERIC_FAILURE = (
    "Something went wrong on the server. Try again, and ask an administrator "
    "to check the server log if it keeps happening."
)

_ABSOLUTE_PATH = re.compile(r"(?<![\w.:/\\-])(?:/(?:[\w.\-]+/)+[\w.\-]*|[A-Za-z]:\\[^\s'\"]+)")
_IP_HOST = r"\[[0-9a-f:]+\]|\d{1,3}(?:\.\d{1,3}){3}|localhost"
_DOTTED_HOST = r"(?:[\w-]+\.)+[A-Za-z][\w-]*"
_URL_TAIL = r"[^\s'\"<>]*"
_ENDPOINT = re.compile(
    r"(?P<at>\s+at)?\s+(?:"
    rf"[a-z][a-z0-9+.-]*://(?:(?:{_IP_HOST}|[\w-]+(?![\w.-]))(?::\d+)?|{_DOTTED_HOST}:\d+){_URL_TAIL}"
    rf"|(?:{_IP_HOST}|{_DOTTED_HOST}):\d{{2,5}}(?!\w)"
    r")",
    re.IGNORECASE,
)


def scrub_paths(text: str) -> str:
    return _ABSOLUTE_PATH.sub("<server path>", text)


def hide_endpoints(text: str) -> str:
    return _ENDPOINT.sub(lambda match: "\u2026" if match.group("at") else " \u2026", text)


def basename_paths(text: str) -> str:
    return _ABSOLUTE_PATH.sub(_basename_or_marker, text)


def _basename_or_marker(match: re.Match) -> str:
    return re.split(r"[\\/]", match.group(0).rstrip("\\/"))[-1] or "<server path>"


def failure_detail(error: object, is_admin: bool, plain: str = GENERIC_FAILURE) -> str:
    return scrub_paths(str(error)) if is_admin else plain
