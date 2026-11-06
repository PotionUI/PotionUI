import ast

from tests.bootstrap.lifespan_support import lifespan


def _statements():
    body = lifespan().body
    split = next(i for i, stmt in enumerate(body) if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Yield))
    return [ast.unparse(stmt) for stmt in body[:split]], [ast.unparse(stmt) for stmt in body[split + 1:]]


def test_the_brew_coordinator_starts_with_the_app():
    startup, _ = _statements()

    assert "container.elixirs.runtime.start()" in startup


def test_the_brew_coordinator_stops_before_the_database_pool_closes():
    _, shutdown = _statements()
    stop = shutdown.index("await container.elixirs.runtime.stop()")
    close = next(i for i, text in enumerate(shutdown) if "Database.close_pool" in text)

    assert stop < close
