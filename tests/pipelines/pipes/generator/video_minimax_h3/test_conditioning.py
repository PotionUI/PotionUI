"""Tests for fl2va keyframe conditioning: the VAE-encode posterior-sampling
wiring (fixed KEYFRAME_ENCODE_SEED=42, independent of the request's own
seed) and the canvas-fit stretch/cover-crop split. CPU-only, no weights --
`_FakeVideoVae` reproduces `MiniMaxH3VideoVAE.encode`'s own
`sample_posterior`/`generator` math (mean + std*noise) rather than a
trivial stand-in, so the sampling wiring is exercised for real."""

from __future__ import annotations

import pytest
import torch
import torch.nn as nn
from PIL import Image

from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
    KEYFRAME_ENCODE_SEED,
    REFERENCE_IMAGE_SHORT_EDGE,
    encode_keyframe_condition,
    fit_keyframe_to_canvas,
    normalize_reference_image,
    prepare_reference_condition_rows,
)
from src.pipelines.pipes.generator.video_minimax_h3.schedule import KEYFRAME_NOISE_AUG

LATENT_CHANNELS = 24


class _FakeVideoVae(nn.Module):
    """Reproduces `MiniMaxH3VideoVAE.encode`'s `sample_posterior`/`generator`
    contract: mode -> a fixed (non-zero, so a bug that zeroes it is visible)
    mean; sample -> `mean + std * noise` with a NON-trivial `logvar` (so
    `std != 1` and the sample/mode difference isn't a degenerate no-op)."""

    def __init__(self):
        super().__init__()
        self.weight = nn.Parameter(torch.zeros(1))

    def encode(self, x: torch.Tensor, *, sample_posterior: bool = False, generator=None) -> torch.Tensor:
        b, _c, f, h, w = x.shape
        shape = (b, LATENT_CHANNELS, f, h, w)
        mean = torch.full(shape, 0.5)
        if not sample_posterior:
            return mean
        logvar = torch.full(shape, 2.0)  # std = exp(1.0) != 1, a real scale
        std = torch.exp(0.5 * logvar)
        noise = torch.randn(shape, generator=generator)
        return mean + std * noise


class _QuantisedFakeVideoVae(_FakeVideoVae):
    """The int8_tensorwise/ConvRot repack stores the ViT decoder's Linear
    weights as integer codes. They are registered ahead of the float encoder
    weight here -- the order the real module happens to avoid -- so that
    picking "the first parameter's dtype" hands `encode` an integer dtype."""

    def __init__(self):
        nn.Module.__init__(self)
        self.codes = nn.Parameter(torch.zeros(4, dtype=torch.int8), requires_grad=False)
        self.weight = nn.Parameter(torch.zeros(1))

    def encode(self, x: torch.Tensor, *, sample_posterior: bool = False, generator=None) -> torch.Tensor:
        assert x.is_floating_point(), f"encode got a non-float activation dtype: {x.dtype}"
        return super().encode(x, sample_posterior=sample_posterior, generator=generator)


def _pixels() -> torch.Tensor:
    return torch.randint(0, 256, (1, 3, 1, 8, 8), dtype=torch.float32)


def test_keyframe_encode_skips_integer_parameters_when_choosing_its_dtype():
    latents_mean = [0.0] * LATENT_CHANNELS
    latents_std = [1.0] * LATENT_CHANNELS
    vae = _QuantisedFakeVideoVae()
    assert next(vae.parameters()).dtype == torch.int8  # the trap this guards

    got = encode_keyframe_condition(vae, _pixels(), latents_mean=latents_mean, latents_std=latents_std)

    assert torch.isfinite(got).all()


def test_keyframe_encode_still_follows_the_float_parameter_dtype():
    """Unquantised checkpoints must be untouched by the above: the encode
    dtype tracks the stored float weight (fp16 repack -> fp16), not a
    hardcoded float32."""
    vae = _FakeVideoVae()
    vae.weight = nn.Parameter(torch.zeros(1, dtype=torch.float16))
    seen = {}
    inner = vae.encode

    def spy(x, **kwargs):
        seen["dtype"] = x.dtype
        return inner(x, **kwargs)

    vae.encode = spy
    encode_keyframe_condition(
        vae, _pixels(), latents_mean=[0.0] * LATENT_CHANNELS, latents_std=[1.0] * LATENT_CHANNELS,
    )
    assert seen["dtype"] == torch.float16


def test_keyframe_encode_is_deterministic_across_calls():
    vae = _FakeVideoVae()
    pixels = _pixels()
    latents_mean = [0.0] * LATENT_CHANNELS
    latents_std = [1.0] * LATENT_CHANNELS

    first = encode_keyframe_condition(vae, pixels, latents_mean=latents_mean, latents_std=latents_std)
    second = encode_keyframe_condition(vae, pixels, latents_mean=latents_mean, latents_std=latents_std)

    torch.testing.assert_close(first, second, rtol=0, atol=0)


def test_keyframe_encode_actually_samples_the_posterior_not_the_mode():
    # sample_posterior=True with a real (non-degenerate) logvar must differ
    # from the mode -- if this ever collapsed back to the mode branch (a
    # regression to the pre-sampling wiring), this would silently pass with
    # sample == mode, so assert they differ instead.
    vae = _FakeVideoVae()
    pixels = _pixels()
    latents_mean = [0.0] * LATENT_CHANNELS
    latents_std = [1.0] * LATENT_CHANNELS

    sampled = encode_keyframe_condition(vae, pixels, latents_mean=latents_mean, latents_std=latents_std)
    mode = vae.encode(pixels.to(torch.float32), sample_posterior=False)
    assert not torch.allclose(sampled, mode)


def test_keyframe_encode_uses_the_fixed_seed_independent_of_request_seed():
    # Constructing the generator with the SAME fixed seed independently
    # (bypassing the module under test) must reproduce the exact same draw
    # the module produces -- proves KEYFRAME_ENCODE_SEED, not some other
    # value, is what actually seeds the sample.
    vae = _FakeVideoVae()
    pixels = _pixels()
    latents_mean = [0.0] * LATENT_CHANNELS
    latents_std = [1.0] * LATENT_CHANNELS

    got = encode_keyframe_condition(vae, pixels, latents_mean=latents_mean, latents_std=latents_std)

    generator = torch.Generator(device="cpu").manual_seed(KEYFRAME_ENCODE_SEED)
    reference_sample = vae.encode(pixels.to(torch.float32), sample_posterior=True, generator=generator)
    reference_sample = reference_sample.to(torch.float16).float()  # same rounding step the module applies
    torch.testing.assert_close(got, reference_sample, rtol=0, atol=0)


def test_bite_check_wrong_seed_would_not_reproduce_the_draw():
    # BITE CHECK: a generator seeded to something OTHER than
    # KEYFRAME_ENCODE_SEED must NOT reproduce the module's draw -- confirms
    # the equality above is actually sensitive to the seed value, not
    # vacuously true (e.g. because logvar collapsed std to ~0).
    vae = _FakeVideoVae()
    pixels = _pixels()
    latents_mean = [0.0] * LATENT_CHANNELS
    latents_std = [1.0] * LATENT_CHANNELS

    got = encode_keyframe_condition(vae, pixels, latents_mean=latents_mean, latents_std=latents_std)

    wrong_generator = torch.Generator(device="cpu").manual_seed(KEYFRAME_ENCODE_SEED + 1)
    wrong_sample = vae.encode(pixels.to(torch.float32), sample_posterior=True, generator=wrong_generator)
    wrong_sample = wrong_sample.to(torch.float16).float()
    assert not torch.allclose(got, wrong_sample)


def test_keyframe_encode_seed_is_42():
    assert KEYFRAME_ENCODE_SEED == 42


# -- canvas fit: stretch (geometry anchor) vs cover-crop (follower) ---------

def test_geometry_anchor_is_stretched_not_cropped():
    image = Image.new("RGB", (100, 50))
    fitted = fit_keyframe_to_canvas(image, height=64, width=64, is_geometry_anchor=True)
    assert fitted.size == (64, 64)


def test_follower_is_cover_cropped_to_exact_canvas_size():
    image = Image.new("RGB", (100, 50))
    fitted = fit_keyframe_to_canvas(image, height=64, width=64, is_geometry_anchor=False)
    assert fitted.size == (64, 64)


def test_already_canvas_sized_image_passes_through_unchanged():
    image = Image.new("RGB", (64, 64))
    fitted = fit_keyframe_to_canvas(image, height=64, width=64, is_geometry_anchor=False)
    assert fitted is image


# -- ref2va reference own-resolution fit -------------------------------------

def test_reference_image_short_edge_is_2048():
    assert REFERENCE_IMAGE_SHORT_EDGE == 2048


def test_reference_image_is_upscaled_to_its_own_short_edge():
    # A small square image has no canvas to fit onto (unlike a keyframe) --
    # it is scaled up so its short edge reaches REFERENCE_IMAGE_SHORT_EDGE,
    # rounded to canvas_multiple, with NO upper pixel cap.
    image = Image.new("RGB", (100, 200))  # short edge = 100
    fitted = normalize_reference_image(image, canvas_multiple=32, short_edge=256)
    scale = 256 / 100
    expected_width = round(100 * scale / 32) * 32
    expected_height = round(200 * scale / 32) * 32
    assert fitted.size == (expected_width, expected_height)


def test_reference_image_already_at_target_size_passes_through_unchanged():
    # canvas_multiple=32, short_edge=32: a 32x32 image is already exactly on
    # its own target grid -- must be returned as-is (identity fast path).
    image = Image.new("RGB", (32, 32))
    fitted = normalize_reference_image(image, canvas_multiple=32, short_edge=32)
    assert fitted is image


def test_reference_image_rounds_to_canvas_multiple():
    image = Image.new("RGB", (100, 100))
    fitted = normalize_reference_image(image, canvas_multiple=32, short_edge=100)
    assert fitted.size[0] % 32 == 0
    assert fitted.size[1] % 32 == 0


@pytest.mark.parametrize("size", [(500, 100), (100, 500)])
def test_reference_image_aspect_ratio_outside_1_to_4_rejected(size):
    image = Image.new("RGB", size)
    with pytest.raises(ValueError):
        normalize_reference_image(image, canvas_multiple=32)


def test_reference_image_aspect_ratio_exactly_4_to_1_accepted():
    image = Image.new("RGB", (400, 100))
    normalize_reference_image(image, canvas_multiple=32, short_edge=100)  # must not raise


def test_reference_image_uses_no_upper_pixel_cap_unlike_fl2va_canvas():
    # An extreme short edge upscale has no area cap to hit -- fl2va's target
    # canvas is bounded by CANVAS_MAX_PIXELS, a ref2va reference is not.
    image = Image.new("RGB", (32, 32))
    fitted = normalize_reference_image(image, canvas_multiple=32, short_edge=4096)
    assert fitted.size == (4096, 4096)


# -- ref2va reference condition rows: own-resolution encode + noise + pack ---

def test_prepare_reference_condition_rows_empty_returns_empty_tensors():
    condition_latents, rows = prepare_reference_condition_rows(
        [], vae_module=_FakeVideoVae(), canvas_multiple=32, patch_size=(1, 2, 2),
        device="cpu", dtype=torch.float32, latents_mean=[0.0] * LATENT_CHANNELS,
        latents_std=[1.0] * LATENT_CHANNELS, generator=torch.Generator().manual_seed(0),
        short_edge=32,
    )
    assert condition_latents == []
    assert rows.shape == (0, LATENT_CHANNELS * 1 * 2 * 2)


def test_prepare_reference_condition_rows_returns_one_clean_latent_per_reference():
    images = [Image.new("RGB", (64, 32)), Image.new("RGB", (32, 64))]
    condition_latents, rows = prepare_reference_condition_rows(
        images, vae_module=_FakeVideoVae(), canvas_multiple=32, patch_size=(1, 2, 2),
        device="cpu", dtype=torch.float32, latents_mean=[0.0] * LATENT_CHANNELS,
        latents_std=[1.0] * LATENT_CHANNELS, generator=torch.Generator().manual_seed(0),
        short_edge=32,
    )
    assert len(condition_latents) == 2
    for latent in condition_latents:
        assert latent.shape[0] == 1 and latent.shape[1] == LATENT_CHANNELS and latent.shape[2] == 1
    # Two DIFFERENT own-resolutions -- unlike fl2va's shared target canvas,
    # each reference's own aspect ratio survives into its own latent shape.
    assert condition_latents[0].shape[3:] != condition_latents[1].shape[3:]
    total_rows = sum((l.shape[3] // 2) * (l.shape[4] // 2) for l in condition_latents)
    assert rows.shape == (total_rows, LATENT_CHANNELS * 1 * 2 * 2)


def test_prepare_reference_condition_rows_noises_to_keyframe_noise_aug():
    # The clean latent this function returns and the noised row it packs
    # must NOT be the same value -- the noising step actually ran.
    images = [Image.new("RGB", (32, 32))]
    condition_latents, rows = prepare_reference_condition_rows(
        images, vae_module=_FakeVideoVae(), canvas_multiple=32, patch_size=(1, 2, 2),
        device="cpu", dtype=torch.float32, latents_mean=[0.0] * LATENT_CHANNELS,
        latents_std=[1.0] * LATENT_CHANNELS, generator=torch.Generator().manual_seed(0),
        short_edge=32,
    )
    assert KEYFRAME_NOISE_AUG < 1.0  # sanity: not fully clean, not fully noised
    from src.pipelines.pipes.generator.video_minimax_h3.layout import patchify_video_latents
    clean_packed = patchify_video_latents(condition_latents[0], (1, 2, 2))
    assert not torch.allclose(rows, clean_packed)


# -- ref2va video/audio references: normalize -> encode -> pack --------------
#
# The fakes below stand in for the two real VAEs at their exact shape
# contracts, so every assertion here is about geometry the layout actually
# slices on. `_ChunkingFakeVideoVae` reproduces the real 17n+5 -> 5n+2
# temporal chunking (the plain `_FakeVideoVae` above passes the frame count
# straight through, which would make a video reference's row count
# indistinguishable from a wrong one).

import numpy as np

from src.pipelines.pipes.generator.video_minimax_h3.audio import AUDIO_SAMPLE_RATE, pack_audio_rows
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
    MAX_AUDIO_REFERENCES,
    MAX_REFERENCES,
    MAX_VIDEO_REFERENCES,
    ReferenceMedia,
    normalize_reference_video,
    normalize_references,
    prepare_reference_conditioning,
    snap_reference_video_frames,
    validate_references,
)
from src.pipelines.pipes.generator.video_minimax_h3.geometry import video_latent_num_frames
from src.pipelines.pipes.generator.video_minimax_h3.layout import ReferenceBlock, build_ref2va_packed_sequence

AUDIO_LATENT_CHANNELS = 8
AUDIO_HOP_LENGTH = 800


class _ChunkingFakeVideoVae(_FakeVideoVae):
    """`_FakeVideoVae` plus the real video VAE's TEMPORAL chunking: a
    `17 * n + 5` frame stack encodes to `5 * n + 2` latent frames, a single
    frame stays one. Spatial size passes through unchanged, as the plain fake
    already does, so a latent's `(h, w)` is still the caller's own."""

    def encode(self, x: torch.Tensor, *, sample_posterior: bool = False, generator=None) -> torch.Tensor:
        b, _c, f, h, w = x.shape
        latent_frames = 1 if f == 1 else video_latent_num_frames(f)
        shape = (b, LATENT_CHANNELS, latent_frames, h, w)
        mean = torch.full(shape, 0.5)
        if not sample_posterior:
            return mean
        std = torch.exp(0.5 * torch.full(shape, 2.0))
        return mean + std * torch.randn(shape, generator=generator)


class _FakeAudioVae:
    """The real `MiniMaxH3AudioVAE.encode` shape contract: `(B, 1, samples)`
    in, `(B, latent_channels, ceil(samples / hop))` out, deterministic (the
    real one returns the posterior mean and never draws noise)."""

    latent_channels = AUDIO_LATENT_CHANNELS
    hop_length = AUDIO_HOP_LENGTH

    def __init__(self):
        self.latents_mean = torch.zeros(AUDIO_LATENT_CHANNELS)
        self.latents_std = torch.ones(AUDIO_LATENT_CHANNELS)

    def encode(self, sample: torch.Tensor) -> torch.Tensor:
        assert sample.ndim == 3 and sample.shape[1] == 1, tuple(sample.shape)
        num = -(-sample.shape[-1] // self.hop_length)
        return torch.full((sample.shape[0], self.latent_channels, num), 0.25)


def _frames(count: int, height: int = 32, width: int = 32) -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.integers(0, 256, (count, height, width, 3), dtype=np.uint8)


def _waveform(seconds: float, sample_rate: int = AUDIO_SAMPLE_RATE) -> torch.Tensor:
    return torch.zeros(2, int(seconds * sample_rate))


def _encode_kwargs(**overrides):
    kwargs = dict(
        patch_size=(1, 2, 2), device="cpu", dtype=torch.float32,
        latents_mean=[0.0] * LATENT_CHANNELS, latents_std=[1.0] * LATENT_CHANNELS,
        generator=torch.Generator().manual_seed(0),
    )
    kwargs.update(overrides)
    return kwargs


# -- normalize_reference_video ----------------------------------------------


def test_reference_video_resample_duplicates_whole_frames_not_interpolates():
    # 12 fps -> 24 fps doubles the frame count by REPEATING frames; every
    # output frame must be byte-identical to some input frame.
    frames = _frames(10)
    got = normalize_reference_video(
        frames, fps=12.0, num_frames=120, canvas_multiple=32, short_edge=32, max_pixels=32 * 32,
    )
    assert got.shape[0] == 20
    for frame in got:
        assert any(np.array_equal(frame, source) for source in frames)


def test_reference_video_is_truncated_to_the_generated_frame_count():
    got = normalize_reference_video(
        _frames(50), fps=24.0, num_frames=21, canvas_multiple=32, short_edge=32, max_pixels=32 * 32,
    )
    assert got.shape[0] == 21


def test_reference_video_uses_its_own_aspect_ratio_on_the_targets_canvas_rule():
    # A 2:1 reference resolves to a 2:1 canvas of its own -- NOT the target's
    # canvas, and NOT the image reference's uncapped 2048 short edge.
    got = normalize_reference_video(
        _frames(5, height=100, width=200), fps=24.0, num_frames=5,
        canvas_multiple=32, short_edge=64, max_pixels=1_000_000,
    )
    assert got.shape[1:3] == (64, 128)


def test_reference_video_already_normalized_passes_through_untouched():
    frames = _frames(5, height=64, width=64)
    got = normalize_reference_video(
        frames, fps=24.0, num_frames=5, canvas_multiple=32, short_edge=64, max_pixels=1_000_000,
    )
    # No resample and no rescale: the frames come back as a view on the very
    # buffer they went in as, not a re-encoded copy.
    assert np.shares_memory(got, frames)


def test_reference_video_accepts_a_chw_float_tensor():
    tensor = torch.rand(4, 3, 64, 64)
    got = normalize_reference_video(
        tensor, fps=24.0, num_frames=4, canvas_multiple=32, short_edge=64, max_pixels=1_000_000,
    )
    assert got.dtype == np.uint8
    assert got.shape == (4, 64, 64, 3)


# -- snap_reference_video_frames --------------------------------------------


@pytest.mark.parametrize("num_frames,expected", [(22, 22), (30, 22), (38, 22), (39, 39), (56, 56), (60, 56)])
def test_reference_video_frame_count_snaps_down_to_a_whole_chunk(num_frames, expected):
    assert snap_reference_video_frames(num_frames) == expected


def test_reference_video_shorter_than_one_chunk_is_rejected():
    # The reference's own `max(1, ...)` would return 22 here, i.e. MORE frames
    # than exist -- unencodable, so this raises rather than passing the VAE a
    # count that is neither 17n+5 nor available.
    with pytest.raises(ValueError, match="at least 22 frames"):
        snap_reference_video_frames(21)


# -- validation --------------------------------------------------------------


def test_audio_references_cannot_be_the_only_references():
    with pytest.raises(ValueError, match="cannot be used"):
        validate_references([ReferenceMedia(kind="audio", audio=_waveform(1.0))])


def test_too_many_video_references_rejected():
    references = [ReferenceMedia(kind="video", frames=_frames(22), fps=24.0)] * (MAX_VIDEO_REFERENCES + 1)
    with pytest.raises(ValueError, match="at most 3 video"):
        validate_references(references)


def test_too_many_references_in_total_rejected():
    # Every per-modality limit is respected (9 + 3 + 1); only the TOTAL is over.
    references = (
        [ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32)))] * 9
        + [ReferenceMedia(kind="video", frames=_frames(22), fps=24.0)] * 3
        + [ReferenceMedia(kind="audio", audio=_waveform(0.5))]
    )
    assert len(references) == MAX_REFERENCES + 1
    with pytest.raises(ValueError, match=f"at most {MAX_REFERENCES} references"):
        validate_references(references)


def test_at_most_three_audio_references():
    assert MAX_AUDIO_REFERENCES == 3


# -- normalize_references: every modality on H3's own rates ------------------


def test_normalize_references_puts_a_video_soundtrack_on_the_audio_vae_rate():
    # A video reference's own soundtrack is normalized exactly like a
    # standalone audio reference's, and truncated to the generated duration.
    normalized = normalize_references(
        [ReferenceMedia(kind="video", frames=_frames(60), fps=24.0, audio=_waveform(10.0), sample_rate=AUDIO_SAMPLE_RATE)],
        num_frames=24, canvas_multiple=32, canvas_short_edge=32, canvas_max_pixels=32 * 32,
    )
    assert normalized[0].kind == "video"
    assert normalized[0].has_audio
    assert normalized[0].sample_rate == AUDIO_SAMPLE_RATE
    # 24 frames at 24 fps = 1 second of the 10 supplied.
    assert normalized[0].audio.shape == (2, AUDIO_SAMPLE_RATE)


def test_normalize_references_upmixes_a_mono_audio_reference_to_stereo():
    normalized = normalize_references(
        [
            ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32))),
            ReferenceMedia(kind="audio", audio=torch.zeros(1, AUDIO_SAMPLE_RATE), sample_rate=AUDIO_SAMPLE_RATE),
        ],
        num_frames=24, reference_short_edge=32, canvas_multiple=32,
    )
    assert normalized[1].audio.shape[0] == 2


# -- prepare_reference_conditioning: rows, blocks and packed order -----------


def test_video_reference_yields_one_row_per_latent_frame_patch():
    # 30 supplied frames snap down to 22 (17*1 + 5) -> 7 latent frames
    # (5*1 + 2); at 32x32 with patch (1, 2, 2) that is 16*16 = 256 rows each.
    frames = _frames(30, height=32, width=32)
    got = prepare_reference_conditioning(
        [ReferenceMedia(kind="video", frames=frames, fps=24.0)],
        vae_module=_ChunkingFakeVideoVae(), **_encode_kwargs(),
    )
    assert len(got.condition_latents) == 1
    assert got.condition_latents[0].shape == (1, LATENT_CHANNELS, 7, 32, 32)
    assert got.condition_rows.shape == (7 * 16 * 16, LATENT_CHANNELS * 1 * 2 * 2)
    assert got.blocks == (ReferenceBlock(kind="video", has_audio=False),)
    assert got.condition_audio_rows is None
    assert got.audio_condition_latents == ()


def test_video_reference_row_count_tracks_its_frame_count():
    # A LONGER reference must produce proportionally more rows -- guards
    # against a video being silently encoded as a single frame.
    short = prepare_reference_conditioning(
        [ReferenceMedia(kind="video", frames=_frames(22), fps=24.0)],
        vae_module=_ChunkingFakeVideoVae(), **_encode_kwargs(),
    )
    long = prepare_reference_conditioning(
        [ReferenceMedia(kind="video", frames=_frames(39), fps=24.0)],
        vae_module=_ChunkingFakeVideoVae(), **_encode_kwargs(),
    )
    assert short.condition_latents[0].shape[2] == 7      # 22 frames -> 5*1 + 2
    assert long.condition_latents[0].shape[2] == 12      # 39 -> 38 -> 5*2 + 2
    assert long.condition_rows.shape[0] == short.condition_rows.shape[0] * 12 // 7


def test_audio_reference_populates_the_row_count_the_layout_slices_on():
    # The layout reads `rows.shape[0]` off each audio_condition_latents entry
    # and divides by audio_channels to get that block's latent count, so the
    # row count must be `n * 2` channel-major, not `n`.
    audio_vae = _FakeAudioVae()
    waveform = _waveform(1.0)
    expected_latents = -(-waveform.shape[-1] // AUDIO_HOP_LENGTH)
    got = prepare_reference_conditioning(
        [
            ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32))),
            ReferenceMedia(kind="audio", audio=waveform),
        ],
        vae_module=_FakeVideoVae(), audio_vae_module=audio_vae, **_encode_kwargs(),
    )
    assert len(got.audio_condition_latents) == 1
    assert got.audio_condition_latents[0].shape == (expected_latents * 2, AUDIO_LATENT_CHANNELS)
    assert got.condition_audio_rows.shape == (expected_latents * 2, AUDIO_LATENT_CHANNELS)
    # An audio reference contributes NO visual latent, so the iterators the
    # layout consumes have different lengths on purpose.
    assert len(got.condition_latents) == 1
    assert got.blocks == (ReferenceBlock(kind="image"), ReferenceBlock(kind="audio", has_audio=True))


def test_audio_condition_rows_are_channel_major():
    # Re-derived independently from `pack_audio_rows` rather than by calling
    # the module under test, so a shared pack/unpack bug cannot hide.
    audio_vae = _FakeAudioVae()
    got = prepare_reference_conditioning(
        [
            ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32))),
            ReferenceMedia(kind="audio", audio=_waveform(0.5)),
        ],
        vae_module=_FakeVideoVae(), audio_vae_module=audio_vae, **_encode_kwargs(),
    )
    latents = audio_vae.encode(_waveform(0.5)[:, None])
    expected = pack_audio_rows((latents - 0.0) / 1.0)
    torch.testing.assert_close(got.audio_condition_latents[0], expected, rtol=0, atol=0)


def test_audio_bearing_video_reference_contributes_to_both_iterators():
    got = prepare_reference_conditioning(
        [ReferenceMedia(kind="video", frames=_frames(22), fps=24.0, audio=_waveform(1.0))],
        vae_module=_ChunkingFakeVideoVae(), audio_vae_module=_FakeAudioVae(), **_encode_kwargs(),
    )
    assert got.blocks == (ReferenceBlock(kind="video", has_audio=True),)
    assert len(got.condition_latents) == 1
    assert len(got.audio_condition_latents) == 1


def test_soundtrack_without_an_audio_vae_is_a_clear_error():
    with pytest.raises(ValueError, match="audio VAE"):
        prepare_reference_conditioning(
            [
                ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32))),
                ReferenceMedia(kind="audio", audio=_waveform(0.5)),
            ],
            vae_module=_FakeVideoVae(), **_encode_kwargs(),
        )


def test_mixed_references_keep_packed_order_and_kinds():
    references = [
        ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32))),
        ReferenceMedia(kind="video", frames=_frames(22), fps=24.0, audio=_waveform(0.5)),
        ReferenceMedia(kind="audio", audio=_waveform(0.5)),
        ReferenceMedia(kind="video", frames=_frames(22), fps=24.0),
    ]
    got = prepare_reference_conditioning(
        references, vae_module=_ChunkingFakeVideoVae(), audio_vae_module=_FakeAudioVae(), **_encode_kwargs(),
    )
    assert got.blocks == (
        ReferenceBlock(kind="image", has_audio=False),
        ReferenceBlock(kind="video", has_audio=True),
        ReferenceBlock(kind="audio", has_audio=True),
        ReferenceBlock(kind="video", has_audio=False),
    )
    # Visual latents: image, video, video -- the audio reference is SKIPPED.
    assert len(got.condition_latents) == 3
    assert got.condition_latents[0].shape[2] == 1   # the image
    assert got.condition_latents[1].shape[2] == 7   # the first video
    assert got.condition_latents[2].shape[2] == 7   # the second video
    # Audio blocks: the audio-bearing video, then the standalone audio.
    assert len(got.audio_condition_latents) == 2


def test_mixed_references_feed_build_ref2va_packed_sequence_consistently():
    # The real end-to-end geometry check: what this module emits must be
    # exactly what the layout's iterators consume, with no leftover entries
    # and no shape disagreement.
    references = [
        ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32))),
        ReferenceMedia(kind="video", frames=_frames(22), fps=24.0, audio=_waveform(0.2)),
        ReferenceMedia(kind="audio", audio=_waveform(0.2)),
    ]
    got = prepare_reference_conditioning(
        references, vae_module=_ChunkingFakeVideoVae(), audio_vae_module=_FakeAudioVae(), **_encode_kwargs(),
    )
    text_tags = torch.full((4,), 1, dtype=torch.long)
    layout = build_ref2va_packed_sequence(
        text_tags, got.blocks, got.condition_latents, got.audio_condition_latents,
        num_latent_frames=2, latent_height=4, latent_width=4, num_audio_latents=3, patch_size=(1, 2, 2),
    )
    assert layout.num_condition_video_rows == got.condition_rows.shape[0]
    assert layout.num_condition_audio_rows == got.condition_audio_rows.shape[0]
    # The condition prefix of each stream is exactly what this module packed.
    assert layout.video_indices[: layout.num_condition_video_rows].numel() == got.condition_rows.shape[0]
    assert layout.audio_indices[: layout.num_condition_audio_rows].numel() == got.condition_audio_rows.shape[0]


# -- the image-only path is unchanged ----------------------------------------


def test_images_only_match_the_existing_reference_condition_rows_path_bit_for_bit():
    images = [Image.new("RGB", (64, 32)), Image.new("RGB", (32, 64))]
    old_latents, old_rows = prepare_reference_condition_rows(
        images, vae_module=_FakeVideoVae(), canvas_multiple=32, short_edge=32,
        **_encode_kwargs(),
    )
    normalized = normalize_references(
        [ReferenceMedia(kind="image", image=image) for image in images],
        num_frames=24, canvas_multiple=32, reference_short_edge=32,
    )
    new = prepare_reference_conditioning(
        normalized, vae_module=_FakeVideoVae(), **_encode_kwargs(),
    )
    torch.testing.assert_close(new.condition_rows, old_rows, rtol=0, atol=0)
    assert len(new.condition_latents) == len(old_latents)
    for got, expected in zip(new.condition_latents, old_latents):
        torch.testing.assert_close(got, expected, rtol=0, atol=0)


def test_a_soundtrack_does_not_shift_the_generator_state_the_video_noise_reads():
    # The "one generator, three draws, in order" contract: only VISUAL
    # references draw. Adding an audio reference must leave the visual draws
    # -- and therefore everything drawn after conditioning -- untouched.
    image = ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32)))
    without = torch.Generator().manual_seed(7)
    with_audio = torch.Generator().manual_seed(7)
    a = prepare_reference_conditioning(
        [image], vae_module=_FakeVideoVae(), **_encode_kwargs(generator=without),
    )
    b = prepare_reference_conditioning(
        [image, ReferenceMedia(kind="audio", audio=_waveform(0.5))],
        vae_module=_FakeVideoVae(), audio_vae_module=_FakeAudioVae(), **_encode_kwargs(generator=with_audio),
    )
    torch.testing.assert_close(a.condition_rows, b.condition_rows, rtol=0, atol=0)
    assert torch.equal(
        torch.randn(4, generator=without), torch.randn(4, generator=with_audio),
    )
