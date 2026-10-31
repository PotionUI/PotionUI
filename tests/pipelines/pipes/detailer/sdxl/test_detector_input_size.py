import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image

from src.features.generation.engine import deep_update
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ProgressGenerationOutput
from src.pipelines.pipes._shared.detection.detailer_helper import DETECTOR_IMGSZ, DetailerHelper
from src.pipelines.pipes.detailer.sdxl.main import ADetailerSDXLPipe


class _FakeYolo:
    def __init__(self):
        self.kwargs = None

    def predict(self, **kwargs):
        self.kwargs = kwargs
        boxes = SimpleNamespace(xyxy=torch.tensor([[10.0, 20.0, 60.0, 80.0]]), conf=torch.tensor([0.9]))
        return [SimpleNamespace(boxes=boxes)]


def _helper(extra=None):
    detection = {"confidence": 0.3, **(extra or {})}
    return DetailerHelper({"detections": {"hand": detection}})


def test_detector_runs_at_its_training_size_not_the_image_size():
    fake = _FakeYolo()
    _helper().detect_objects(Image.new("RGB", (1344, 2016)), fake, "hand")
    assert fake.kwargs["imgsz"] == DETECTOR_IMGSZ == 640


def test_detector_size_can_be_overridden():
    fake = _FakeYolo()
    _helper({"imgsz": 1024}).detect_objects(Image.new("RGB", (1344, 2016)), fake, "hand")
    assert fake.kwargs["imgsz"] == 1024


def test_box_is_returned_in_image_coordinates():
    boxes = _helper().detect_objects(Image.new("RGB", (400, 400)), _FakeYolo(), "hand")
    assert np.allclose(boxes[0], [10, 20, 60, 80])


class _RedModel:
    def img2img(self, generation_input, generation_outputs):
        size = {item.name: item.value for item in generation_input.input}["image"].size
        return type("Out", (), {"image": Image.new("RGB", size, (255, 0, 0))})()


def _pipe(model_path):
    detection = {
        "type": "yolo",
        "model": model_path,
        "enabled": True,
        "strength": 0.4,
        "confidence": 0.3,
        "steps": 24,
        "cfg": 5,
        "mask_blur": 4,
        "padding": 32,
        "device": "cpu",
    }
    return ADetailerSDXLPipe(
        config=deep_update(
            ADetailerSDXLPipe.get_default_config(),
            {"detect": ["hand"], "device": "cpu", "detections": {"hand": detection}},
        )
    )


def test_missing_detection_is_reported(tmp_path):
    weights = tmp_path / "hand.pt"
    weights.write_bytes(b"")
    pipe = _pipe(str(weights))
    pipe.helper.load_detector = lambda path: object()
    pipe.helper.detect_objects = lambda image, model, dtype, classes=None: []
    events = []
    pipe.process(
        PipeInput(input={"model": _RedModel(), "image": [Image.new("RGB", (256, 256))], "conditioning": [object()], "seed": [1]}),
        events.append,
    )
    assert any(isinstance(e, ProgressGenerationOutput) and "No hand found" in e.state for e in events)


def _real_paths():
    weights = os.environ.get("POTIONUI_TEST_HAND_YOLO")
    image = os.environ.get("POTIONUI_TEST_HAND_IMAGE")
    if not weights or not image or not Path(weights).is_file() or not Path(image).is_file():
        return None
    return weights, image


@pytest.mark.skipif(_real_paths() is None, reason="hand detector weights or sample image not provided")
def test_real_detector_repaints_the_detected_hand(monkeypatch):
    monkeypatch.setenv("YOLO_OFFLINE", "true")
    pytest.importorskip("ultralytics")
    weights, image_path = _real_paths()
    source = Image.open(image_path).convert("RGB")
    pipe = _pipe(weights)
    out = pipe.process(
        PipeInput(input={"model": _RedModel(), "image": [source], "conditioning": [object()], "seed": [1]}),
        lambda event: None,
    )
    before = np.array(source).astype(int)
    after = np.array(out.output["image"][0]).astype(int)
    changed = np.abs(after - before).max(axis=2) > 0
    assert changed.any()
    ys, xs = np.nonzero(changed)
    boxes = pipe.helper.detect_objects(source, pipe.helper.load_detector(weights), "hand")
    x1, y1, x2, y2 = boxes[0]
    pad = 80
    assert xs.min() >= x1 - pad and xs.max() <= x2 + pad
    assert ys.min() >= y1 - pad and ys.max() <= y2 + pad
