import sqlite3
from functools import lru_cache
from typing import Any, Optional

import re2


class InvalidRegex(ValueError):
    pass


def _options() -> "re2.Options":
    options = re2.Options()
    options.case_sensitive = False
    options.log_errors = False
    return options


def compile_regex(pattern: str) -> Any:
    try:
        return re2.compile(pattern, _options())
    except re2.error as exc:
        reason = exc.args[0] if exc.args else exc
        if isinstance(reason, bytes):
            reason = reason.decode("utf-8", "replace")
        raise InvalidRegex(str(reason))


@lru_cache(maxsize=128)
def _compiled(pattern: str) -> Optional[Any]:
    try:
        return compile_regex(pattern)
    except InvalidRegex:
        return None


def regexp(pattern: Optional[str], value: Optional[str]) -> bool:
    if pattern is None or value is None:
        return False
    compiled = _compiled(pattern)
    if compiled is None:
        return False
    return compiled.search(str(value)) is not None


def register_sql_functions(conn: sqlite3.Connection) -> None:
    conn.create_function("REGEXP", 2, regexp, deterministic=True)
