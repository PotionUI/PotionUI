import re

GENERIC_FAILURE = (
    "Something went wrong on the server. Try again, and ask an administrator "
    "to check the server log if it keeps happening."
)

_ABSOLUTE_PATH = re.compile(r"(?<![\w.:/\\-])(?:/(?:[\w.\-]+/)+[\w.\-]*|[A-Za-z]:\\[^\s'\"]+)")


def scrub_paths(text: str) -> str:
    return _ABSOLUTE_PATH.sub("<server path>", text)


def failure_detail(error: object, is_admin: bool, plain: str = GENERIC_FAILURE) -> str:
    return scrub_paths(str(error)) if is_admin else plain
