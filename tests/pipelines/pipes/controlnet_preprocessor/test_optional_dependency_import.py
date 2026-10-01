import importlib.abc
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes.controlnet_preprocessor import main as main_module

MAIN_PATH = Path(main_module.__file__)


class _BrokenFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "controlnet_aux":
            raise AttributeError("module 'mediapipe' has no attribute 'solutions'")
        return None


@pytest.fixture
def restore_modules():
    saved = dict(sys.modules)
    yield
    for name in [n for n in sys.modules if n not in saved]:
        del sys.modules[name]
    sys.modules.update(saved)


def _fresh_main(name):
    spec = importlib.util.spec_from_file_location(name, MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run(module, strict):
    pipe = module.ControlNetPreprocessorPipe(
        config={"preprocessors": [{"type": "canny", "enabled": True}], "strict": strict}
    )
    image = Image.new("RGB", (4, 4))
    return image, pipe.process(PipeInput(input={"image": [image]}), lambda _o: None)


def test_a_dependency_failing_with_any_exception_keeps_the_pipe_importable(monkeypatch, restore_modules):
    for name in [n for n in sys.modules if n == "controlnet_aux" or n.startswith("controlnet_aux.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [_BrokenFinder(), *sys.meta_path])

    module = _fresh_main("controlnet_preprocessor_broken_aux")

    assert module.ControlNetPreprocessorPipe.name == "controlnet_preprocessor"
    assert module.CONTROLNET_AUX_AVAILABLE is False
    assert "solutions" in module.CONTROLNET_AUX_ERROR

    image, result = _run(module, strict=False)
    assert result.output["image"] == [image]

    with pytest.raises(GenerationExecutionError, match="solutions"):
        _run(module, strict=True)


def test_controlnet_aux_imports_when_mediapipe_has_no_solutions(monkeypatch, restore_modules):
    pytest.importorskip("controlnet_aux")
    for name in [n for n in sys.modules if n == "controlnet_aux" or n.startswith("controlnet_aux.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "mediapipe", types.ModuleType("mediapipe"))

    module = _fresh_main("controlnet_preprocessor_no_solutions")

    assert module.CONTROLNET_AUX_AVAILABLE is True
    assert module.CONTROLNET_AUX_ERROR == ""
    with pytest.raises(RuntimeError, match="mediapipe"):
        sys.modules["controlnet_aux.mediapipe_face"].MediapipeFaceDetector()


def test_installed_controlnet_aux_detectors_import():
    pytest.importorskip("controlnet_aux")
    assert main_module.CONTROLNET_AUX_AVAILABLE, main_module.CONTROLNET_AUX_ERROR
