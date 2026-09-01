"""Tests for the generator/trellis2 pipe.

Two layers. Most tests replace ``run_image_to_mesh`` with a recorder and assert
what this pipe is actually responsible for: seed resolution, assembling the
per-stage sampler settings from the form, passing the bundle's tier through,
and turning the returned volume into a mesh output. One test then runs the
whole thing for real — real cascade, real dual-grid extraction, real bake — on
staged models small enough to run on CPU, and checks a GLB lands on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    MeshGenerationOutput,
    SeedGenerationOutput,
)
from src.pipelines.pipes.generator.trellis2 import main as generator_main
from src.pipelines.pipes.generator.trellis2.main import GeneratorTrellis2Pipe
from src.platform.runtime.native.arch.trellis2.config import SSFlowConfig
from src.platform.runtime.native.arch.trellis2.image_to_mesh import MeshVolume
from src.platform.runtime.native.arch.trellis2.octree_vae import FdgDecoderOutput
from src.platform.runtime.native.sparse3d import SparseTensor

LATENT_CHANNELS = 32


# -- fakes ------------------------------------------------------------------


def _cube_coords():
    return torch.tensor(
        [[0, x, y, z] for x in range(2) for y in range(2) for z in range(2)],
        dtype=torch.int32,
    )


def _cube_volume(resolution=512):
    """The ``MeshVolume`` a run of the closed-cube fakes produces."""
    coords = _cube_coords()
    vertices = coords[:, 1:].float() / resolution - 0.5
    faces = torch.zeros((12, 3), dtype=torch.long)
    return MeshVolume(
        vertices=vertices,
        faces=faces,
        attrs=torch.full((8, 6), 0.75),
        coords=coords[:, 1:],
        resolution=resolution,
    )


class _Placed:
    def to(self, device):
        return self


class _FakeConditioner(_Placed):
    def encode(self, image, size):
        return torch.full((1, 4, 8), float(size) / 1000.0)

    @staticmethod
    def negative(cond):
        return torch.zeros_like(cond)


class _FakeSSFlow(_Placed):
    config = SSFlowConfig(
        resolution=4, in_channels=8, model_channels=16, cond_channels=8,
        out_channels=8, num_blocks=1, num_heads=2,
    )

    def __call__(self, x, t, cond, **kwargs):
        return torch.zeros_like(x)


class _FakeSSVAE(_Placed):
    def __call__(self, latent):
        occupancy = torch.full((1, 1, 64, 64, 64), -1.0)
        occupancy[0, 0, :2, :2, :2] = 1.0
        return occupancy


class _FakeSLatFlow(_Placed):
    def __init__(self, in_channels=LATENT_CHANNELS):
        self.in_channels = in_channels

    def __call__(self, x, t, cond, concat_cond=None, **kwargs):
        return x.replace(x.feats[:, :LATENT_CHANNELS] * 0.0 + 1.0)


class _FakeShapeDecoder(_Placed):
    """Emits the dual-grid description of a closed cube — the recipe from
    ``tests/.../trellis2/test_dual_grid.py``, so the real extraction produces a
    real 12-triangle manifold for the bake to work on."""

    def set_resolution(self, resolution):
        self.resolution = resolution

    def upsample(self, slat, upsample_times):
        rows = slat.feats.shape[0]
        return torch.stack([
            torch.zeros(rows, dtype=torch.int32),
            torch.arange(rows, dtype=torch.int32),
            torch.zeros(rows, dtype=torch.int32),
            torch.zeros(rows, dtype=torch.int32),
        ], dim=1)

    def __call__(self, slat, return_subs=False):
        coords = _cube_coords()
        index = {tuple(c.tolist()[1:]): i for i, c in enumerate(coords)}
        intersected = torch.zeros((coords.shape[0], 3), dtype=torch.bool)
        for base, axis in [
            ((0, 0, 0), 0), ((1, 0, 0), 0),
            ((0, 0, 0), 1), ((0, 1, 0), 1),
            ((0, 0, 0), 2), ((0, 0, 1), 2),
        ]:
            intersected[index[base], axis] = True

        state = SparseTensor(feats=torch.zeros((coords.shape[0], 3)), coords=coords)
        return FdgDecoderOutput(
            coords=coords,
            vertices=state,
            intersected=state.replace(intersected),
            quad_lerp=state.replace(torch.ones((coords.shape[0], 1))),
            subs=["level"] if return_subs else None,
        )


class _FakeTexDecoder(_Placed):
    def __call__(self, slat, guide_subs=None):
        coords = _cube_coords()
        return SparseTensor(feats=torch.full((coords.shape[0], 6), 0.5), coords=coords)


class _FakeComponents:
    """Stands in for ``Trellis2Components`` where the run itself is faked."""


class _FakeBundle:
    def __init__(self, tier="1024", components=None):
        self.tier = tier
        self.device = "cpu"
        self._components = components if components is not None else _FakeComponents()

    def components(self):
        return self._components


def _real_components():
    from src.platform.runtime.native.arch.trellis2.image_to_mesh import Trellis2Components

    return Trellis2Components(
        conditioner=_FakeConditioner(),
        ss_flow=_FakeSSFlow(),
        ss_vae=_FakeSSVAE(),
        shape_flow_lr=_FakeSLatFlow(),
        shape_flow_hr=_FakeSLatFlow(),
        shape_decoder=_FakeShapeDecoder(),
        tex_flow=_FakeSLatFlow(in_channels=LATENT_CHANNELS * 2),
        tex_decoder=_FakeTexDecoder(),
    )


@pytest.fixture
def recorded_run(monkeypatch):
    """Replace the cascade with a recorder that returns a cube volume."""
    calls = []

    def _run(components, image, **kwargs):
        calls.append({"components": components, "image": image, **kwargs})
        return _cube_volume()

    monkeypatch.setattr(generator_main, "run_image_to_mesh", _run)
    return calls


@pytest.fixture
def exported(monkeypatch, tmp_path):
    """Replace the bake with a recorder that writes a placeholder file."""
    calls = []

    def _export(**kwargs):
        calls.append(kwargs)
        Path(kwargs["out_path"]).write_bytes(b"glTF-placeholder")

    monkeypatch.setattr(generator_main, "postprocess_to_glb", _export)
    return calls


def _config(**over):
    config = GeneratorTrellis2Pipe.get_default_config()
    config["device"] = "cpu"
    config.update(over)
    return config


def _run_pipe(config=None, images=None, bundle=None, seeds=None):
    pipe = GeneratorTrellis2Pipe(config or _config())
    payload = {
        "model": bundle if bundle is not None else _FakeBundle(),
        "image": images if images is not None else [Image.new("RGB", (32, 32), (9, 9, 9))],
    }
    if seeds is not None:
        payload["seed"] = seeds

    emitted = []
    out = pipe.process(PipeInput(input=payload), emitted.append)
    return out, emitted


# -- contract ---------------------------------------------------------------


def test_declares_a_mesh_output():
    outputs = {spec.name: spec for spec in GeneratorTrellis2Pipe.outputs()}
    assert outputs["mesh"].io_type.value == "MESH"
    assert outputs["mesh"].is_array


def test_a_missing_image_is_refused():
    pipe = GeneratorTrellis2Pipe(_config())
    with pytest.raises(ValueError, match="needs a source image"):
        pipe.process(PipeInput(input={"model": _FakeBundle(), "image": []}), lambda o: None)


def test_a_missing_bundle_is_refused():
    pipe = GeneratorTrellis2Pipe(_config())
    with pytest.raises(ValueError, match="model bundle"):
        pipe.process(
            PipeInput(input={"image": [Image.new("RGB", (8, 8))]}), lambda o: None
        )


# -- seeds ------------------------------------------------------------------


def test_wired_seeds_are_used_as_given(recorded_run, exported):
    out, _ = _run_pipe(
        images=[Image.new("RGB", (8, 8))] * 2, seeds=[11, 22]
    )
    assert out.output["seed"] == [11, 22]
    assert [call["seed"] for call in recorded_run] == [11, 22]


def test_a_configured_seed_is_offset_per_image(recorded_run, exported):
    """Matching ``seed_generator``: a batch stays reproducible without every
    mesh in it being identical."""
    out, _ = _run_pipe(_config(seed=100), images=[Image.new("RGB", (8, 8))] * 3)
    assert out.output["seed"] == [100, 101, 102]


def test_seed_minus_one_draws_a_fresh_one(recorded_run, exported):
    out, _ = _run_pipe(_config(seed=-1))
    assert out.output["seed"][0] >= 0


def test_each_seed_is_emitted_as_an_artifact(recorded_run, exported):
    _, emitted = _run_pipe(images=[Image.new("RGB", (8, 8))] * 2, seeds=[5, 6])
    seeds = [o for o in emitted if isinstance(o, SeedGenerationOutput)]
    assert [(o.index, o.seed) for o in seeds] == [(0, 5), (1, 6)]


# -- the cascade seam -------------------------------------------------------


def test_the_tier_comes_from_the_bundle_not_this_pipes_config(recorded_run, exported):
    """The tier decides which flow models the loader acquired, so it cannot be
    configurable here too — the two would drift."""
    _run_pipe(bundle=_FakeBundle(tier="1536"))
    assert recorded_run[0]["tier"] == "1536"


def test_form_values_reach_the_run(recorded_run, exported):
    _run_pipe(_config(remove_background=True, max_num_tokens=8192))
    assert recorded_run[0]["remove_background"] is True
    assert recorded_run[0]["max_num_tokens"] == 8192


def test_stage_overrides_replace_only_what_the_form_sets(recorded_run, exported):
    _run_pipe(_config(shape_steps=30, texture_guidance_strength=2.5))
    settings = recorded_run[0]["stage_settings"]

    assert settings["shape"].steps == 30
    assert settings["texture"].guidance_strength == 2.5
    # Untouched values keep the published defaults.
    assert settings["sparse_structure"].steps == 12
    assert settings["shape"].guidance_rescale == 0.5


def test_guidance_interval_is_passed_through_rather_than_exposed(recorded_run, exported):
    """It is not a value a user tunes, but omitting it and passing a wrong one
    are different failures — so it is carried, not dropped."""
    settings = _run_pipe(_config())[0] and recorded_run[0]["stage_settings"]
    assert settings["sparse_structure"].guidance_interval == (0.6, 1.0)
    assert settings["texture"].guidance_interval == (0.6, 0.9)


# -- export -----------------------------------------------------------------


def test_the_bake_uses_the_volumes_own_voxel_size(recorded_run, exported, monkeypatch):
    """A token-budget degrade decodes below the requested tier; baking against
    the tier's voxel size instead would sample the attribute volume off-grid."""
    monkeypatch.setattr(
        generator_main, "run_image_to_mesh",
        lambda components, image, **kwargs: _cube_volume(resolution=1280),
    )
    _run_pipe(bundle=_FakeBundle(tier="1536"))
    assert exported[0]["voxel_size"] == pytest.approx(1 / 1280)


def test_bake_settings_come_from_the_form(recorded_run, exported):
    _run_pipe(_config(decimation_target=40000, texture_size=1024, project_to_source=True))
    assert exported[0]["decimation_target"] == 40000
    assert exported[0]["texture_size"] == 1024
    assert exported[0]["project_to_source"] is True


def test_an_empty_decode_is_reported_as_a_generation_failure(recorded_run, monkeypatch):
    def _empty(**kwargs):
        raise ValueError("no geometry to post-process: 0 input faces cleaned down to nothing")

    monkeypatch.setattr(generator_main, "postprocess_to_glb", _empty)
    with pytest.raises(ValueError, match="no exportable geometry"):
        _run_pipe()


# -- outputs ----------------------------------------------------------------


def test_the_mesh_is_emitted_for_the_gallery_and_kept(recorded_run, exported):
    _, emitted = _run_pipe(seeds=[42])
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1

    mesh = gallery[0].meshes[0]
    assert isinstance(mesh, MeshGenerationOutput)
    assert mesh.temporary is False
    assert mesh.seed == 42
    assert mesh.vertex_count == 8
    assert mesh.face_count == 12


def test_the_returned_paths_are_the_emitted_ones(recorded_run, exported):
    out, emitted = _run_pipe()
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
    assert out.output["mesh"] == [gallery.meshes[0].mesh_path]


def test_every_image_produces_its_own_mesh(recorded_run, exported):
    out, _ = _run_pipe(images=[Image.new("RGB", (8, 8))] * 3)
    assert len(out.output["mesh"]) == 3
    assert len(set(out.output["mesh"])) == 3


# -- end to end -------------------------------------------------------------


def test_a_whole_run_writes_a_real_glb():
    """The real cascade, the real dual-grid extraction and the real bake, over
    staged models small enough for CPU. Nothing between the pipe and the file
    on disk is faked."""
    pipe = GeneratorTrellis2Pipe(_config(
        seed=3, texture_size=64, decimation_target=100000,
        sparse_structure_steps=2, shape_steps=2, texture_steps=2,
    ))
    bundle = _FakeBundle(tier="1024", components=_real_components())

    emitted = []
    out = pipe.process(
        PipeInput(input={"model": bundle, "image": [Image.new("RGB", (64, 64), (180, 90, 40))]}),
        emitted.append,
    )

    path = Path(out.output["mesh"][0])
    assert path.suffix == ".glb"
    assert path.stat().st_size > 0
    assert path.read_bytes()[:4] == b"glTF"

    mesh = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0].meshes[0]
    assert mesh.temporary is False
    assert mesh.face_count == 12
    path.unlink()
