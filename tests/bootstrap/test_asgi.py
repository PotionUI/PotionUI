"""resolve_asgi_loop() must not raise even when uvloop (no Windows wheels) is
absent - a bare `hasattr(__import__('uvloop'), ...)` check would raise
ModuleNotFoundError there before any fallback could run.
"""
import sys

from src.bootstrap.asgi import resolve_asgi_loop


def test_uses_uvloop_when_installed():
    assert resolve_asgi_loop() == "uvloop"


def test_falls_back_to_asyncio_when_uvloop_is_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "uvloop", None)
    assert resolve_asgi_loop() == "asyncio"
