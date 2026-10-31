from __future__ import annotations

import types

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes._shared.generation.img2img import Img2ImgGeneratorMixin
from src.pipelines.pipes._shared.imaging.inpaint_mask import (
    composite_pixels,
    latent_mask_from_array,
    mask_to_unit_array,
)
from src.pipelines.pipes.generator.krea2.main import GeneratorKrea2Pipe
from src.pipelines.pipes.generator.moss_image.main import GeneratorMossImagePipe
from src.pipelines.pipes.generator.qwen_image21.main import GeneratorQwenImage21Pipe
from src.pipelines.pipes.generator.z_image.main import GeneratorZImagePipe


class _Gen:
    def __init__(self, latent_shape=(1, 4, 1, 4, 4)):
        self.latent_shape = latent_shape
        self.sample_kw = None

    def snap_resolution(self, w, h):
        return w, h

    def encode_image(self, image, vram_free_gb=None):
        self.encoded = np.asarray(image)
        return torch.zeros(self.latent_shape)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_kw = kw
        return "latent"

    def decode(self, latent, vram_free_gb=None):
        return np.full((1, 16, 16, 3), 200, dtype=np.uint8)


class _Pipe(Img2ImgGeneratorMixin):
    def __init__(self):
        self.config = {"preview": False}


class _Progress:
    def step(self, *a, **kw):
        pass


def _ctx(mode="inpaint", images=None, masks=None):
    return types.SimpleNamespace(
        quantity=1,
        is_cancelled=lambda: False,
        extra={
            "mode": mode, "denoise": 1.0, "images": images, "masks": masks, "steps": 4,
            "guidance": 1.0, "sampler": "euler", "width": 16, "height": 16,
        },
    )


def _source():
    return Image.new("RGB", (16, 16), (10, 20, 30))


def _mask(box=(8, 0, 16, 16)):
    image = Image.new("L", (16, 16), 0)
    image.paste(255, box)
    return image


def test_other_modes_fall_through():
    out = _Pipe().maybe_inpaint(_Gen(), "COND", _ctx("img2img", [_source()], [_mask()]), 0, 1, _Progress())
    assert out is None


def test_missing_picture_or_mask_is_a_clear_error():
    with pytest.raises(GenerationExecutionError, match="picture"):
        _Pipe().maybe_inpaint(_Gen(), "COND", _ctx(images=[], masks=[_mask()]), 0, 1, _Progress())
    with pytest.raises(GenerationExecutionError, match="Paint"):
        _Pipe().maybe_inpaint(_Gen(), "COND", _ctx(images=[_source()], masks=[]), 0, 1, _Progress())


def test_mask_reaches_sample_as_latent_mask_and_kept_pixels_stay_exact():
    gen = _Gen()
    out = _Pipe().maybe_inpaint(gen, "COND", _ctx(images=[_source()], masks=[_mask()]), 0, 1, _Progress())
    mask = gen.sample_kw["latent_mask"]
    assert tuple(mask.shape) == (1, 1, 1, 4, 4)
    assert mask[..., :2].sum() == 0 and mask[..., 2:].min() == 1
    assert gen.sample_kw["init_latent"].shape == gen.latent_shape
    pixels = np.asarray(out.image)
    assert (pixels[:, :8] == (10, 20, 30)).all()
    assert (pixels[:, 8:] == 200).all()


def test_four_dim_latents_get_four_dim_mask():
    gen = _Gen((1, 4, 4, 4))
    _Pipe().maybe_inpaint(gen, "COND", _ctx(images=[_source()], masks=[_mask()]), 0, 1, _Progress())
    assert tuple(gen.sample_kw["latent_mask"].shape) == (1, 1, 4, 4)


def test_a_single_masked_pixel_repaints_its_whole_latent_cell():
    array = np.zeros((16, 16), dtype=np.float32)
    array[5, 5] = 1.0
    mask = latent_mask_from_array(array, (1, 4, 4, 4))
    assert mask.sum() == 1 and mask[0, 0, 1, 1] == 1


def test_mask_is_resized_to_the_picture():
    array = mask_to_unit_array(Image.new("L", (4, 4), 255), (16, 8))
    assert array.shape == (8, 16) and array.min() == 1.0


def test_composite_pixels_keeps_source_where_mask_is_zero_and_blends_soft_edges():
    source = np.full((1, 3, 3), 100, dtype=np.uint8)
    generated = np.full((1, 3, 3), 200, dtype=np.uint8)
    out = composite_pixels(generated, source, np.array([[0.0, 0.5, 1.0]], dtype=np.float32))
    assert out[0, :, 0].tolist() == [100, 150, 200]


@pytest.mark.parametrize("pipe", [GeneratorKrea2Pipe, GeneratorMossImagePipe, GeneratorQwenImage21Pipe, GeneratorZImagePipe])
def test_family_generators_expose_inpaint(pipe):
    mode = next(spec for spec in pipe.configuration() if spec.name == "mode")
    assert "inpaint" in mode.choices
    names = {spec.name for spec in pipe.inputs()}
    assert {"image", "mask"} <= names
    assert any(spec.name == "denoise" for spec in pipe.configuration())


class _EchoGen:
    def __init__(self):
        self.shape = None

    def snap_resolution(self, w, h):
        return round(w / 16) * 16, round(h / 16) * 16

    def encode_image(self, image, vram_free_gb=None):
        self.shape = np.asarray(image).shape
        return torch.zeros(1, 4, 1, self.shape[0] // 8, self.shape[1] // 8)

    def sample(self, conditioning, latents_shape, **kw):
        return "latent"

    def decode(self, latent, vram_free_gb=None):
        return np.full((1, *self.shape), 250, dtype=np.uint8)


def test_crop_generate_restore_chain_changes_only_the_painted_area():
    from src.pipelines.contracts import PipeInput
    from src.pipelines.pipes.inpaint_region_crop.main import InpaintRegionCropPipe
    from src.pipelines.pipes.inpaint_region_restore.main import InpaintRegionRestorePipe

    rng = np.random.default_rng(1)
    original = Image.fromarray(rng.integers(0, 120, (192, 256, 3), dtype=np.uint8))
    painted = np.zeros((192, 256), dtype=np.uint8)
    painted[64:128, 96:160] = 255
    crop = InpaintRegionCropPipe({"strategy": "scaled", "multiple": 16, "working_area": 128 * 128}).process(
        PipeInput(input={"image": original, "mask": Image.fromarray(painted)}), None
    ).output

    gen = _EchoGen()
    ctx = _ctx(images=[crop["image"]], masks=[crop["mask"]])
    ctx.extra["width"], ctx.extra["height"] = crop["image"].size
    result = _Pipe().maybe_inpaint(gen, "COND", ctx, 0, 1, _Progress())

    restored = InpaintRegionRestorePipe({"strategy": "composite", "feather": 0.05, "color_match": False}).process(
        PipeInput(input={
            "inpainted_image": [result.image], "crop_region": crop["crop_region"],
            "original_image": crop["original_image"], "original_mask": crop["original_mask"],
        }), None
    ).output["image"]

    before, after = np.asarray(original), np.asarray(restored[0] if isinstance(restored, (list, tuple)) else restored).reshape(192, 256, 3)
    assert after.shape == before.shape
    assert np.array_equal(after[:32], before[:32])
    assert np.array_equal(after[:, :48], before[:, :48])
    assert (after[88:104, 112:144] > 200).all()
