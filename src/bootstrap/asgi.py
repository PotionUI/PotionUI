"""Tiny, dependency-light helpers for uvicorn's ASGI server configuration.

Kept out of api.py itself so they can be imported and unit-tested without
pulling in api.py's module-level side effects (`load_dotenv()`,
`configure_logging()`, `create_app()`).
"""

from __future__ import annotations


def resolve_asgi_loop() -> str:
    """"uvloop" where installed, else uvicorn's own asyncio loop.

    uvloop has no Windows wheels, so it is an optional dependency there (see
    requirements.txt's `sys_platform != "win32"` marker). A bare
    `hasattr(__import__('uvloop'), ...)` check evaluates the import first and
    raises ModuleNotFoundError before any fallback can run; this catches that.
    """
    try:
        import uvloop  # noqa: F401
    except ImportError:
        return "asyncio"
    return "uvloop"
