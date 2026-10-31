import numpy as np
import pytest
from PIL import Image

from src.features.generation.engine import deep_update
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ProgressGenerationOutput
from src.pipelines.pipes.detailer.sdxl.main import ADetailerSDXLPipe

RED = (255, 0, 0)
BASE = (40, 120, 200)


class _StubModel:
    def img2img(self, generation_input, generation_outputs):
        size = {item.name: item.value for item in generation_input.input}["image"].size
        return type("Out", (), {"image": Image.new("RGB", size, RED)})()


def _run(tmp_path, kind, image_size, box, extra=None):
    detector = tmp_path / "detector.pt"
    detector.write_bytes(b"")
    detection = {
        "type": "yolo",
        "model": str(detector),
        "enabled": True,
        "strength": "0.4",
        "confidence": 0.3,
        "steps": 24,
        "cfg": 5,
        "mask_blur": 4,
        "padding": 32,
        **(extra or {}),
    }
    pipe = ADetailerSDXLPipe(
        config=deep_update(
            ADetailerSDXLPipe.get_default_config(),
            {"detect": [kind], "detections": {kind: detection}},
        )
    )
    pipe.helper.load_detector = lambda path: object()
    pipe.helper.detect_objects = lambda image, model, dtype, classes=None: [np.array(box, dtype=float)]
    source = Image.new("RGB", image_size, BASE)
    events = []
    out = pipe.process(
        PipeInput(input={"model": _StubModel(), "image": [source], "conditioning": [object()], "seed": [1]}),
        events.append,
    )
    return np.array(source), np.array(out.output["image"][0]), events


@pytest.mark.parametrize("size", [60, 80, 100, 160])
def test_hand_of_full_body_size_is_repainted(tmp_path, size):
    cx, cy = 400, 900
    box = [cx - size // 2, cy - size // 2, cx + size // 2, cy + size // 2]
    before, after, _ = _run(tmp_path, "hand", (1024, 1536), box)
    assert np.abs(after.astype(int) - before.astype(int)).max() > 0
    assert tuple(after[cy, cx]) != BASE


def test_hand_outside_size_limit_is_reported(tmp_path):
    before, after, events = _run(
        tmp_path, "hand", (1024, 1536), [400, 900, 410, 910]
    )
    assert np.array_equal(before, after)
    assert any(
        isinstance(e, ProgressGenerationOutput) and "Skipped" in e.state for e in events
    )
