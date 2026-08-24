"""Unit tests for the family-agnostic img2img helpers (no model, mock generator)."""

import types

import numpy as np
from PIL import Image

from src.pipelines.pipes._shared.generation.img2img import (
    Img2ImgGeneratorMixin,
    img2img_denoise,
)
from src.pipelines.pipes._shared.generation.native_generator import (
    build_native_generator,
    native_generator_class,
    register_native_generator,
)
from src.platform.runtime.native.engine import NativeGenerator


class _FakeGen:
    """Records the encode -> sample -> decode calls img2img_denoise makes."""

    def __init__(self):
        self.calls = []

    def encode_image(self, image, vram_free_gb=None):
        self.calls.append("encode")
        self.encoded = image
        self.encoded_latent = np.zeros((1, 16, 2, 2), dtype=np.float32)  # carries a .shape
        return self.encoded_latent

    def sample(self, conditioning, latents_shape, **kw):
        self.calls.append("sample")
        self.sample_shape = latents_shape
        self.sample_kw = kw
        return "clean_latent"

    def decode(self, latent, vram_free_gb=None):
        self.calls.append("decode")
        self.decoded = latent
        return np.ones((1, 16, 16, 3), dtype=np.uint8)


def test_zero_denoise_is_identity_no_model_calls():
    gen = _FakeGen()
    img = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)
    out = img2img_denoise(gen, img, "COND", steps=4, seed=1, cfg_scale=1.0, denoise=0.0)
    assert np.array_equal(out, img)
    assert gen.calls == []                     # short-circuited before touching the model


def test_encode_sample_decode_sequence_and_init_latent():
    gen = _FakeGen()
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    out = img2img_denoise(gen, img, "COND", steps=6, seed=99, cfg_scale=6.0,
                          sampler="euler", denoise=0.3)
    assert gen.calls == ["encode", "sample", "decode"]
    # latents_shape is derived from the encoded latent's shape
    assert gen.sample_shape == (1, 16, 2, 2)
    # img2img contract: the encoded latent is the init, strength is threaded through
    assert gen.sample_kw["denoise_strength"] == 0.3
    assert gen.sample_kw["init_latent"] is gen.encoded_latent
    assert gen.decoded == "clean_latent"
    assert out.shape == (1, 16, 16, 3)


def test_sampler_options_and_step_cache_options_forwarded_to_sample():
    gen = _FakeGen()
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    sampler_options = {"eta": 0.5}
    step_cache_options = {"rel_threshold": 0.12}
    img2img_denoise(
        gen, img, "COND", steps=6, seed=99, cfg_scale=6.0, denoise=0.3,
        sampler_options=sampler_options, step_cache_options=step_cache_options,
    )
    assert gen.sample_kw["sampler_options"] == sampler_options
    assert gen.sample_kw["step_cache_options"] == step_cache_options


def test_sampler_options_and_step_cache_options_default_to_none():
    gen = _FakeGen()
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    img2img_denoise(gen, img, "COND", steps=6, seed=99, cfg_scale=6.0, denoise=0.3)
    assert gen.sample_kw["sampler_options"] is None
    assert gen.sample_kw["step_cache_options"] is None


def test_sigmas_forwarded_to_sample_and_defaults_to_none():
    gen = _FakeGen()
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    img2img_denoise(gen, img, "COND", steps=6, seed=99, cfg_scale=6.0, denoise=0.3)
    assert gen.sample_kw["sigmas"] is None

    explicit = [0.909375, 0.725, 0.421875, 0.0]
    img2img_denoise(gen, img, "COND", steps=6, seed=99, cfg_scale=6.0, denoise=0.3, sigmas=explicit)
    assert gen.sample_kw["sigmas"] is explicit


class _SnapGen:
    """A generator whose ``snap_resolution`` rounds to a 16px grid and whose
    ``encode_image`` records the pixel array it is handed (so a test can assert
    the resolution img2img actually encodes at)."""

    def snap_resolution(self, w, h):
        snap = lambda v: max(16, round(v / 16) * 16)  # noqa: E731
        return snap(w), snap(h)

    def encode_image(self, image, vram_free_gb=None):
        self.encoded_pixels_shape = np.asarray(image).shape
        return np.zeros((1, 16, 2, 2), dtype=np.float32)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_kw = kw
        return "clean_latent"

    def decode(self, latent, vram_free_gb=None):
        return np.ones((1, 8, 8, 3), dtype=np.uint8)


class _Pipe(Img2ImgGeneratorMixin):
    """Bare mixin host: only ``config`` is needed by ``maybe_img2img``."""

    def __init__(self):
        self.config = {"preview": False}


class _Progress:
    def step(self, *a, **kw):
        pass


def _ctx(width, height, images):
    return types.SimpleNamespace(
        quantity=1,
        is_cancelled=lambda: False,
        extra={
            "mode": "img2img",
            "denoise": 0.4,
            "images": images,
            "steps": 6,
            "guidance": 6.0,
            "sampler": "euler",
            "width": width,
            "height": height,
        },
    )


class TestMaybeImg2ImgResolution:
    def test_portrait_source_square_target_keeps_aspect_and_area(self):
        # A portrait upload with a square form resolution must be scaled to the
        # form's pixel AREA while preserving aspect (never squashed to the square).
        gen = _SnapGen()
        pipe = _Pipe()
        src = Image.new("RGB", (512, 768))              # 2:3 portrait
        pipe.maybe_img2img(gen, "COND", _ctx(1024, 1024, [src]), 0, 1, _Progress())
        h, w, _ = gen.encoded_pixels_shape
        # aspect preserved (within one snap step), area ~= the 1024x1024 budget.
        assert abs((w / h) - (512 / 768)) < 0.05
        assert 0.85 <= (w * h) / (1024 * 1024) <= 1.15
        assert w % 16 == 0 and h % 16 == 0

    def test_no_target_huge_source_downscaled_to_cap_area(self):
        # Stock qwen/flux img2img carry no output resolution: a huge upload must
        # be capped to the default area (the 17GB-encode bomb), aspect preserved.
        gen = _SnapGen()
        pipe = _Pipe()
        src = Image.new("RGB", (4096, 4096))
        ctx = _ctx(0, 0, [src])
        ctx.extra["width"] = None
        ctx.extra["height"] = None
        pipe.maybe_img2img(gen, "COND", ctx, 0, 1, _Progress())
        h, w, _ = gen.encoded_pixels_shape
        assert (w * h) <= int(1.15 * 1024 * 1024)       # capped near 1024x1024 area
        assert abs((w / h) - 1.0) < 0.05                # square in, square out

    def test_no_target_small_source_left_untouched(self):
        # Cap only downscales — a source already under the budget is not upscaled.
        gen = _SnapGen()
        pipe = _Pipe()
        src = Image.new("RGB", (256, 256))
        ctx = _ctx(0, 0, [src])
        ctx.extra["width"] = None
        ctx.extra["height"] = None
        pipe.maybe_img2img(gen, "COND", ctx, 0, 1, _Progress())
        assert gen.encoded_pixels_shape == (256, 256, 3)


class TestMaybeImg2ImgLiteralScale:
    """``img2img_scale`` (opt-in, off at 0): a literal multiplier of the
    SOURCE's own (w, h), bypassing the resolution-area-match path entirely --
    Krea-2 enhance mode's "Upscale by" control
    (content/presets/marketplace/Krea2/modes/enhance/pipeline.yml)."""

    def test_scale_multiplies_source_dims_directly_then_snaps(self):
        gen = _SnapGen()
        pipe = _Pipe()
        pipe.config["img2img_scale"] = 2.0
        src = Image.new("RGB", (1600, 800))  # already 16px-aligned before AND after x2
        # A target resolution is present in ctx.extra too -- img2img_scale must
        # win outright and ignore it entirely.
        pipe.maybe_img2img(gen, "COND", _ctx(999, 999, [src]), 0, 1, _Progress())
        h, w, _ = gen.encoded_pixels_shape
        assert (w, h) == (3200, 1600)

    def test_scale_off_by_default_falls_back_to_area_match_byte_identical(self):
        # Same inputs as test_portrait_source_square_target_keeps_aspect_and_area,
        # cross-checked against the pre-img2img_scale formula directly: proves
        # the new branch is a strict opt-in, not a behaviour change for a family
        # that never sets it (Flux/Qwen/Anima/Z-Image's own img2img modes).
        gen = _SnapGen()
        pipe = _Pipe()
        src = Image.new("RGB", (512, 768))
        pipe.maybe_img2img(gen, "COND", _ctx(1024, 1024, [src]), 0, 1, _Progress())
        h, w, _ = gen.encoded_pixels_shape
        expected_scale = (1024 * 1024 / (512 * 768)) ** 0.5
        expected_w, expected_h = gen.snap_resolution(round(512 * expected_scale), round(768 * expected_scale))
        assert (w, h) == (expected_w, expected_h)

    def test_zero_scale_is_treated_as_off_same_as_absent(self):
        gen = _SnapGen()
        pipe = _Pipe()
        pipe.config["img2img_scale"] = 0.0
        src = Image.new("RGB", (512, 768))
        pipe.maybe_img2img(gen, "COND", _ctx(1024, 1024, [src]), 0, 1, _Progress())
        h, w, _ = gen.encoded_pixels_shape
        # The area-matched path ran (not a scale=0 collapse to a zero/degenerate size).
        assert 0.85 <= (w * h) / (1024 * 1024) <= 1.15


class TestMaybeImg2ImgSigmas:
    """The ``ctx.extra["sigmas"]`` -> ``img2img_denoise(sigmas=...)`` seam is
    family-agnostic, so exercised once here rather than per
    family. Krea-2's own ``refine_tail`` slicing has its dedicated coverage in
    tests/pipelines/pipes/generator/krea2/test_krea2_generator.py."""

    def test_defaults_to_none_when_ctx_extra_carries_no_sigmas(self):
        gen = _SnapGen()
        pipe = _Pipe()
        src = Image.new("RGB", (256, 256))
        pipe.maybe_img2img(gen, "COND", _ctx(256, 256, [src]), 0, 1, _Progress())
        assert gen.sample_kw["sigmas"] is None

    def test_forwarded_unchanged_when_ctx_extra_sets_sigmas(self):
        gen = _SnapGen()
        pipe = _Pipe()
        src = Image.new("RGB", (256, 256))
        ctx = _ctx(256, 256, [src])
        explicit = [0.655, 0.513, 0.311, 0.0]
        ctx.extra["sigmas"] = explicit
        pipe.maybe_img2img(gen, "COND", ctx, 0, 1, _Progress())
        assert gen.sample_kw["sigmas"] is explicit


class TestMaybeImg2ImgCancellation:
    """`ctx.is_cancelled` must reach `NativeGenerator.sample` so a mid-refine
    cancellation can actually stop the denoise loop instead of running to
    completion regardless (the seam a stuck-cancel bug would live in)."""

    def test_ctx_is_cancelled_reaches_sample(self):
        gen = _SnapGen()
        pipe = _Pipe()
        src = Image.new("RGB", (256, 256))
        ctx = _ctx(256, 256, [src])
        probe = lambda: False
        ctx.is_cancelled = probe
        pipe.maybe_img2img(gen, "COND", ctx, 0, 1, _Progress())
        assert gen.sample_kw["is_cancelled"] is probe


class TestGeneratorFactory:
    def test_unknown_family_defaults_to_native_generator(self):
        assert native_generator_class("no_such_family") is NativeGenerator

    def test_registered_subclass_is_returned(self):
        @register_native_generator("test_family_reg")
        class _Sub(NativeGenerator):
            pass

        assert native_generator_class("test_family_reg") is _Sub

    def test_build_uses_registered_class_and_bundle_components(self):
        @register_native_generator("test_family_build")
        class _Recorder(NativeGenerator):
            def __init__(self, dit, te, vae, device_plan):
                self.built = (dit, te, vae, device_plan)

        bundle = types.SimpleNamespace(
            dit=types.SimpleNamespace(estimated_vram_gb=1.0),
            te_encoder="TE",
            vae="VAE",
            spec=types.SimpleNamespace(family="test_family_build"),
        )
        gen = build_native_generator(bundle, device="cpu")
        assert isinstance(gen, _Recorder)
        assert gen.built[0] is bundle.dit
        assert gen.built[1] == "TE"
        assert gen.built[2] == "VAE"
