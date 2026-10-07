from __future__ import annotations

from pathlib import Path

import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GalleryGenerationOutput
from src.pipelines.pipes.generator.pixal3d import main as generator_main
from src.pipelines.pipes.generator.pixal3d.main import GeneratorPixal3DPipe
from src.pipelines.pipes.generator.trellis2 import main as trellis2_generator_main
from src.platform.runtime.native.arch.pixal3d.image_to_mesh import Pixal3DViews
from src.platform.runtime.native.arch.trellis2.image_to_mesh import MeshVolume
from src.platform.runtime.native.errors import SamplingCancelled

RESOLUTION = 8


def _volume():
    coords = torch.tensor([[0, 1, 2], [3, 4, 5], [6, 7, 0]], dtype=torch.int32)
    vertices = torch.tensor([[1.0, 2.0, 3.0], [-4.0, 5.0, 6.0], [7.0, -8.0, 9.0]])
    return MeshVolume(
        vertices=vertices,
        faces=torch.zeros((12, 3), dtype=torch.long),
        attrs=torch.full((3, 6), 0.5),
        coords=coords,
        resolution=RESOLUTION,
    )


class _FakeBundle:
    def __init__(self, variant="single", tier="1536"):
        self.variant = variant
        self.tier = tier
        self.device = "cpu"

    def components(self):
        return object()


def _image(color=9):
    return Image.new("RGB", (16, 16), (color, color, color))


@pytest.fixture
def recorded_run(monkeypatch):
    calls = []

    def _run(components, image, **kwargs):
        calls.append({"components": components, "image": image, **kwargs})
        return _volume()

    monkeypatch.setattr(generator_main, "run_pixal3d", _run)
    return calls


@pytest.fixture
def exported(monkeypatch):
    calls = []

    def _export(**kwargs):
        calls.append(kwargs)
        Path(kwargs["out_path"]).write_bytes(b"glTF-placeholder")

    monkeypatch.setattr(trellis2_generator_main, "postprocess_to_glb", _export)
    return calls


def _config(**over):
    config = GeneratorPixal3DPipe.get_default_config()
    config["device"] = "cpu"
    config.update(over)
    return config


def _run_pipe(config=None, bundle=None, is_cancelled=None, **inputs):
    pipe = GeneratorPixal3DPipe(config or _config())
    payload = {"model": bundle if bundle is not None else _FakeBundle(), "image": [_image()], **inputs}
    emitted = []
    kwargs = {} if is_cancelled is None else {"is_cancelled": is_cancelled}
    out = pipe.process(PipeInput(input=payload), emitted.append, **kwargs)
    return out, emitted


def test_configuration_adds_camera_fov_and_export_frame():
    specs = {spec.name: spec for spec in GeneratorPixal3DPipe.configuration()}
    assert specs["camera_fov"].default == 49.13
    assert specs["export_frame"].default == "upstream"
    assert specs["export_frame"].choices == ["upstream", "camera"]
    defaults = GeneratorPixal3DPipe.get_default_config()
    assert defaults["camera_fov"] == 49.13
    assert defaults["export_frame"] == "upstream"


def test_the_side_views_are_optional_image_arrays():
    inputs = {spec.name: spec for spec in GeneratorPixal3DPipe.inputs()}
    assert inputs["image"].required
    for view in ("left", "back", "right"):
        assert inputs[view].io_type.value == "IMAGE"
        assert inputs[view].is_array
        assert not inputs[view].required


def test_a_single_view_bundle_runs_once_per_image_with_each_image_as_is(recorded_run, exported):
    images = [_image(1), _image(2)]
    _run_pipe(bundle=_FakeBundle("single", tier="1024"), image=images)
    assert len(recorded_run) == 2
    assert recorded_run[0]["image"] is images[0]
    assert recorded_run[1]["image"] is images[1]
    assert recorded_run[0]["tier"] == "1024"


def test_camera_fov_reaches_the_run_as_fov_deg(recorded_run, exported):
    _run_pipe()
    assert recorded_run[0]["fov_deg"] == pytest.approx(49.13)
    recorded_run.clear()
    _run_pipe(_config(camera_fov=35.0))
    assert recorded_run[0]["fov_deg"] == 35.0


@pytest.mark.parametrize("sides", [["left"], ["back", "right"]])
def test_side_views_with_a_single_view_bundle_are_refused_by_name(recorded_run, exported, sides):
    wired = {side: [_image()] for side in sides}
    with pytest.raises(ValueError) as excinfo:
        _run_pipe(**wired)
    message = str(excinfo.value)
    assert all(side in message for side in sides)
    assert "pixal3d_multiview_bf16.safetensors" in message
    assert recorded_run == []


def test_a_multiview_bundle_runs_once_with_views_ordered_front_left_back_right(recorded_run, exported):
    _run_pipe(
        bundle=_FakeBundle("multiview"),
        image=[_image(1)], right=[_image(4)], back=[_image(3)], left=[_image(2)],
    )
    assert len(recorded_run) == 1
    views = recorded_run[0]["image"]
    assert isinstance(views, Pixal3DViews)
    assert views.names == ["front", "left", "back", "right"]


def test_a_multiview_run_with_only_some_views_keeps_just_those(recorded_run, exported):
    _run_pipe(bundle=_FakeBundle("multiview"), image=[_image(1)], back=[_image(3)])
    assert recorded_run[0]["image"].names == ["front", "back"]


def test_a_multiview_bundle_without_a_front_view_is_refused(recorded_run, exported):
    with pytest.raises(ValueError, match="needs a source image"):
        _run_pipe(bundle=_FakeBundle("multiview"), image=[], left=[_image()])
    assert recorded_run == []


def test_a_multiview_bundle_with_two_front_images_is_refused(recorded_run, exported):
    with pytest.raises(ValueError, match="exactly one front view"):
        _run_pipe(bundle=_FakeBundle("multiview"), image=[_image(1), _image(2)])
    assert recorded_run == []


def test_the_upstream_frame_is_the_default_and_maps_xyz_to_minus_x_z_y(recorded_run, exported):
    _run_pipe()
    source = _volume()
    last = RESOLUTION - 1
    assert len(exported) == 1
    expected_vertices = torch.stack(
        [-source.vertices[:, 0], source.vertices[:, 2], source.vertices[:, 1]], dim=-1)
    expected_coords = torch.stack(
        [last - source.coords[:, 0], source.coords[:, 2], source.coords[:, 1]], dim=-1)
    assert torch.equal(exported[0]["vertices"], expected_vertices)
    assert torch.equal(exported[0]["coords"], expected_coords)


def test_the_camera_frame_maps_xyz_to_x_minus_z_y(recorded_run, exported):
    _run_pipe(_config(export_frame="camera"))
    source = _volume()
    last = RESOLUTION - 1
    expected_vertices = torch.stack(
        [source.vertices[:, 0], -source.vertices[:, 2], source.vertices[:, 1]], dim=-1)
    expected_coords = torch.stack(
        [source.coords[:, 0], last - source.coords[:, 2], source.coords[:, 1]], dim=-1)
    assert torch.equal(exported[0]["vertices"], expected_vertices)
    assert torch.equal(exported[0]["coords"], expected_coords)


def test_an_unknown_export_frame_is_refused(recorded_run, exported):
    with pytest.raises(ValueError, match="export frame"):
        _run_pipe(_config(export_frame="sideways"))
    assert exported == []


def test_the_mesh_is_emitted_for_the_gallery(recorded_run, exported):
    out, emitted = _run_pipe()
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1
    assert gallery[0].meshes[0].vertex_count == 3
    assert gallery[0].meshes[0].face_count == 12
    assert out.output["mesh"] == [gallery[0].meshes[0].mesh_path]
    Path(out.output["mesh"][0]).unlink()


def test_the_cancellation_probe_reaches_the_run(recorded_run, exported):
    probe = lambda: False
    out, _ = _run_pipe(is_cancelled=probe)
    assert recorded_run[0]["is_cancelled"] is probe
    Path(out.output["mesh"][0]).unlink()


def test_a_run_cancelled_up_front_never_reconstructs_or_exports(recorded_run, exported):
    with pytest.raises(SamplingCancelled):
        _run_pipe(is_cancelled=lambda: True)
    assert recorded_run == []
    assert exported == []
