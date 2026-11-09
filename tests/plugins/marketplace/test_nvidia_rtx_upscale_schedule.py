import importlib.util
from pathlib import Path

import pytest

from src.pipelines.pipes.interpolator.rife import cadence as rife_cadence
from src.pipelines.pipes.interpolator.rife import main as rife_main
from src.plugin_api import media

VFG_MAIN = (
    Path(__file__).resolve().parents[3]
    / "content" / "plugins" / "marketplace" / "nvidia-rtx-upscale" / "pipes" / "interpolator_rtx" / "main.py"
)


@pytest.fixture(scope="module")
def vfg_module():
    spec = importlib.util.spec_from_file_location("_nvidia_rtx_upscale_schedule_test", str(VFG_MAIN))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rife_schedule(source_count, ratio):
    entries = []
    for index in range(rife_cadence.output_count(source_count, ratio)):
        at = rife_cadence.slot(index, ratio)
        if at.left >= source_count - 1:
            entries.append((source_count - 1, None))
        elif at.t == 0.0:
            entries.append((at.left, None))
        else:
            entries.append((at.left, at.t))
    return entries


def test_rife_and_the_plugin_api_share_one_schedule():
    for name in ("source_per_output", "output_count", "output_fps", "slot"):
        assert getattr(media, name) is getattr(rife_cadence, name)
        assert getattr(rife_main, name) is getattr(rife_cadence, name)


@pytest.mark.parametrize("source_fps,factor,target_fps,source_count", [
    (24000 / 1001, 2, 60.0, 1400),
    (24000 / 1001, 2, 60.0, 86313),
    (23.98, 2, 60.0, 5400),
    (30000 / 1001, 2, 24000 / 1001, 3000),
    (24000 / 1001, 2, 0.0, 1400),
    (24000 / 1001, 4, 0.0, 1400),
    (25.0, 2, 60.0, 125),
])
def test_rtx_frame_generation_places_the_frames_rife_places(vfg_module, source_fps, factor, target_fps, source_count):
    ratio = rife_cadence.source_per_output(source_fps, factor, target_fps)

    assert vfg_module.plan(source_count, ratio) == _rife_schedule(source_count, ratio)
    assert vfg_module.output_fps(source_fps, factor, target_fps) == rife_cadence.output_fps(source_fps, factor, target_fps)
