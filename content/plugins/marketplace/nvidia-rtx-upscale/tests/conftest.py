"""Test fixtures for the nvidia-rtx-upscale plugin."""

import importlib.util
import itertools
import sys
from enum import IntEnum
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import torch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PIPE_MAIN = PLUGIN_ROOT / "pipes" / "upscaler_rtx" / "main.py"

#: The only module the pipe must not touch at import time. `PipeCatalog`
#: imports pipe modules to discover them, and `_load_pipe_module` swallows any
#: exception and returns None - so a pipe that cannot import does not fail
#: loudly, it silently ceases to exist (see test_import_safety.py).
HEAVY_MODULES = ("nvvfx",)

_counter = itertools.count()


class BlockingFinder:
    """A meta-path finder that makes the named top-level packages unimportable.

    Raising from `find_spec` rather than returning None matters: returning None
    only means "I can't provide this", so a machine that HAS the package
    installed would fall through to the real finder and the test would pass
    vacuously there while failing on a bare host. Raising blocks the import on
    every machine, so the guard means the same thing everywhere.
    """

    def __init__(self, names):
        self.names = set(names)
        self.attempted = []

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in self.names:
            self.attempted.append(fullname)
            raise ImportError(f"{fullname} is blocked by the test harness")
        return None


def load_pipe_module(path: Path = PIPE_MAIN, blocked=()) -> ModuleType:
    """Import the pipe's `main.py` the way the catalog does, optionally with
    some modules made unimportable first.

    Each call gets a fresh module name so repeated loads do not hit the
    `sys.modules` cache and quietly skip the import being tested.
    """
    finder = BlockingFinder(blocked) if blocked else None
    evicted = {}

    if finder is not None:
        for name in list(sys.modules):
            if name.split(".")[0] in finder.names:
                evicted[name] = sys.modules.pop(name)
        sys.meta_path.insert(0, finder)

    module_name = f"_nvidia_rtx_upscale_test.pipe_{next(_counter)}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        assert spec is not None and spec.loader is not None, f"Cannot load {path}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if finder is not None:
            sys.meta_path.remove(finder)
            sys.modules.update(evicted)
        sys.modules.pop(module_name, None)


@pytest.fixture(scope="session")
def pipe_module() -> ModuleType:
    return load_pipe_module()


# Exposed as fixtures, not bare module-level names imported by test files:
# other plugins in this repo cross-import their conftest as
# `tests.plugins.<name>.conftest`, which resolves only when the main repo's
# `tests/plugins/<name>/` mirrors the plugin (it doesn't, for this plugin, in
# this checkout). Fixture injection sidesteps that path entirely - it is
# resolved by pytest's directory-based conftest discovery, not by import path.
@pytest.fixture(scope="session")
def pipe_main_path() -> Path:
    return PIPE_MAIN


@pytest.fixture(scope="session")
def heavy_modules():
    return HEAVY_MODULES


@pytest.fixture(scope="session")
def load_pipe_module_fn():
    return load_pipe_module


@pytest.fixture(scope="session")
def blocking_finder_cls():
    return BlockingFinder


class _FakeQualityLevel(IntEnum):
    BICUBIC = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    ULTRA = 4
    DENOISE_LOW = 5
    DENOISE_MEDIUM = 6
    DENOISE_HIGH = 7
    DENOISE_ULTRA = 8
    DEBLUR_LOW = 9
    DEBLUR_MEDIUM = 10
    DEBLUR_HIGH = 11
    DEBLUR_ULTRA = 12
    HIGHBITRATE_LOW = 13
    HIGHBITRATE_MEDIUM = 14
    HIGHBITRATE_HIGH = 15
    HIGHBITRATE_ULTRA = 16


@pytest.fixture
def fake_nvvfx(monkeypatch):
    """Installs a fake `nvvfx` module in `sys.modules` for the duration of the
    test. `FakeVideoSuperRes.run()` deliberately reuses the SAME tensor buffer
    across calls (overwritten in place, like the real DLPack-backed result the
    brief describes) so a pipe that forgets `.clone()` fails a test rather than
    the real GPU.
    """

    class FakeVideoSuperRes:
        QualityLevel = _FakeQualityLevel
        instances = []

        def __init__(self, quality):
            self.quality = quality
            self.output_width = None
            self.output_height = None
            self.load_calls = 0
            self.run_calls = 0
            self.closed = False
            self._buffer = None
            FakeVideoSuperRes.instances.append(self)

        def load(self):
            assert self.output_width and self.output_height, "load() called before output size was set"
            self.load_calls += 1

        def run(self, frame):
            h, w = self.output_height, self.output_width
            if self._buffer is None or tuple(self._buffer.shape[-2:]) != (h, w):
                self._buffer = torch.zeros(3, h, w)
            # Distinct value per call, still inside the [0,1] range `nvvfx`
            # itself uses -- 0.0, 0.5, 1.0 for calls 0, 1, 2.
            self._buffer.fill_(min(1.0, 0.5 * self.run_calls))
            self.run_calls += 1
            return SimpleNamespace(image=self._buffer)

        def close(self):
            self.closed = True

    module = ModuleType("nvvfx")
    module.VideoSuperRes = FakeVideoSuperRes
    monkeypatch.setitem(sys.modules, "nvvfx", module)
    return module


@pytest.fixture
def no_real_cuda(monkeypatch):
    """The real GPU on this host is shared and busy - tests must never touch
    it. `Tensor.cuda()` is patched to a no-op (return self) so pipe code that
    unconditionally moves a frame to CUDA keeps running entirely on CPU
    tensors under test, and `torch.cuda.is_available()` is forced False so the
    pipe's own `if torch.cuda.is_available(): torch.cuda.empty_cache()`
    branches never touch the device either."""
    monkeypatch.setattr(torch.Tensor, "cuda", lambda self, *a, **k: self)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
