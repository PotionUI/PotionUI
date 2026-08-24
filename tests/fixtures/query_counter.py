"""A `db` stand-in that counts the SQL statements a code path issues.

For pinning "this list path costs a constant number of queries" - the N+1
regression that is invisible in behaviour and only shows up as a slow page.
Wraps the real (test) database rather than replacing it, so the statements it
counts are statements that really ran against the real schema.

`sqlite3.Cursor.execute` cannot be monkeypatched (it is a read-only attribute
on the C type), which is why this proxies the cursor instead.
"""

from contextlib import contextmanager


class CountingCursor:
    """Cursor proxy that records every statement executed through it."""

    def __init__(self, cursor, statements):
        self._cursor = cursor
        self._statements = statements

    def execute(self, sql, params=()):
        self._statements.append(sql)
        return self._cursor.execute(sql, params)

    def executemany(self, sql, params):
        self._statements.append(sql)
        return self._cursor.executemany(sql, params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class CountingDb:
    """Wraps a `Database`, collecting the SQL run through `get_cursor()`."""

    def __init__(self, real_db):
        self._db = real_db
        self.statements = []

    @contextmanager
    def get_cursor(self):
        with self._db.get_cursor() as cursor:
            yield CountingCursor(cursor, self.statements)

    def __getattr__(self, name):
        return getattr(self._db, name)
