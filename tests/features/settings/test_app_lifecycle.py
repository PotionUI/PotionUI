"""`restart_argv` must faithfully re-exec BOTH launch shapes.

The failure this guards against: under `python -m uvicorn` (the
`./potionui start` flow) runpy rewrites `sys.argv[0]` to
`site-packages/uvicorn/__main__.py`; naively re-exec'ing that path as a
script puts the uvicorn package dir at `sys.path[0]`, so the new process
resolves `import logging` to `uvicorn/logging.py` and dies on a circular
import -- with no supervisor to revive it, the in-app restart just kills
the backend.
"""

import sys
from types import ModuleType, SimpleNamespace

from src.features.settings.app_lifecycle import restart_argv


def _with_main(monkeypatch, spec, argv):
    main = ModuleType("__main__")
    main.__spec__ = spec
    monkeypatch.setitem(sys.modules, "__main__", main)
    monkeypatch.setattr(sys, "argv", argv)


def test_script_launch_reuses_argv_verbatim(monkeypatch):
    _with_main(monkeypatch, None, ["api.py"])
    assert restart_argv() == [sys.executable, "api.py"]


def test_module_launch_rebuilds_the_dash_m_form(monkeypatch):
    # `python -m uvicorn api:app --port 8005`: spec.name is the package's
    # `__main__` submodule and argv[0] is its FILE path -- the path must not
    # survive into the re-exec.
    _with_main(
        monkeypatch,
        SimpleNamespace(name="uvicorn.__main__"),
        ["/venv/lib/python3.12/site-packages/uvicorn/__main__.py", "api:app", "--port", "8005"],
    )
    assert restart_argv() == [sys.executable, "-m", "uvicorn", "api:app", "--port", "8005"]


def test_plain_module_launch_keeps_its_own_name(monkeypatch):
    # `python -m some_module` (a module, not a package): spec.name has no
    # `.__main__` suffix to strip.
    _with_main(monkeypatch, SimpleNamespace(name="some_module"), ["/x/some_module.py", "--flag"])
    assert restart_argv() == [sys.executable, "-m", "some_module", "--flag"]


def test_spec_without_a_name_falls_back_to_argv(monkeypatch):
    _with_main(monkeypatch, SimpleNamespace(name=None), ["api.py"])
    assert restart_argv() == [sys.executable, "api.py"]
