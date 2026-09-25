import sqlite3
from functools import lru_cache
from typing import Any, Optional

from src.platform.util.safe_regex import InvalidRegex, compile_regex

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
