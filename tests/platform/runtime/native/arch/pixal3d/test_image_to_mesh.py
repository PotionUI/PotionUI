from __future__ import annotations

import math

import pytest
import torch
from PIL import Image

from src.platform.runtime.native.arch.pixal3d import image_to_mesh as p3d
from src.platform.runtime.native.arch.pixal3d.conditioning import ProjectedCondition
from src.platform.runtime.native.arch.pixal3d.projection import distance_from_fov, front_camera, orbit_camera
from src.platform.runtime.native.arch.trellis2 import image_to_mesh as trellis2_itm
from src.platform.runtime.native.arch.trellis2.config import SSFlowConfig, StageSampling
from src.platform.runtime.native.arch.trellis2.image_to_mesh import MeshVolume
from src.platform.runtime.native.arch.trellis2.octree_vae import FdgDecoderOutput
from src.platform.runtime.native.arch.trellis2.slat_flow import SLatFlowModel
from src.platform.runtime.native.arch.trellis2.ss_flow import SSFlowDiT
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.sparse3d import SparseTensor
from vendor.gpl.comfyui.ops import pick_operations

FAST = StageSampling(steps=2, guidance_strength=2.0, guidance_rescale=0.5, guidance_interval=(0.0, 1.0), rescale_t=1.0)
FAST_STAGES = {"sparse_structure": FAST, "shape": FAST, "texture": FAST}
DINO = 8


class _Placed:
    def __init__(self) -> None:
        self.placements: list[str] = []

    def to(self, device):
        self.placements.append(str(device))
        return self


class _FakeConditioner(_Placed):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[int, int]] = []

    def encode(self, pixels, size):
        self.calls.append((pixels.shape[0], size))
        grid = size // 16
        tokens = torch.full((pixels.shape[0], 5, DINO), size / 1000.0)
        patches = torch.linspace(-1, 1, grid * grid * DINO).reshape(1, grid * grid, DINO).repeat(pixels.shape[0], 1, 1)
        return torch.cat([tokens, patches], dim=1)


class _FakeNAF(_Placed):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[int, int, int]] = []

    def __call__(self, image, features, output_size, out_dtype=None):
        self.calls.append((image.shape[-1], features.shape[-1], output_size[0]))
        return torch.full((1, *output_size, features.shape[1]), 0.5, dtype=out_dtype or features.dtype)


class _Recording:
    def __init__(self, module) -> None:
        self.module = module
        self.conds: list = []
        self.placements: list[str] = []

    def __getattr__(self, name):
        return getattr(self.module, name)

    def to(self, device):
        self.placements.append(str(device))
        self.module.to(device)
        return self

    def __call__(self, x, t, cond, **kwargs):
        self.conds.append((x, cond))
        return self.module(x, t, cond, **kwargs)


def _randomised(module, seed):
    generator = torch.Generator().manual_seed(seed)
    with torch.no_grad():
        for parameter in module.parameters():
            parameter.copy_(torch.randn(parameter.shape, generator=generator) * 0.05)
    return module.eval()


def _ss_flow():
    config = SSFlowConfig(
        resolution=4, in_channels=8, model_channels=16, cond_channels=DINO, out_channels=8, num_blocks=1,
        num_heads=2, mlp_ratio=2.0, share_mod=True, qk_rms_norm=True, qk_rms_norm_cross=True,
        image_attn_mode="proj", proj_in_channels=DINO,
    )
    module = SSFlowDiT(config, pick_operations(torch.float32, torch.float32))
    module.post_load()
    return _Recording(_randomised(module, 1))


def _slat_flow(in_channels, seed):
    module = SLatFlowModel(
        resolution=32, in_channels=in_channels, model_channels=16, cond_channels=DINO, out_channels=32,
        num_blocks=1, num_heads=2, mlp_ratio=2.0, share_mod=True, qk_rms_norm=True, qk_rms_norm_cross=True,
        image_attn_mode="proj", proj_in_channels=2 * DINO,
    )
    return _Recording(_randomised(module, seed))


class _FakeSSVAE(_Placed):
    def __call__(self, latent):
        occupancy = torch.full((1, 1, 64, 64, 64), -1.0)
        occupancy[0, 0, 20:28, 30:34, 40:46] = 1.0
        return occupancy


def _closed_cube_coords():
    return torch.tensor([[0, x, y, z] for x in range(2) for y in range(2) for z in range(2)], dtype=torch.int32)


class _FakeShapeDecoder(_Placed):
    def __init__(self) -> None:
        super().__init__()
        self.resolutions: list[int] = []

    def set_resolution(self, resolution):
        self.resolutions.append(resolution)

    def upsample(self, slat, upsample_times):
        coords = slat.coords[:, 1:].long() * 16
        offsets = torch.tensor([[0, 0, 0], [8, 4, 2], [15, 15, 15]])
        rows = (coords[:, None, :] + offsets[None]).reshape(-1, 3)
        return torch.cat([torch.zeros(rows.shape[0], 1, dtype=torch.int32), rows.int()], dim=1)

    def __call__(self, slat, return_subs=False):
        coords = _closed_cube_coords()
        index = {tuple(c.tolist()[1:]): i for i, c in enumerate(coords)}
        intersected = torch.zeros((coords.shape[0], 3), dtype=torch.bool)
        for base, axis in [((0, 0, 0), 0), ((1, 0, 0), 0), ((0, 0, 0), 1), ((0, 1, 0), 1), ((0, 0, 0), 2), ((0, 0, 1), 2)]:
            intersected[index[base], axis] = True
        state = SparseTensor(feats=torch.zeros((coords.shape[0], 3)), coords=coords)
        return FdgDecoderOutput(
            coords=coords, vertices=state, intersected=state.replace(intersected),
            quad_lerp=state.replace(torch.ones((coords.shape[0], 1))), subs=["s0"] if return_subs else None,
        )


class _FakeTexDecoder(_Placed):
    def __call__(self, slat, guide_subs=None):
        coords = _closed_cube_coords()
        return SparseTensor(feats=torch.full((coords.shape[0], 6), 0.5), coords=coords)


def _components(**overrides):
    parts = dict(
        conditioner=_FakeConditioner(),
        naf=_FakeNAF(),
        ss_flow=_ss_flow(),
        ss_vae=_FakeSSVAE(),
        shape_flow_lr=_slat_flow(32, 2),
        shape_flow_hr=_slat_flow(32, 3),
        shape_decoder=_FakeShapeDecoder(),
        tex_flow=_slat_flow(64, 4),
        tex_decoder=_FakeTexDecoder(),
    )
    parts.update(overrides)
    return p3d.Pixal3DComponents(**parts)


def _image(color=(200, 100, 50)):
    return Image.new("RGB", (64, 64), color)


def _run(components, source=None, **kwargs):
    kwargs.setdefault("tier", "1024")
    return p3d.run_pixal3d(
        components, source if source is not None else _image(), seed=7, device="cpu",
        stage_settings=FAST_STAGES, **kwargs,
    )


def test_the_whole_cascade_runs_on_real_projection_flows():
    components = _components()
    volume = _run(components)
    assert isinstance(volume, MeshVolume)
    assert volume.resolution == 1024
    assert volume.faces.shape == (12, 3)


def test_the_volume_carries_the_conditioned_front_view_and_texture_latent_for_profiling_dumps():
    components = _components()
    front = _image()
    volume = _run(components, p3d.Pixal3DViews({"front": front, "left": _image((1, 2, 3))}))
    assert volume.cond_image is front
    assert volume.tex_slat is not None
    assert torch.equal(volume.tex_slat.coords, components.tex_flow.conds[-1][0].coords)


def test_there_is_no_512_tier():
    with pytest.raises(ValueError, match="tier"):
        _run(_components(), tier="512")


def test_the_upsampler_is_required():
    with pytest.raises(ValueError, match="NAF"):
        _run(_components(naf=None))


def test_the_high_resolution_shape_flow_is_required():
    with pytest.raises(ValueError, match="high-resolution"):
        _run(_components(shape_flow_hr=None))


def test_the_encoder_runs_once_per_conditioning_size():
    components = _components()
    _run(components)
    assert components.conditioner.calls == [(1, 512), (1, 1024)]


def test_naf_runs_per_stage_at_upstreams_sizes():
    components = _components()
    _run(components)
    assert components.naf.calls == [(512, 32, 512), (1024, 64, 512), (1024, 64, 1024)]


def test_the_dense_stage_is_conditioned_on_global_tokens_and_the_whole_projected_grid():
    components = _components()
    _run(components)
    _, cond = components.ss_flow.conds[0]
    assert isinstance(cond, ProjectedCondition)
    assert cond.tokens.shape == (1, 5, DINO)
    assert torch.allclose(cond.tokens, torch.full((1, 5, DINO), 0.512))
    assert cond.proj.shape == (1, 4 ** 3, DINO)


def test_every_sparse_stage_carries_its_features_on_the_noises_own_coordinates():
    components = _components()
    _run(components)
    for flow in (components.shape_flow_lr, components.shape_flow_hr, components.tex_flow):
        for x, cond in flow.conds:
            assert isinstance(cond.proj, SparseTensor)
            assert torch.equal(cond.proj.coords, x.coords)
            assert cond.proj.feats.shape == (x.coords.shape[0], 2 * DINO)


def test_the_unconditional_branch_zeroes_tokens_and_features():
    components = _components()
    _run(components)
    conds = components.shape_flow_lr.conds
    assert len(conds) == 4
    assert torch.count_nonzero(conds[1][1].proj.feats) == 0
    assert torch.count_nonzero(conds[1][1].tokens) == 0
    assert torch.count_nonzero(conds[0][1].proj.feats) > 0


def test_the_high_resolution_stages_project_onto_the_tiers_own_grid():
    components = _components()
    _run(components, tier="1536")
    hr_coords = components.shape_flow_hr.conds[0][0].coords
    assert int(hr_coords[:, 1:].max()) <= 1536 // 16 - 1
    assert components.shape_decoder.resolutions == [1536]


def test_requantisation_rounds_onto_the_linspace_grid():
    coords = torch.tensor([[0, 0, 255, 511], [0, 7, 260, 500]], dtype=torch.int32)
    quantised = p3d.quantize_to_projection_grid(coords, 512, 1024)
    expected = torch.round((coords[:, 1:].double() + 0.5) / 512 * 63).int()
    assert torch.equal(quantised[:, 1:], expected.unique(dim=0))
    assert int(quantised[:, 1:].max()) == 63
    floor = trellis2_itm.quantize_to_grid(coords, 512, 1024)
    assert not torch.equal(floor, quantised)


def test_the_cascade_degrades_by_128_down_to_1024_for_the_token_budget():
    axes = torch.meshgrid(*[torch.arange(16) * 32] * 3, indexing="ij")
    coords = torch.stack([torch.zeros(4096, dtype=torch.long), *[axis.reshape(-1) for axis in axes]], dim=1).int()
    quantised, resolution = p3d.resolve_projection_grid(coords, 1536, 10)
    assert resolution == 1024
    _, kept = p3d.resolve_projection_grid(coords, 1536, 10 ** 6)
    assert kept == 1536


def test_single_view_background_removal_pads_the_crop_by_ten_percent():
    image = Image.new("RGBA", (200, 200), (255, 0, 0, 0))
    image.paste((255, 0, 0, 255), (50, 50, 151, 151))
    [view] = p3d.prepare_views(image, math.radians(40.0), remove_background=True)
    assert view.image.size == (110, 110)
    assert trellis2_itm.prepare_image(image).size == (100, 100)
    assert torch.allclose(view.camera, front_camera(distance_from_fov(math.radians(40.0))))


def test_single_view_without_background_removal_uses_the_image_as_given():
    image = _image()
    [view] = p3d.prepare_views(image, math.radians(40.0))
    assert view.image is image


def test_views_are_matted_and_premultiplied_but_never_cropped():
    front = Image.new("RGBA", (80, 60), (200, 200, 200, 0))
    front.paste((200, 200, 200, 255), (30, 20, 40, 30))
    views = p3d.prepare_views(
        p3d.Pixal3DViews({"front": front, "back": front}), math.radians(20.0), remove_background=True
    )
    assert [view.image.size for view in views] == [(80, 60), (80, 60)]
    assert views[0].image.getpixel((0, 0)) == (0, 0, 0)
    assert views[0].image.getpixel((35, 25)) == (200, 200, 200)


def test_an_opaque_view_needs_a_matting_model_for_background_removal():
    with pytest.raises(ValueError, match="matting model"):
        p3d.prepare_views(p3d.Pixal3DViews({"front": _image()}), math.radians(20.0), remove_background=True)


def test_the_view_rig_is_the_padded_orbit_at_the_given_fov():
    fov = math.radians(20.0)
    views = p3d.prepare_views(p3d.Pixal3DViews({"right": _image(), "front": _image(), "left": _image()}), fov)
    distance = 1.1 * 0.5 / math.tan(fov / 2)
    assert distance == pytest.approx(3.1192049980163574, abs=1e-6)
    expected = [orbit_camera(a, 0.0, distance) for a in (0.0, 90.0, 270.0)]
    for view, camera in zip(views, expected):
        assert torch.allclose(view.camera, camera, atol=1e-5)


def test_views_are_ordered_front_left_back_right_and_need_a_front():
    views = p3d.Pixal3DViews({"back": 1, "front": 2, "right": 3})
    assert views.names == ["front", "back", "right"]
    with pytest.raises(ValueError, match="front"):
        p3d.Pixal3DViews({"left": 1})
    with pytest.raises(ValueError, match="unknown view"):
        p3d.Pixal3DViews({"front": 1, "top": 2})


def test_a_multi_view_run_encodes_every_view_and_averages_them_into_one_condition():
    components = _components()
    _run(components, p3d.Pixal3DViews({"front": _image(), "left": _image(), "back": _image()}))
    assert components.conditioner.calls == [(3, 512), (3, 1024)]
    assert len(components.naf.calls) == 9
    _, cond = components.ss_flow.conds[0]
    assert cond.tokens.shape == (1, 5, DINO)


def test_every_model_returns_to_the_cpu_after_its_stage():
    components = _components()
    _run(components)
    for name in ("conditioner", "naf", "ss_flow", "ss_vae", "shape_flow_lr", "shape_flow_hr",
                 "shape_decoder", "tex_flow", "tex_decoder"):
        placements = getattr(components, name).placements
        assert placements, name
        assert placements[-1] == "cpu", name


def test_the_matting_model_is_placed_for_background_removal():
    class _Matting(_Placed):
        def __call__(self, image):
            matted = Image.new("RGBA", image.size, (9, 9, 9, 0))
            matted.paste((9, 9, 9, 255), (8, 8, image.width - 8, image.height - 8))
            return matted

    components = _components(matting=_Matting())
    _run(components, remove_background=True)
    assert components.matting.placements == ["cpu", "cpu"]


def test_the_same_seed_reproduces_the_same_latents():
    first = _components()
    _run(first)
    torch.randn(100)
    second = _components()
    _run(second)
    assert torch.equal(first.tex_flow.conds[-1][0].feats, second.tex_flow.conds[-1][0].feats)


def test_the_fov_changes_where_voxels_sample():
    narrow, wide = _components(), _components()
    _run(narrow, fov_deg=12.0)
    _run(wide, fov_deg=49.13)
    assert not torch.allclose(narrow.ss_flow.conds[0][1].proj, wide.ss_flow.conds[0][1].proj)


def test_an_impossible_fov_is_refused():
    with pytest.raises(ValueError, match="field of view"):
        _run(_components(), fov_deg=180.0)


def test_cancellation_stops_the_cascade():
    components = _components()
    with pytest.raises(SamplingCancelled):
        _run(components, is_cancelled=lambda: len(components.shape_flow_lr.conds) >= 1)
    assert components.shape_flow_hr.conds == []
    assert components.tex_flow.conds == []


def _volume(resolution=8):
    generator = torch.Generator().manual_seed(0)
    coords = torch.randint(0, resolution, (20, 3), generator=generator)
    return MeshVolume(
        vertices=(coords.float() + 0.5) / resolution - 0.5,
        faces=torch.tensor([[0, 1, 2]]),
        attrs=torch.rand(20, 6, generator=generator),
        coords=coords,
        resolution=resolution,
    )


def _exported(vertices):
    return torch.stack([vertices[:, 0], vertices[:, 2], -vertices[:, 1]], dim=-1)


@pytest.mark.parametrize("frame,expected", [("upstream", lambda v: torch.stack([-v[:, 0], v[:, 1], -v[:, 2]], -1)),
                                            ("camera", lambda v: v)])
def test_reframing_lands_on_the_target_frame_after_the_trellis2_export_swap(frame, expected):
    volume = _volume()
    reframed = p3d.reframe_volume(volume, frame)
    assert torch.allclose(_exported(reframed.vertices), expected(volume.vertices))


@pytest.mark.parametrize("frame", ["upstream", "camera"])
def test_reframed_voxels_still_sit_under_the_reframed_vertices(frame):
    volume = _volume()
    reframed = p3d.reframe_volume(volume, frame)
    centres = (reframed.coords.float() + 0.5) / reframed.resolution - 0.5
    assert torch.allclose(centres, reframed.vertices, atol=1e-6)
    assert reframed.coords.min() >= 0 and reframed.coords.max() < reframed.resolution
    assert torch.equal(reframed.attrs, volume.attrs)


@pytest.mark.parametrize("frame", ["upstream", "camera"])
def test_reframing_is_a_proper_rotation_so_winding_survives(frame):
    volume = _volume()
    basis = torch.eye(3)
    rotated = p3d.reframe_volume(
        MeshVolume(vertices=basis, faces=volume.faces, attrs=volume.attrs[:3], coords=volume.coords[:3], resolution=8),
        frame,
    ).vertices
    assert torch.det(rotated) == pytest.approx(1.0)


def test_reframing_keeps_the_profiling_payload():
    volume = _volume()
    volume.cond_image = _image()
    volume.tex_slat = object()
    reframed = p3d.reframe_volume(volume, "upstream")
    assert reframed.cond_image is volume.cond_image
    assert reframed.tex_slat is volume.tex_slat


def test_an_unknown_export_frame_is_refused():
    with pytest.raises(ValueError, match="export frame"):
        p3d.reframe_volume(_volume(), "z_up")
