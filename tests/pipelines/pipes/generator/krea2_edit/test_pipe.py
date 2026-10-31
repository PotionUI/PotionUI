
from __future__ import annotations

import numpy as np
import pytest
import torch
from PIL import Image

from types import SimpleNamespace

from src.pipelines.contracts import PipeConfigSpec, PipeInput
from src.pipelines.outputs import GenerationExecutionError, ImageGenerationOutput
from src.pipelines.pipes.generator.krea2_edit.geometry import (
    area_fit_size,
    fit_ref_mask,
    fit_source_area,
    fit_source_image,
)
from src.pipelines.pipes.generator.krea2_edit.main import GeneratorKrea2EditPipe


@pytest.fixture
def pipe_module():
    return SimpleNamespace(
        fit_source_image=fit_source_image,
        fit_source_area=fit_source_area,
        area_fit_size=area_fit_size,
        fit_ref_mask=fit_ref_mask,
        GeneratorKrea2EditPipe=GeneratorKrea2EditPipe,
    )


class _FakeBundle:

    def __init__(self, te_cache_key=None):
        self.te_cache_key = te_cache_key


class _FakeConditioningModel:
    def __init__(self, embeds, n_embeds=None):
        self.embeds = embeds
        self.n_embeds = n_embeds


class _FakeProgress:
    def step(self, *args, **kwargs):
        pass

    def preview(self, *args, **kwargs):
        pass


class _FakeGenerator:

    def __init__(self):
        self.spec = None
        self.encode_calls = []
        self.sample_calls = []
        self._ref_latent = torch.full((1, 4, 1, 8, 8), 7.0)

    def snap_resolution(self, width, height):
        return max(16, width // 16 * 16), max(16, height // 16 * 16)

    def encode_image(self, pixels, **kwargs):
        self.encode_calls.append(pixels)
        return self._ref_latent

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 4, 1, height // 8, width // 8)

    def sample(self, conditioning, shape, **kwargs):
        self.sample_calls.append({"conditioning": conditioning, "shape": shape, "kwargs": kwargs})
        return torch.zeros(shape)

    def decode(self, latent, **kwargs):
        b = latent.shape[0]
        return np.zeros((b, 64, 64, 3), dtype=np.uint8)


def _ctx_extra(gen, conditioning, images, width=64, height=64):
    return {
        "generator": gen,
        "conditioning": conditioning,
        "steps": 8,
        "guidance": 0.0,
        "sampler": "euler",
        "width": width,
        "height": height,
        "images": images,
    }


class _Ctx:
    def __init__(self, extra, quantity=1):
        self.extra = extra
        self.quantity = quantity
        self.is_cancelled = None


def test_fit_source_image_crop_mode_matches_target_exactly(pipe_module):
    img = Image.new("RGB", (300, 200), color=(10, 20, 30))
    out = pipe_module.fit_source_image(img, width=128, height=64, mode="crop")
    assert out.shape == (64, 128, 3)
    assert out.dtype == np.uint8


def test_fit_source_image_crop_mode_crops_wide_source_for_a_square_target(pipe_module):
    w, h = 400, 100
    img = Image.new("RGB", (w, h), color=(0, 0, 0))
    stripe = Image.new("RGB", (20, h), color=(255, 255, 255))
    img.paste(stripe, (w // 2 - 10, 0))

    out = pipe_module.fit_source_image(img, width=100, height=100, mode="crop")
    assert out.shape == (100, 100, 3)
    center_col = out[:, 50, :]
    edge_col = out[:, 5, :]
    assert center_col.mean() > edge_col.mean()


def test_fit_source_image_upscales_small_source(pipe_module):
    img = Image.new("RGB", (16, 16), color=(50, 60, 70))
    out = pipe_module.fit_source_image(img, width=256, height=256)
    assert out.shape == (256, 256, 3)


def test_fit_source_image_is_a_noop_geometry_for_already_matching_ar(pipe_module):
    img = Image.new("RGB", (64, 64), color=(1, 2, 3))
    out = pipe_module.fit_source_image(img, width=64, height=64)
    assert out.shape == (64, 64, 3)
    assert abs(int(out[..., 0].mean()) - 1) <= 2


def test_fit_mode_fills_exactly_for_matched_ar(pipe_module):
    img = Image.new("RGB", (256, 128), color=(4, 5, 6))
    out = pipe_module.fit_source_image(img, width=128, height=64, mode="fit")
    assert out.shape == (64, 128, 3)


def test_fit_mode_fits_inside_smaller_grid_for_mismatched_ar(pipe_module):
    img = Image.new("RGB", (384, 128), color=(7, 8, 9))
    out = pipe_module.fit_source_image(img, width=128, height=128, mode="fit")
    h, w, _ = out.shape
    assert h <= 128 and w <= 128
    assert h % 16 == 0 and w % 16 == 0
    assert w == 128 and h < 128


def test_crop_and_fit_agree_at_matched_ar(pipe_module):
    img = Image.new("RGB", (200, 200), color=(3, 3, 3))
    fit = pipe_module.fit_source_image(img, width=64, height=64, mode="fit")
    crop = pipe_module.fit_source_image(img, width=64, height=64, mode="crop")
    assert fit.shape == crop.shape == (64, 64, 3)


def _make_pipe(pipe_module, **overrides):
    config = pipe_module.GeneratorKrea2EditPipe.get_default_config()
    config.update(overrides)
    return pipe_module.GeneratorKrea2EditPipe(config)


def test_generate_one_injects_ref_latents_into_cond_and_uncond(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(
        embeds={"context": torch.zeros(1, 5, 3, 16)},
        n_embeds={"context": torch.zeros(1, 5, 3, 16)},
    )
    source = Image.new("RGB", (64, 64), color=(9, 9, 9))
    ctx = _Ctx(_ctx_extra(gen, [cond_model], [source]))
    pipe = _make_pipe(pipe_module)

    out = pipe.generate_one(ctx, index=0, seed=123, progress=_FakeProgress())

    assert isinstance(out, ImageGenerationOutput)
    assert len(gen.sample_calls) == 1
    conditioning_obj = gen.sample_calls[0]["conditioning"]
    assert "ref_latents" in conditioning_obj.cond
    assert torch.equal(conditioning_obj.cond["ref_latents"], gen._ref_latent)
    assert conditioning_obj.uncond is not None
    assert "ref_latents" in conditioning_obj.uncond


def test_generate_one_encodes_the_fitted_source_at_output_resolution(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(embeds={"context": torch.zeros(1, 5, 3, 16)})
    source = Image.new("RGB", (128, 128), color=(1, 1, 1))
    ctx = _Ctx(_ctx_extra(gen, [cond_model], [source], width=64, height=64))
    pipe = _make_pipe(pipe_module)

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    assert len(gen.encode_calls) == 1
    pixels = gen.encode_calls[0]
    assert pixels.shape == (64, 64, 3)


def test_generate_one_threads_ref_boost_dials_into_conditioning(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(
        embeds={"context": torch.zeros(1, 5, 3, 16)},
        n_embeds={"context": torch.zeros(1, 5, 3, 16)},
    )
    source = Image.new("RGB", (64, 64), color=(9, 9, 9))
    ctx = _Ctx(_ctx_extra(gen, [cond_model], [source]))
    pipe = _make_pipe(pipe_module, ref_boost=2.5, ref_boost_a=1.5)

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    cond = gen.sample_calls[0]["conditioning"].cond
    uncond = gen.sample_calls[0]["conditioning"].uncond
    assert cond["ref_boost"] == 2.5 and cond["ref_boost_a"] == 1.5
    assert uncond["ref_boost"] == 2.5 and uncond["ref_boost_a"] == 1.5


def test_generate_one_single_ref_is_a_bare_tensor(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(embeds={"context": torch.zeros(1, 5, 3, 16)})
    source = Image.new("RGB", (64, 64), color=(9, 9, 9))
    ctx = _Ctx(_ctx_extra(gen, [cond_model], [source]))
    pipe = _make_pipe(pipe_module)

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    ref = gen.sample_calls[0]["conditioning"].cond["ref_latents"]
    assert torch.is_tensor(ref)


def test_generate_one_second_image_makes_ref_latents_a_scene_subject_list(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(embeds={"context": torch.zeros(1, 5, 3, 16)})
    scene = Image.new("RGB", (64, 64), color=(1, 1, 1))
    subject = Image.new("RGB", (64, 64), color=(2, 2, 2))
    extra = _ctx_extra(gen, [cond_model], [scene])
    extra["images_b"] = [subject]
    ctx = _Ctx(extra)
    pipe = _make_pipe(pipe_module)

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    ref = gen.sample_calls[0]["conditioning"].cond["ref_latents"]
    assert isinstance(ref, list) and len(ref) == 2
    assert len(gen.encode_calls) == 2


def test_generate_one_raises_without_a_source_image(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(embeds={"context": torch.zeros(1, 5, 3, 16)})
    ctx = _Ctx(_ctx_extra(gen, [cond_model], []))
    pipe = _make_pipe(pipe_module)

    with pytest.raises(GenerationExecutionError):
        pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())


def test_generate_one_does_not_mutate_the_shared_conditioning_model_dict(pipe_module):
    gen = _FakeGenerator()
    embeds = {"context": torch.zeros(1, 5, 3, 16)}
    cond_model = _FakeConditioningModel(embeds=embeds)
    source = Image.new("RGB", (64, 64), color=(2, 2, 2))
    ctx = _Ctx(_ctx_extra(gen, [cond_model], [source]))
    pipe = _make_pipe(pipe_module)

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    assert "ref_latents" not in embeds


def test_generate_one_reuses_last_image_when_fewer_than_quantity(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(embeds={"context": torch.zeros(1, 5, 3, 16)})
    source = Image.new("RGB", (64, 64), color=(3, 3, 3))
    ctx = _Ctx(_ctx_extra(gen, [cond_model], [source]))
    pipe = _make_pipe(pipe_module)

    out = pipe.generate_one(ctx, index=2, seed=1, progress=_FakeProgress())
    assert isinstance(out, ImageGenerationOutput)


def test_fit_ref_mask_matches_fit_source_image_shape_crop_matched_ar(pipe_module):
    img = Image.new("RGB", (256, 128), color=(4, 5, 6))
    mask = Image.new("L", (256, 128), color=0)
    rgb = pipe_module.fit_source_image(img, width=128, height=64, mode="crop")
    m = pipe_module.fit_ref_mask(mask, width=128, height=64, mode="crop")
    assert m.shape[-2:] == rgb.shape[:2]


def test_fit_ref_mask_matches_fit_source_image_shape_fit_mismatched_ar(pipe_module):
    img = Image.new("RGB", (384, 128), color=(7, 8, 9))
    mask = Image.new("L", (384, 128), color=0)
    rgb = pipe_module.fit_source_image(img, width=128, height=128, mode="fit")
    m = pipe_module.fit_ref_mask(mask, width=128, height=128, mode="fit")
    assert m.shape[-2:] == rgb.shape[:2]
    assert rgb.shape[0] < 128


def test_fit_ref_mask_values_are_normalized_to_unit_range(pipe_module):
    mask = Image.new("L", (64, 64), color=255)
    m = pipe_module.fit_ref_mask(mask, width=64, height=64, mode="fit")
    assert torch.is_tensor(m)
    assert m.shape == (1, 1, 64, 64)
    assert torch.allclose(m, torch.ones_like(m))


def test_fit_ref_mask_preserves_a_painted_region_after_crop(pipe_module):
    w, h = 400, 100
    mask = Image.new("L", (w, h), color=0)
    left_half = Image.new("L", (w // 2, h), color=255)
    mask.paste(left_half, (0, 0))
    m = pipe_module.fit_ref_mask(mask, width=100, height=100, mode="crop")
    left_col_mean = m[..., :, :10].mean().item()
    right_col_mean = m[..., :, -10:].mean().item()
    assert left_col_mean > right_col_mean


def test_generate_one_no_mask_uploaded_leaves_ref_boost_mask_unset(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(
        embeds={"context": torch.zeros(1, 5, 3, 16)},
        n_embeds={"context": torch.zeros(1, 5, 3, 16)},
    )
    source = Image.new("RGB", (64, 64), color=(9, 9, 9))
    ctx = _Ctx(_ctx_extra(gen, [cond_model], [source]))
    pipe = _make_pipe(pipe_module, ref_boost=2.5)

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    cond = gen.sample_calls[0]["conditioning"].cond
    uncond = gen.sample_calls[0]["conditioning"].uncond
    assert "ref_boost_mask" not in cond
    assert "ref_boost_mask" not in uncond


def test_generate_one_single_ref_uses_the_mask_input(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(
        embeds={"context": torch.zeros(1, 5, 3, 16)},
        n_embeds={"context": torch.zeros(1, 5, 3, 16)},
    )
    source = Image.new("RGB", (64, 64), color=(9, 9, 9))
    mask = Image.new("L", (64, 64), color=255)
    extra = _ctx_extra(gen, [cond_model], [source])
    extra["masks"] = [mask]
    ctx = _Ctx(extra)
    pipe = _make_pipe(pipe_module, ref_boost=2.5)

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    cond = gen.sample_calls[0]["conditioning"].cond
    uncond = gen.sample_calls[0]["conditioning"].uncond
    assert torch.is_tensor(cond["ref_boost_mask"])
    assert torch.is_tensor(uncond["ref_boost_mask"])
    assert torch.equal(cond["ref_boost_mask"], uncond["ref_boost_mask"])


def test_generate_one_two_refs_prefers_mask_b_over_mask(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(embeds={"context": torch.zeros(1, 5, 3, 16)})
    scene = Image.new("RGB", (64, 64), color=(1, 1, 1))
    subject = Image.new("RGB", (64, 64), color=(2, 2, 2))
    scene_mask = Image.new("L", (64, 64), color=255)
    subject_mask = Image.new("L", (64, 64), color=128)
    extra = _ctx_extra(gen, [cond_model], [scene])
    extra["images_b"] = [subject]
    extra["masks"] = [scene_mask]
    extra["masks_b"] = [subject_mask]
    ctx = _Ctx(extra)
    pipe = _make_pipe(pipe_module, ref_boost=2.5)

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    used_mask = gen.sample_calls[0]["conditioning"].cond["ref_boost_mask"]
    expected = pipe_module.fit_ref_mask(subject_mask, width=64, height=64, mode="fit")
    assert torch.equal(used_mask, expected)


def test_generate_one_two_refs_without_mask_b_leaves_ref_boost_mask_unset(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(embeds={"context": torch.zeros(1, 5, 3, 16)})
    scene = Image.new("RGB", (64, 64), color=(1, 1, 1))
    subject = Image.new("RGB", (64, 64), color=(2, 2, 2))
    scene_mask = Image.new("L", (64, 64), color=255)
    extra = _ctx_extra(gen, [cond_model], [scene])
    extra["images_b"] = [subject]
    extra["masks"] = [scene_mask]
    ctx = _Ctx(extra)
    pipe = _make_pipe(pipe_module, ref_boost=2.5)

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    cond = gen.sample_calls[0]["conditioning"].cond
    assert "ref_boost_mask" not in cond


def test_generate_one_reuses_last_mask_when_fewer_than_quantity(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(embeds={"context": torch.zeros(1, 5, 3, 16)})
    source = Image.new("RGB", (64, 64), color=(3, 3, 3))
    mask = Image.new("L", (64, 64), color=255)
    extra = _ctx_extra(gen, [cond_model], [source])
    extra["masks"] = [mask]
    ctx = _Ctx(extra)
    pipe = _make_pipe(pipe_module, ref_boost=2.0)

    out = pipe.generate_one(ctx, index=2, seed=1, progress=_FakeProgress())
    assert isinstance(out, ImageGenerationOutput)
    cond = gen.sample_calls[0]["conditioning"].cond
    assert torch.is_tensor(cond["ref_boost_mask"])


def test_configuration_has_no_mode_or_denoise_spec(pipe_module):
    specs = pipe_module.GeneratorKrea2EditPipe.configuration()
    names = {s.name for s in specs}
    assert "mode" not in names
    assert "denoise" not in names
    assert all(isinstance(s, PipeConfigSpec) for s in specs)


def test_default_config_has_no_denoise_key(pipe_module):
    assert "denoise" not in pipe_module.GeneratorKrea2EditPipe.get_default_config()


def test_inputs_declare_a_required_image(pipe_module):
    specs = pipe_module.GeneratorKrea2EditPipe.inputs()
    image_spec = next(s for s in specs if s.name == "image")
    assert image_spec.required is True


def test_inputs_declare_optional_masks(pipe_module):
    specs = {s.name: s for s in pipe_module.GeneratorKrea2EditPipe.inputs()}
    assert specs["mask"].required is False
    assert specs["mask_b"].required is False


def test_inputs_declare_the_models_service(pipe_module):
    names = {s.name for s in pipe_module.GeneratorKrea2EditPipe.inputs()}
    assert "MODELS" in names


def test_fit_ref_mask_is_stretched_over_its_source_before_the_crop(pipe_module):
    mask = Image.new("L", (64, 64), 0)
    for x in range(56, 64):
        for y in range(64):
            mask.putpixel((x, y), 255)
    out = pipe_module.fit_ref_mask(mask, width=64, height=64, mode="crop", source_size=(128, 64))
    assert out.shape == (1, 1, 64, 64)
    assert out.max().item() < 0.05
    unscaled = pipe_module.fit_ref_mask(mask, width=64, height=64, mode="crop")
    assert unscaled[0, 0, :, 60:].mean() > 0.9


def test_fit_ref_mask_without_source_size_keeps_its_own_geometry(pipe_module):
    mask = Image.new("L", (50, 50), 255)
    out = pipe_module.fit_ref_mask(mask, width=64, height=64, mode="crop")
    assert out.shape == (1, 1, 64, 64)


def test_generate_one_aligns_a_mask_drawn_at_a_different_size_than_its_source(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(embeds={"context": torch.zeros(1, 5, 3, 16)})
    source = Image.new("RGB", (128, 64), color=(1, 1, 1))
    mask = Image.new("L", (64, 64), 0)
    for x in range(56, 64):
        for y in range(64):
            mask.putpixel((x, y), 255)
    extra = _ctx_extra(gen, [cond_model], [source], width=64, height=64)
    extra["masks"] = [mask]
    pipe = _make_pipe(pipe_module, ref_boost=2.0, fit_mode="crop")

    pipe.generate_one(_Ctx(extra), index=0, seed=1, progress=_FakeProgress())

    placed = gen.sample_calls[0]["conditioning"].cond["ref_boost_mask"]
    assert placed.shape == (1, 1, 64, 64)
    assert placed.max().item() < 0.05


def test_generate_one_threads_reference_placement_into_cond_and_uncond(pipe_module):
    gen = _FakeGenerator()
    cond_model = _FakeConditioningModel(
        embeds={"context": torch.zeros(1, 5, 3, 16)},
        n_embeds={"context": torch.zeros(1, 5, 3, 16)},
    )
    source = Image.new("RGB", (64, 64), color=(9, 9, 9))
    ctx = _Ctx(_ctx_extra(gen, [cond_model], [source]))
    pipe = _make_pipe(pipe_module, ref_position="beside", ref_position_a="aligned")

    pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    conditioning = gen.sample_calls[0]["conditioning"]
    assert conditioning.cond["ref_position"] == "beside"
    assert conditioning.cond["ref_position_a"] == "aligned"
    assert conditioning.uncond["ref_position"] == "beside"
    assert conditioning.uncond["ref_position_a"] == "aligned"


def test_reference_placement_defaults_to_aligned(pipe_module):
    config = pipe_module.GeneratorKrea2EditPipe.get_default_config()
    assert config["ref_position"] == "aligned"
    assert config["ref_position_a"] == "aligned"


def _ostris_extra(gen, source, **over):
    cond_model = _FakeConditioningModel(
        embeds={"context": torch.zeros(1, 5, 3, 16)},
        n_embeds={"context": torch.zeros(1, 5, 3, 16)},
    )
    return _Ctx(_ctx_extra(gen, [cond_model], [source], **over))


def test_ostris_style_turns_on_timestep_zero_isolated_origin_references(pipe_module):
    gen = _FakeGenerator()
    pipe = _make_pipe(pipe_module, ref_style="ostris", ref_position="beside", ref_boost=3.0)

    pipe.generate_one(_ostris_extra(gen, Image.new("RGB", (64, 64))), index=0, seed=1, progress=_FakeProgress())

    sent = gen.sample_calls[0]["conditioning"]
    for branch in (sent.cond, sent.uncond):
        assert branch["ref_timestep_zero"] is True
        assert branch["ref_isolated"] is True
        assert branch["ref_position"] == "origin"
        assert branch["ref_position_a"] == "origin"


def test_standard_style_never_sets_the_ostris_flags(pipe_module):
    gen = _FakeGenerator()
    pipe = _make_pipe(pipe_module)

    pipe.generate_one(_ostris_extra(gen, Image.new("RGB", (64, 64))), index=0, seed=1, progress=_FakeProgress())

    sent = gen.sample_calls[0]["conditioning"]
    assert "ref_timestep_zero" not in sent.cond and "ref_isolated" not in sent.cond
    assert sent.cond["ref_position"] == "aligned"


def test_ostris_style_encodes_the_reference_at_its_own_aspect_capped_by_area(pipe_module):
    gen = _FakeGenerator()
    pipe = _make_pipe(pipe_module, ref_style="ostris", ref_pixels=64 * 64)

    pipe.generate_one(_ostris_extra(gen, Image.new("RGB", (256, 128))), index=0, seed=1, progress=_FakeProgress())

    assert gen.encode_calls[0].shape[:2] == (48, 96)


def test_ostris_style_sizes_the_output_from_the_source_when_asked(pipe_module):
    gen = _FakeGenerator()
    pipe = _make_pipe(pipe_module, ref_style="ostris", output_pixels=96 * 96)

    out = pipe.generate_one(_ostris_extra(gen, Image.new("RGB", (192, 96))), index=0, seed=1, progress=_FakeProgress())

    assert out.resolution == (128, 64)
    assert gen.sample_calls[0]["shape"][-2:] == (8, 16)


def test_ostris_style_keeps_the_resolution_when_output_pixels_is_zero(pipe_module):
    gen = _FakeGenerator()
    pipe = _make_pipe(pipe_module, ref_style="ostris")

    out = pipe.generate_one(_ostris_extra(gen, Image.new("RGB", (192, 96))), index=0, seed=1, progress=_FakeProgress())

    assert out.resolution == (64, 64)


def test_ostris_style_ignores_the_boost_mask(pipe_module):
    gen = _FakeGenerator()
    extra = _ostris_extra(gen, Image.new("RGB", (64, 64)))
    extra.extra["masks"] = [Image.new("L", (64, 64), 255)]
    pipe = _make_pipe(pipe_module, ref_style="ostris")

    pipe.generate_one(extra, index=0, seed=1, progress=_FakeProgress())

    assert "ref_boost_mask" not in gen.sample_calls[0]["conditioning"].cond


def test_area_fit_never_upscales_and_snaps_to_sixteen(pipe_module):
    assert pipe_module.area_fit_size(100, 50, 10_000_000) == (96, 48)
    assert pipe_module.area_fit_size(2048, 1024, 512 * 512) == (720, 368)
    assert pipe_module.area_fit_size(8, 8, 10_000) == (16, 16)


def test_ostris_style_is_the_standard_default_off(pipe_module):
    config = pipe_module.GeneratorKrea2EditPipe.get_default_config()
    assert config["ref_style"] == "standard"
    assert config["output_pixels"] == 0
