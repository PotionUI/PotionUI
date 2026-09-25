import re
import sqlite3
from functools import lru_cache
from typing import Optional, Pattern


@lru_cache(maxsize=128)
def _compiled(pattern: str) -> Optional[Pattern[str]]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
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
