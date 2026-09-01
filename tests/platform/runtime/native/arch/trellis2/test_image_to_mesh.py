"""Tests for the TRELLIS.2 image-to-mesh orchestration (``image_to_mesh.py``).

The cascade math (occupancy pooling, requantisation, the token-budget degrade)
is tested as pure functions. The run itself is exercised against staged fakes
rather than real weights: every fake has the signature the real model satisfies,
so the seams — which conditioning size reaches which stage, that the shape
latent is denormalised for the decoder but renormalised for the texture flow,
that the texture decoder is guided by the shape decoder's own subdivisions, and
that each model leaves the compute device before the next one arrives — are the
real ones.
"""

from __future__ import annotations

import pytest
import torch
from PIL import Image

from src.platform.runtime.native.arch.trellis2 import image_to_mesh as itm
from src.platform.runtime.native.arch.trellis2.config import (
    SHAPE_SLAT_NORMALIZATION,
    SSFlowConfig,
    StageSampling,
    TEX_SLAT_NORMALIZATION,
)
from src.platform.runtime.native.arch.trellis2.octree_vae import FdgDecoderOutput
from src.platform.runtime.native.sparse3d import SparseTensor

LATENT_CHANNELS = 32
FAST = StageSampling(
    steps=2, guidance_strength=1.0, guidance_rescale=0.0,
    guidance_interval=(0.0, 1.0), rescale_t=1.0,
)
FAST_STAGES = {"sparse_structure": FAST, "shape": FAST, "texture": FAST}


# -- cascade math -----------------------------------------------------------


def test_occupancy_keeps_the_decoders_own_grid_when_it_already_matches():
    occupancy = torch.full((1, 1, 64, 64, 64), -1.0)
    occupancy[0, 0, 3, 5, 7] = 1.0
    assert itm.occupancy_to_coords(occupancy, 64).tolist() == [[0, 3, 5, 7]]


def test_occupancy_pools_down_and_a_single_child_keeps_its_parent():
    occupancy = torch.full((1, 1, 64, 64, 64), -1.0)
    occupancy[0, 0, 3, 5, 7] = 1.0
    assert itm.occupancy_to_coords(occupancy, 32).tolist() == [[0, 1, 2, 3]]


def test_occupancy_pooling_refuses_a_non_integer_ratio():
    occupancy = torch.full((1, 1, 64, 64, 64), -1.0)
    with pytest.raises(ValueError, match="not an integer ratio"):
        itm.occupancy_to_coords(occupancy, 48)


def test_quantise_maps_cell_centres_onto_the_target_grid():
    coords = torch.tensor([[0, 0, 0, 0], [0, 511, 511, 511]], dtype=torch.int32)
    quantised = itm.quantize_to_grid(coords, 512, 1024)
    # (0 + 0.5)/512 * 64 -> 0; (511 + 0.5)/512 * 64 -> 63.
    assert quantised.tolist() == [[0, 0, 0, 0], [0, 63, 63, 63]]


def test_quantise_deduplicates_voxels_that_collapse_together():
    coords = torch.tensor(
        [[0, 0, 0, 0], [0, 1, 0, 0], [0, 2, 0, 0], [0, 3, 0, 0]], dtype=torch.int32
    )
    # A 512-space cell maps to a 64-cell grid, so eight source voxels per target.
    assert itm.quantize_to_grid(coords, 512, 1024).shape[0] == 1


def test_cascade_keeps_the_requested_resolution_when_it_fits_the_budget():
    coords = torch.stack([
        torch.zeros(64, dtype=torch.int32),
        torch.arange(64, dtype=torch.int32) * 8,
        torch.zeros(64, dtype=torch.int32),
        torch.zeros(64, dtype=torch.int32),
    ], dim=1)
    quantised, resolution = itm.resolve_cascade_grid(coords, 1536, max_num_tokens=49152)
    assert resolution == 1536
    assert quantised.shape[0] < 49152


def test_cascade_steps_down_by_128_until_the_token_budget_is_met():
    """A shape too detailed for its tier is decoded coarser rather than refused.

    The budget is calibrated off the fixture's own token counts so the expected
    stopping point is exact rather than statistical: a budget one token above
    what 1408 costs must be met at 1408 and not before.
    """
    dense = _scattered_coords(60000, seed=0)
    at_1536 = itm.quantize_to_grid(dense, 512, 1536).shape[0]
    at_1408 = itm.quantize_to_grid(dense, 512, 1408).shape[0]
    assert at_1408 < at_1536, "fixture must actually shed tokens as it coarsens"

    quantised, resolution = itm.resolve_cascade_grid(
        dense, 1536, max_num_tokens=at_1408 + 1
    )
    assert resolution == 1408
    assert quantised.shape[0] == at_1408


def test_cascade_stops_degrading_at_1024_even_over_budget():
    """1024 is the high-resolution weights' own tier — there is nothing coarser
    to fall back to, so the budget is exceeded rather than the run failing."""
    dense = _scattered_coords(200000, seed=1)
    quantised, resolution = itm.resolve_cascade_grid(dense, 1536, max_num_tokens=8)
    assert resolution == 1024
    assert quantised.shape[0] > 8


def _scattered_coords(count, seed):
    """``[N, 4]`` voxel coordinates scattered through the LR pass's 512 grid."""
    torch.manual_seed(seed)
    return torch.stack(
        [torch.zeros(count, dtype=torch.int32)]
        + [torch.randint(0, 512, (count,), dtype=torch.int32) for _ in range(3)],
        dim=1,
    ).unique(dim=0)


def test_normalisation_round_trips():
    state = _sparse(rows=6, channels=LATENT_CHANNELS)
    restored = itm.denormalize_slat(
        itm.normalize_slat(state, SHAPE_SLAT_NORMALIZATION), SHAPE_SLAT_NORMALIZATION
    )
    assert torch.allclose(restored.feats, state.feats, atol=1e-5)


def test_normalisation_applies_the_published_per_channel_statistics():
    state = _sparse(rows=3, channels=LATENT_CHANNELS)
    normalized = itm.normalize_slat(state, TEX_SLAT_NORMALIZATION)
    mean = torch.tensor(TEX_SLAT_NORMALIZATION.mean)
    std = torch.tensor(TEX_SLAT_NORMALIZATION.std)
    assert torch.allclose(normalized.feats, (state.feats - mean) / std, atol=1e-5)


# -- image preparation ------------------------------------------------------


def test_prepare_crops_to_the_subject_and_premultiplies():
    image = Image.new("RGBA", (200, 100), (255, 0, 0, 0))
    for x in range(40, 60):
        for y in range(30, 50):
            image.putpixel((x, y), (255, 0, 0, 255))

    prepared = itm.prepare_image(image)
    assert prepared.mode == "RGB"
    assert prepared.width == prepared.height
    assert prepared.width < 100


def test_prepare_downscales_an_oversized_image():
    image = Image.new("RGBA", (4000, 2000), (0, 255, 0, 0))
    image.paste((0, 255, 0, 255), (1000, 500, 3000, 1500))

    prepared = itm.prepare_image(image)
    # The long edge is capped at 1024 first, so the 2000px-wide subject is
    # cropped out of a 1024x512 image and comes back about 512 square. Without
    # the cap the crop would be the subject's original 2000px.
    assert prepared.width == pytest.approx(512, abs=4)


def test_prepare_uses_the_alpha_channel_and_never_the_matting_model():
    image = Image.new("RGBA", (64, 64), (10, 20, 30, 0))
    for x in range(20, 40):
        for y in range(20, 40):
            image.putpixel((x, y), (10, 20, 30, 255))

    calls = []
    itm.prepare_image(image, matting=lambda img: calls.append(img))
    assert calls == []


def test_prepare_mattes_an_opaque_image():
    opaque = Image.new("RGB", (64, 64), (10, 20, 30))
    matted = Image.new("RGBA", (64, 64), (10, 20, 30, 0))
    for x in range(20, 40):
        for y in range(20, 40):
            matted.putpixel((x, y), (10, 20, 30, 255))

    seen = []

    def matting(image):
        seen.append(image)
        return matted

    itm.prepare_image(opaque, matting=matting)
    assert len(seen) == 1


def test_prepare_names_the_missing_matting_model_on_an_opaque_image():
    with pytest.raises(ValueError, match="needs a matting model"):
        itm.prepare_image(Image.new("RGB", (32, 32), (1, 2, 3)))


def test_prepare_reports_an_image_matted_down_to_nothing():
    empty = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    with pytest.raises(ValueError, match="no subject"):
        itm.prepare_image(empty, matting=lambda image: empty)


# -- fakes ------------------------------------------------------------------


def _sparse(rows=8, channels=LATENT_CHANNELS, batch=1):
    coords = torch.stack([
        torch.zeros(rows, dtype=torch.int32),
        torch.arange(rows, dtype=torch.int32),
        torch.zeros(rows, dtype=torch.int32),
        torch.zeros(rows, dtype=torch.int32),
    ], dim=1)
    return SparseTensor(feats=torch.randn(rows, channels), coords=coords)


class _Placed:
    """Records every device this component was moved to, in order."""

    def __init__(self) -> None:
        self.placements: list[str] = []

    def to(self, device):
        self.placements.append(str(device))
        return self


class _FakeConditioner(_Placed):
    def __init__(self) -> None:
        super().__init__()
        self.sizes: list[int] = []

    def encode(self, image, size):
        self.sizes.append(size)
        return torch.full((1, 4, 8), float(size) / 1000.0)

    @staticmethod
    def negative(cond):
        return torch.zeros_like(cond)


class _FakeSSFlow(_Placed):
    def __init__(self) -> None:
        super().__init__()
        self.config = SSFlowConfig(
            resolution=4, in_channels=8, model_channels=16, cond_channels=8,
            out_channels=8, num_blocks=1, num_heads=2,
        )
        self.conds: list[float] = []

    def __call__(self, x, t, cond, **kwargs):
        self.conds.append(float(cond.reshape(-1)[0]))
        return torch.zeros_like(x)


class _FakeSSVAE(_Placed):
    """Occupancy the sparse-structure stage decodes to: a 2x2x2 block, which
    survives the pool to a 32-grid as a single voxel."""

    def __call__(self, latent):
        occupancy = torch.full((1, 1, 64, 64, 64), -1.0)
        occupancy[0, 0, :2, :2, :2] = 1.0
        return occupancy


class _FakeSLatFlow(_Placed):
    def __init__(self, in_channels=LATENT_CHANNELS) -> None:
        super().__init__()
        self.in_channels = in_channels
        self.conds: list[float] = []
        self.concat_conds: list = []

    def __call__(self, x, t, cond, concat_cond=None, **kwargs):
        self.conds.append(float(cond.reshape(-1)[0]))
        if concat_cond is not None:
            self.concat_conds.append(concat_cond)
        return x.replace(x.feats[:, :LATENT_CHANNELS] * 0.0 + 1.0)


def _closed_cube_coords():
    return torch.tensor(
        [[0, x, y, z] for x in range(2) for y in range(2) for z in range(2)],
        dtype=torch.int32,
    )


class _FakeShapeDecoder(_Placed):
    """Emits the dual-grid description of a closed unit cube (the 2x2x2 recipe
    from ``test_dual_grid``), plus one subdivision entry per octree level."""

    def __init__(self) -> None:
        super().__init__()
        self.resolutions: list[int] = []
        self.upsample_calls: list[int] = []
        self.decoded: list = []

    def set_resolution(self, resolution):
        self.resolutions.append(resolution)

    def upsample(self, slat, upsample_times):
        self.upsample_calls.append(upsample_times)
        rows = slat.feats.shape[0] * 4
        return torch.stack([
            torch.zeros(rows, dtype=torch.int32),
            torch.arange(rows, dtype=torch.int32) % 512,
            torch.zeros(rows, dtype=torch.int32),
            torch.zeros(rows, dtype=torch.int32),
        ], dim=1)

    def __call__(self, slat, return_subs=False):
        self.decoded.append(slat)
        coords = _closed_cube_coords()
        index = {tuple(c.tolist()[1:]): i for i, c in enumerate(coords)}
        count = coords.shape[0]
        intersected = torch.zeros((count, 3), dtype=torch.bool)
        for base, axis in [
            ((0, 0, 0), 0), ((1, 0, 0), 0),
            ((0, 0, 0), 1), ((0, 1, 0), 1),
            ((0, 0, 0), 2), ((0, 0, 1), 2),
        ]:
            intersected[index[base], axis] = True

        state = SparseTensor(feats=torch.zeros((count, 3)), coords=coords)
        return FdgDecoderOutput(
            coords=coords,
            vertices=state,
            intersected=state.replace(intersected),
            quad_lerp=state.replace(torch.ones((count, 1))),
            subs=["level-0", "level-1"] if return_subs else None,
        )


class _FakeTexDecoder(_Placed):
    def __init__(self) -> None:
        super().__init__()
        self.guides: list = []

    def __call__(self, slat, guide_subs=None):
        self.guides.append(guide_subs)
        coords = _closed_cube_coords()
        return SparseTensor(feats=torch.full((coords.shape[0], 6), 0.5), coords=coords)


def _components(**overrides):
    parts = dict(
        conditioner=_FakeConditioner(),
        ss_flow=_FakeSSFlow(),
        ss_vae=_FakeSSVAE(),
        shape_flow_lr=_FakeSLatFlow(),
        shape_flow_hr=_FakeSLatFlow(),
        shape_decoder=_FakeShapeDecoder(),
        tex_flow=_FakeSLatFlow(in_channels=LATENT_CHANNELS * 2),
        tex_decoder=_FakeTexDecoder(),
    )
    parts.update(overrides)
    return itm.Trellis2Components(**parts)


def _run(components, tier="1024", **kwargs):
    return itm.run_image_to_mesh(
        components, Image.new("RGB", (64, 64), (200, 100, 50)),
        tier=tier, seed=7, device="cpu", stage_settings=FAST_STAGES, **kwargs,
    )


# -- the run ----------------------------------------------------------------


def test_unknown_tier_is_refused():
    with pytest.raises(ValueError, match="unknown resolution tier"):
        _run(_components(), tier="2048")


def test_a_cascade_tier_needs_high_resolution_shape_weights():
    with pytest.raises(ValueError, match="cascade"):
        _run(_components(shape_flow_hr=None), tier="1024")


def test_the_512_tier_runs_one_shape_pass_and_never_conditions_at_1024():
    components = _components(shape_flow_hr=None)
    volume = _run(components, tier="512")

    assert components.conditioner.sizes == [512]
    assert components.shape_decoder.upsample_calls == []
    assert volume.resolution == 512
    assert volume.voxel_size == pytest.approx(1 / 512)


def test_a_cascade_conditions_the_low_pass_at_512_and_the_high_pass_at_1024():
    components = _components()
    _run(components, tier="1024")

    assert components.conditioner.sizes == [512, 1024]
    assert components.ss_flow.conds == [pytest.approx(0.512)] * 2
    assert components.shape_flow_lr.conds == [pytest.approx(0.512)] * 2
    assert components.shape_flow_hr.conds == [pytest.approx(1.024)] * 2
    assert components.tex_flow.conds == [pytest.approx(1.024)] * 2


def test_a_cascade_upsamples_the_low_resolution_latent_by_four_levels():
    components = _components()
    _run(components, tier="1024")
    assert components.shape_decoder.upsample_calls == [4]


def test_the_texture_flow_is_conditioned_on_the_normalised_shape_latent():
    """The shape decoder needs the latent denormalised and the texture flow
    needs it normalised, so the run cannot hand the same tensor to both."""
    components = _components()
    _run(components, tier="1024")

    decoded = components.shape_decoder.decoded[0]
    concat = components.tex_flow.concat_conds[0]

    renormalised = itm.normalize_slat(decoded, SHAPE_SLAT_NORMALIZATION)
    assert torch.allclose(concat.feats, renormalised.feats, atol=1e-4)
    assert not torch.allclose(concat.feats, decoded.feats, atol=1e-2)


def test_the_texture_decoder_is_guided_by_the_shape_decoders_subdivisions():
    components = _components()
    _run(components, tier="1024")
    assert components.tex_decoder.guides == [["level-0", "level-1"]]


def test_the_shape_decoder_is_set_to_the_resolution_actually_reached():
    components = _components()
    volume = _run(components, tier="1024")
    assert components.shape_decoder.resolutions == [volume.resolution]


def test_the_run_returns_the_cube_its_decoder_described():
    components = _components()
    volume = _run(components, tier="1024")

    assert volume.vertices.shape == (8, 3)
    assert volume.faces.shape == (12, 3)
    assert volume.attrs.shape == (8, 6)
    assert volume.coords.shape == (8, 3)


def test_texture_attributes_are_mapped_out_of_the_decoders_signed_range():
    """The texture decoder emits in [-1, 1] and the volume is consumed in
    [0, 1], so a decoder output of 0.5 must arrive as 0.75."""
    components = _components()
    volume = _run(components, tier="1024")
    assert torch.allclose(volume.attrs, torch.full_like(volume.attrs, 0.75))


def test_every_model_returns_to_the_cpu_after_its_stage():
    components = _components()
    _run(components, tier="1024")

    for name in ("conditioner", "ss_flow", "ss_vae", "shape_flow_lr",
                 "shape_flow_hr", "shape_decoder", "tex_flow", "tex_decoder"):
        placements = getattr(components, name).placements
        assert placements, f"{name} was never placed"
        assert placements[-1] == "cpu", f"{name} was left on {placements[-1]}"


def test_an_empty_sparse_structure_is_reported_rather_than_decoded():
    class _Empty(_Placed):
        def __call__(self, latent):
            return torch.full((1, 1, 64, 64, 64), -1.0)

    with pytest.raises(ValueError, match="empty volume"):
        _run(_components(ss_vae=_Empty()), tier="1024")


def test_the_same_seed_reproduces_the_same_run():
    """Sampling draws from a seeded generator, not the global RNG, so two runs
    with one seed agree even with unrelated draws in between."""
    first = _run(_components(), tier="512")
    torch.randn(1000)
    second = _run(_components(), tier="512")
    assert torch.allclose(first.vertices, second.vertices)


class _FakeMatting(_Placed):
    """A matting model that mattes the middle of whatever it is handed."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = []

    def __call__(self, image):
        self.calls.append(image)
        matted = Image.new("RGBA", image.size, (5, 5, 5, 0))
        matted.paste((5, 5, 5, 255), (8, 8, image.width - 8, image.height - 8))
        return matted


def test_background_removal_is_off_unless_asked_for():
    components = _components()
    components.matting = _FakeMatting()
    _run(components, tier="512", remove_background=False)
    assert components.matting.calls == []


def test_background_removal_runs_the_matting_model():
    components = _components()
    components.matting = _FakeMatting()
    _run(components, tier="512", remove_background=True)
    assert len(components.matting.calls) == 1


def test_the_matting_model_is_placed_and_returned_like_every_other_stage():
    """BiRefNet is unusable on CPU and large enough to matter on the GPU, so it
    must be moved for the matte and moved back before the conditioner loads."""
    components = _components()
    components.matting = _FakeMatting()
    _run(components, tier="512", remove_background=True)
    assert components.matting.placements == ["cpu", "cpu"]
