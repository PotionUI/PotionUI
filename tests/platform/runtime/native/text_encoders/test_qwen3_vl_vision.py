"""Qwen3-VL vision-tower tests (CPU only, synthetic).

Covers image preprocessing geometry including the `grounding_px`
longest-side cap, the learned bilinear position-embedding interpolation, the
tiny synthetic tower's forward shape/finiteness + DeepStack tap count, and the
interleaved m-RoPE frequency combination against a hand-computed small case.
No GPU, no real-weight forward (maintainer validation required).
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from vendor.gpl.comfyui.ops import disable_weight_init as ops  # noqa: E402

from src.platform.runtime.native.text_encoders.qwen3_vl_vision import (  # noqa: E402
    IMAGE_PAD_TOKEN,
    VISION_END_TOKEN,
    VISION_START_TOKEN,
    Qwen3VLVisionTower,
    preprocess_qwen3_vl_image,
    vision_bilinear_pos_embed_indices_weights,
    vision_rope_position_ids,
)
from src.platform.runtime.native.text_encoders.qwen3 import _interleave_mrope_freqs  # noqa: E402


# --- preprocessing geometry --------------------------------------------------


def test_preprocess_exact_multiple_of_factor_is_unchanged():
    # factor = patch_size(16) * merge_size(2) = 32; 256 = 32*8 needs no resize.
    img = torch.rand(256, 256, 3)
    patches, grid_thw = preprocess_qwen3_vl_image(img, grounding_px=0)
    assert grid_thw.tolist() == [[1, 16, 16]]  # 256 / patch_size(16) = 16
    assert patches.shape == (16 * 16, 3 * 2 * 16 * 16)
    assert torch.isfinite(patches).all()


def test_preprocess_grounding_px_zero_disables_the_cap():
    img = torch.rand(2048, 1024, 3)
    _patches, grid_thw = preprocess_qwen3_vl_image(img, grounding_px=0, max_pixels=10_000_000)
    h_bar, w_bar = grid_thw[0, 1].item() * 16, grid_thw[0, 2].item() * 16
    assert max(h_bar, w_bar) > 768  # nowhere near capped


def test_preprocess_grounding_px_caps_the_longest_side():
    # 2000x1000 (long side 2000) capped to 768: scale = 768/2000 = 0.384 ->
    # (768, 384) before the factor-32 snap.
    img = torch.rand(1000, 2000, 3)
    _patches, grid_thw = preprocess_qwen3_vl_image(img, grounding_px=768, max_pixels=10_000_000)
    h_bar, w_bar = grid_thw[0, 1].item() * 16, grid_thw[0, 2].item() * 16
    # Snapped to the nearest 32px multiple of the capped (768, 384) target —
    # within one factor step of the requested cap, never anywhere near the
    # native 2000px long side.
    assert abs(max(h_bar, w_bar) - 768) <= 32
    assert max(h_bar, w_bar) < 900


def test_preprocess_grounding_px_noop_when_already_smaller():
    img = torch.rand(256, 256, 3)
    capped, grid_capped = preprocess_qwen3_vl_image(img, grounding_px=768)
    uncapped, grid_uncapped = preprocess_qwen3_vl_image(img, grounding_px=0)
    assert grid_capped.tolist() == grid_uncapped.tolist()
    assert torch.equal(capped, uncapped)


def test_preprocess_rejects_wrong_shape():
    with pytest.raises(ValueError):
        preprocess_qwen3_vl_image(torch.rand(4, 4))


def test_preprocess_uses_plain_center_normalization_not_clip_stats():
    # A uniform mid-gray image normalizes to ~0 under (x-0.5)/0.5 — a
    # CLIP-stat normalization (mean 0.48145466) would NOT land at exactly 0.
    img = torch.full((32, 32, 3), 0.5)
    patches, _grid = preprocess_qwen3_vl_image(img, grounding_px=0)
    assert torch.allclose(patches, torch.zeros_like(patches), atol=1e-5)


# --- vision-tower position ids / bilinear pos-embed interpolation -----------


def test_vision_rope_position_ids_hand_computed_2x2_grid():
    # 2x2 patch grid, merge_size=2 -> one merge unit covering the whole grid.
    grid = torch.tensor([[1, 2, 2]])
    pos = vision_rope_position_ids(grid, merge_size=2, device="cpu")
    assert pos.shape == (4, 2)
    # merge-adjacent order: (0,0),(0,1),(1,0),(1,1) — reshape/transpose over the
    # single 2x2 block reproduces the raw row-major meshgrid order here.
    assert pos.tolist() == [[0, 0], [0, 1], [1, 0], [1, 1]]


def test_bilinear_indices_exact_grid_points_have_unit_weight_on_floor_corner():
    # num_grid_per_side == h == w: linspace(0, side-1, h) lands on exact
    # integers, so every corner weight collapses to (1,0,0,0) at the
    # floor-floor corner (no genuine interpolation needed).
    grid = torch.tensor([[1, 4, 4]])
    idx, weight = vision_bilinear_pos_embed_indices_weights(grid, num_grid_per_side=4, merge_size=2, device="cpu")
    assert idx.shape == (4, 16)
    assert weight.shape == (4, 16)
    assert torch.allclose(weight[0], torch.ones(16))
    assert torch.allclose(weight[1:], torch.zeros(3, 16))


# --- tiny synthetic tower: forward shape/finiteness + DeepStack -------------

_TINY = dict(
    hidden_size=8, out_hidden_size=12, intermediate_size=16, num_heads=2, num_layers=3,
    patch_size=2, temporal_patch_size=2, spatial_merge_size=2,
    num_position_embeddings=16,  # 4x4 grid, matching the test's 4x4 patch grid
    deepstack_indexes=(1,),
)


def _build_tiny_tower() -> Qwen3VLVisionTower:
    tower = Qwen3VLVisionTower(operations=ops, **_TINY)
    for p in tower.parameters():
        torch.nn.init.normal_(p, std=0.02)
    return tower


def test_tiny_tower_forward_shape_and_finite():
    tower = _build_tiny_tower()
    grid_thw = torch.tensor([[1, 4, 4]])  # 4x4 patch grid -> post-merge 2x2 = 4 tokens
    num_patches = 4 * 4
    patch_dim = 3 * _TINY["temporal_patch_size"] * _TINY["patch_size"] * _TINY["patch_size"]
    pixel_values = torch.randn(num_patches, patch_dim)

    merged, deepstack = tower(pixel_values, grid_thw)
    assert merged.shape == (4, _TINY["out_hidden_size"])
    assert torch.isfinite(merged).all()
    assert len(deepstack) == 1  # one deepstack_indexes entry
    assert deepstack[0].shape == (4, _TINY["out_hidden_size"])
    assert torch.isfinite(deepstack[0]).all()


def test_tiny_tower_no_deepstack_indexes_returns_empty_list():
    tower = Qwen3VLVisionTower(operations=ops, **{**_TINY, "deepstack_indexes": ()})
    for p in tower.parameters():
        torch.nn.init.normal_(p, std=0.02)
    grid_thw = torch.tensor([[1, 4, 4]])
    pixel_values = torch.randn(16, 3 * 2 * 2 * 2)
    merged, deepstack = tower(pixel_values, grid_thw)
    assert merged.shape == (4, _TINY["out_hidden_size"])
    assert deepstack == []


def test_special_tokens_match_qwen2_vocab_constants():
    assert IMAGE_PAD_TOKEN == 151655
    assert VISION_START_TOKEN == 151652
    assert VISION_END_TOKEN == 151653


# --- interleaved m-RoPE frequency combination -------------------------------


def test_interleave_mrope_matches_hand_derived_layout():
    # head_dim//2 = 6, mrope_section=(1,2,3) so T occupies indices {0,3,4,5}
    # (mod-3==0 plus everything past H/W's stride-3 span), H occupies {1},
    # W occupies {2}... work it out explicitly instead of asserting the
    # formula's own intermediate state.
    section = (1, 2, 3)
    t = torch.tensor([[10.0, 11, 12, 13, 14, 15]])
    h = torch.tensor([[20.0, 21, 22, 23, 24, 25]])
    w = torch.tensor([[30.0, 31, 32, 33, 34, 35]])
    freqs = torch.stack([t, h, w], dim=0)  # [3, 1, 6]

    out = _interleave_mrope_freqs(freqs, section)
    assert out.shape == (1, 6)

    # H: offset=1, length=2*3=6 -> slice(1,6,3) -> indices {1,4}.
    # W: offset=2, length=3*3=9 (clamped by tensor width 6) -> slice(2,6,3) -> indices {2,5}.
    # T (unclaimed): indices {0,3}.
    expected = torch.tensor([[10.0, 21, 32, 13, 24, 35]])
    assert torch.allclose(out, expected)


def test_interleave_mrope_is_noop_when_all_three_axes_agree():
    # Text-only position ids: T==H==W at every position -> the interleave
    # relabels identical values, so the result equals any single axis.
    same = torch.arange(8, dtype=torch.float32).unsqueeze(0)
    freqs = torch.stack([same, same, same], dim=0)
    out = _interleave_mrope_freqs(freqs, mrope_section=(1, 1, 2))
    assert torch.equal(out, same)


# --- preprocess_qwen3_vl_video / the shared smart-resize --------------------
#
# `preprocess_qwen3_vl_video` shares `_smart_resize_grid` with the image path.
# The image path has live callers, so the first test below pins its output
# against a hardcoded expectation rather than against the shared helper --
# breaking the helper must fail it, not silently redefine "correct".

from src.platform.runtime.native.text_encoders.qwen3_vl_vision import (  # noqa: E402
    _smart_resize_grid,
    preprocess_qwen3_vl_video,
)


@pytest.mark.parametrize("shape,grounding_px,min_pixels,max_pixels,grid,patch0_mean", [
    # Captured from the implementation as it stood BEFORE `_smart_resize_grid`
    # was factored out (git HEAD), by running both versions side by side --
    # not recomputed through the shared helper, so a change to the helper
    # cannot quietly move the goalposts. Four rows cover every branch:
    # exact-fit, the min_pixels floor, the grounding_px pre-scale, and the
    # max_pixels cap.
    ((64, 96, 3), 0, 3136, 12845056, [[1, 4, 6]], -0.024002045392990112),
    ((32, 32, 3), 0, 65536, 16777216, [[1, 16, 16]], -0.055649220943450928),
    ((224, 160, 3), 768, 3136, 12845056, [[1, 14, 10]], 0.027883127331733704),
    ((4000, 30, 3), 0, 3136, 4096, [[1, 46, 2]], 0.0013605240965262055),
])
def test_image_preprocess_output_is_unchanged_by_the_shared_smart_resize(
    shape, grounding_px, min_pixels, max_pixels, grid, patch0_mean,
):
    """Bystander pin for the image path, which has live callers: a known input
    must still produce exactly the grid and pixels it did before the smart-
    resize arithmetic became shared with the video path."""
    torch.manual_seed(0)
    image = torch.rand(*shape)
    patches, grid_thw = preprocess_qwen3_vl_image(
        image, grounding_px=grounding_px, min_pixels=min_pixels, max_pixels=max_pixels,
        patch_size=16, temporal_patch_size=2, merge_size=2,
    )
    assert grid_thw.tolist() == grid
    assert patches.shape == (grid[0][1] * grid[0][2], 3 * 2 * 16 * 16)
    torch.testing.assert_close(
        patches[0].mean(), torch.tensor(patch0_mean), rtol=0, atol=1e-9,
    )


def test_image_preprocess_still_applies_the_min_pixels_floor():
    # The min_pixels branch of the shared helper, exercised through the image
    # path: a tiny image is scaled UP to reach the area floor.
    _patches, grid_thw = preprocess_qwen3_vl_image(
        torch.rand(32, 32, 3), grounding_px=0, min_pixels=65536, max_pixels=16777216,
        patch_size=16, temporal_patch_size=2, merge_size=2,
    )
    grid_h, grid_w = int(grid_thw[0, 1]), int(grid_thw[0, 2])
    assert grid_h * 16 * grid_w * 16 >= 65536


def test_smart_resize_grid_rounds_both_axes_to_the_factor():
    h_bar, w_bar = _smart_resize_grid(100, 200, factor=32, min_pixels=1, max_pixels=10**9)
    assert h_bar % 32 == 0 and w_bar % 32 == 0
    assert (h_bar, w_bar) == (96, 192)


def test_video_grid_t_is_the_frame_count_over_the_temporal_patch():
    _patches, grid_thw = preprocess_qwen3_vl_video(
        torch.rand(6, 64, 64, 3), min_pixels=3136, max_pixels=12845056,
        patch_size=16, temporal_patch_size=2, merge_size=2,
    )
    assert int(grid_thw[0, 0]) == 3


@pytest.mark.parametrize("num_frames,expected_grid_t", [(1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (7, 4)])
def test_video_ragged_frame_count_pads_up_and_grid_t_is_the_ceiling(num_frames, expected_grid_t):
    """The off-by-one the lead flagged: an odd frame count is padded by
    REPEATING the last frame, so grid_t is ceil(F / temporal_patch), never
    floor. A floor would silently drop the tail frame and produce a
    correctly-shaped tensor with wrong rotary positions."""
    patches, grid_thw = preprocess_qwen3_vl_video(
        torch.rand(num_frames, 64, 64, 3), min_pixels=3136, max_pixels=12845056,
        patch_size=16, temporal_patch_size=2, merge_size=2,
    )
    grid_t, grid_h, grid_w = (int(v) for v in grid_thw[0])
    assert grid_t == expected_grid_t
    # The padded frame count, read back out of the row count: grid_t groups of
    # temporal_patch frames each.
    assert grid_t * 2 == num_frames + (-num_frames % 2)
    assert patches.shape[0] == grid_t * grid_h * grid_w


def test_video_padding_repeats_the_last_frame_not_the_first_or_zeros():
    # Three frames, temporal patch 2 -> the second group is (frame 2, frame 2).
    # Build a stack whose frames are constant and distinct so the merged patch
    # content identifies which frame was duplicated.
    frames = torch.stack([
        torch.full((32, 32, 3), 0.0), torch.full((32, 32, 3), 0.5), torch.full((32, 32, 3), 1.0),
    ])
    patches, grid_thw = preprocess_qwen3_vl_video(
        frames, min_pixels=1, max_pixels=10**9,
        patch_size=16, temporal_patch_size=2, merge_size=2,
    )
    grid_t, grid_h, grid_w = (int(v) for v in grid_thw[0])
    assert grid_t == 2
    # A patch row is (channel, temporal, patch_h, patch_w); the last group's
    # two temporal slots must BOTH be frame 2 (value 1.0 -> normalized 1.0).
    tail = patches[grid_h * grid_w:].reshape(-1, 3, 2, 16, 16)
    torch.testing.assert_close(tail[:, :, 0], tail[:, :, 1], rtol=0, atol=0)
    assert tail.min().item() == pytest.approx(1.0)


def test_video_resolves_one_spatial_grid_shared_with_the_image_path():
    frames = torch.rand(4, 64, 96, 3)
    _p, video_grid = preprocess_qwen3_vl_video(
        frames, min_pixels=3136, max_pixels=12845056,
        patch_size=16, temporal_patch_size=2, merge_size=2,
    )
    _p2, image_grid = preprocess_qwen3_vl_image(
        frames[0], grounding_px=0, min_pixels=3136, max_pixels=12845056,
        patch_size=16, temporal_patch_size=2, merge_size=2,
    )
    assert video_grid[0, 1:].tolist() == image_grid[0, 1:].tolist()


def test_video_rejects_a_non_frame_stack():
    with pytest.raises(ValueError, match=r"\[F, H, W, 3\]"):
        preprocess_qwen3_vl_video(torch.rand(64, 64, 3))
