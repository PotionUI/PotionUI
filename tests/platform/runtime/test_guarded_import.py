import sys
import threading
import time

import pytest

from src.platform.runtime import guarded_import as gi

PKG = "racepkg"

A_SOURCE = """
import time
VALUE = 1
time.sleep(0.3)
from . import b
DONE = b.DERIVED
"""

B_SOURCE = """
import time
time.sleep(0.15)
from .a import VALUE
DERIVED = VALUE + 1
"""


def _write_package(root, a_source=A_SOURCE, b_source=B_SOURCE):
    pkg = root / PKG
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text(a_source)
    (pkg / "b.py").write_text(b_source)


@pytest.fixture
def racepkg(tmp_path, monkeypatch):
    _write_package(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(gi, "GUARDED_ROOTS", frozenset({PKG}))
    yield tmp_path
    for name in [n for n in sys.modules if n == PKG or n.startswith(PKG + ".")]:
        del sys.modules[name]


def _race(importers):
    barrier = threading.Barrier(len(importers))
    errors: list[BaseException] = []

    def run(importer):
        barrier.wait()
        if importer is not importers[0]:
            time.sleep(0.05)
        try:
            importer()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in importers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_unguarded_concurrent_first_imports_of_a_cycle_fail(racepkg):
    errors = _race([lambda: __import__(f"{PKG}.a"), lambda: __import__(f"{PKG}.b")])
    assert errors, "the stand-in cycle must reproduce the partial-module failure without the lock"
    assert any(isinstance(e, (ImportError, AttributeError)) for e in errors)


def test_guarded_concurrent_first_imports_of_a_cycle_succeed(racepkg):
    errors = _race([lambda: gi.guarded_import(f"{PKG}.a"), lambda: gi.guarded_import(f"{PKG}.b")])
    assert errors == []
    assert sys.modules[f"{PKG}.a"].DONE == 2


def test_guarded_from_resolves_names_under_the_same_lock(racepkg):
    errors = _race([lambda: gi.guarded_from(f"{PKG}.a", "DONE"), lambda: gi.guarded_from(f"{PKG}.b", "DERIVED")])
    assert errors == []
    assert gi.guarded_from(f"{PKG}.a", "VALUE", "DONE") == (1, 2)


def test_a_missing_name_surfaces_as_import_error_without_purging_the_module(racepkg):
    (racepkg / PKG / "b.py").write_text("DERIVED = 2\n")
    gi.guarded_import(f"{PKG}.b")
    with pytest.raises(ImportError, match="NOPE"):
        gi.guarded_from(f"{PKG}.b", "NOPE")
    assert f"{PKG}.b" in sys.modules


def test_a_failed_import_leaves_no_half_initialised_modules_and_a_retry_succeeds(racepkg):
    (racepkg / PKG / "b.py").write_text("from .a import VALUE\nraise ImportError('transient')\n")
    (racepkg / PKG / "a.py").write_text("VALUE = 1\nfrom . import b\n")
    with pytest.raises(ImportError, match="transient"):
        gi.guarded_import(f"{PKG}.a")
    assert [n for n in sys.modules if n == PKG or n.startswith(PKG + ".")] == []
    (racepkg / PKG / "b.py").write_text("DERIVED = 2\n")
    (racepkg / PKG / "a.py").write_text("VALUE = 1\nfrom . import b\nDONE = b.DERIVED\n")
    assert gi.guarded_import(f"{PKG}.a").DONE == 2


def test_a_broken_leftover_from_an_unguarded_race_is_healed_by_the_clean_retry(racepkg):
    gi.guarded_import(PKG)
    stale = type(sys)(f"{PKG}.a")
    sys.modules[f"{PKG}.a"] = stale
    (racepkg / PKG / "a.py").write_text("VALUE = 1\n")
    (racepkg / PKG / "c.py").write_text("from .a import VALUE\nGOT = VALUE\n")
    assert gi.guarded_import(f"{PKG}.c").GOT == 1
    assert sys.modules[f"{PKG}.a"] is not stale


def test_a_module_that_is_not_installed_is_reported_untouched(racepkg):
    with pytest.raises(ModuleNotFoundError):
        gi.guarded_import(f"{PKG}.absent")


def test_other_roots_are_never_purged_after_a_failure(racepkg, tmp_path, monkeypatch):
    other = tmp_path / "otherroot"
    other.mkdir()
    (other / "__init__.py").write_text("")
    (other / "bad.py").write_text("from . import nothing_here\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    import otherroot

    with pytest.raises(ImportError):
        gi.guarded_import("otherroot.bad")
    assert "otherroot" in sys.modules
    del sys.modules["otherroot"]


def test_warm_up_imports_every_installed_name_in_order_and_skips_missing_ones(racepkg, monkeypatch):
    seen = []
    monkeypatch.setattr(gi, "guarded_import", lambda name: seen.append(name))
    assert gi.warm_up_heavy_imports((PKG, "definitely_not_installed_pkg", "json")) == []
    assert seen == [PKG, "json"]


def test_warm_up_reports_failures_and_keeps_going(racepkg, monkeypatch):
    def fake(name):
        if name == "json":
            raise ImportError("boom")

    monkeypatch.setattr(gi, "guarded_import", fake)
    assert gi.warm_up_heavy_imports(("json", PKG)) == ["json"]


def test_a_non_import_error_also_leaves_no_half_initialised_modules(racepkg):
    (racepkg / PKG / "b.py").write_text("from .a import VALUE\nraise RuntimeError('native init failed')\n")
    (racepkg / PKG / "a.py").write_text("VALUE = 1\nfrom . import b\n")
    with pytest.raises(RuntimeError, match="native init"):
        gi.guarded_import(f"{PKG}.a")
    assert [n for n in sys.modules if n == PKG or n.startswith(PKG + ".")] == []
    (racepkg / PKG / "b.py").write_text("DERIVED = 2\n")
    (racepkg / PKG / "a.py").write_text("VALUE = 1\nfrom . import b\nDONE = b.DERIVED\n")
    assert gi.guarded_import(f"{PKG}.a").DONE == 2
