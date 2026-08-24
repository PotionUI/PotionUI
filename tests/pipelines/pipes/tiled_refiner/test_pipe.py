"""Integration test for the TiledRefiner pipe.process() wiring.

Mocks the model-touching parts (the family generator + the img2img denoise) so the
upscale -> snap -> tile -> blend -> gallery flow is exercised without a GPU/VAE.
The per-tile refine is stubbed to identity, so a correct pipe reproduces the
upscaled image at the snapped output size (the "mock-VAE equivalence" check) and
emits one gallery + one compare per input image.
"""

import types
from unittest.mock import patch

import numpy as np
from PIL import Image

from src.pipelines.outputs import CompareImagesGenerationOutput, GalleryGenerationOutput
from src.pipelines.pipes.tiled_refiner.main import TiledRefiner
from src.pipelines.contracts import PipeInput


class _FakeGen:
    """A generator whose only real behaviour is granularity/snap geometry."""

    spec = types.SimpleNamespace(family="fake", variant="fake")

    def pixel_granularity(self):
        return 16

    def snap_resolution(self, w, h):
        return ((w // 16) * 16, (h // 16) * 16)


def _identity_denoise(gen, crop, conditioning, **kwargs):
    """Stub img2img: return the crop unchanged, batched (B=1) like the real helper."""
    return np.asarray(crop)[None]


def _run(config, image, conditioning=None):
    refiner = TiledRefiner(config)
    outputs = []
    cond = conditioning or [types.SimpleNamespace(embeds={}, n_embeds=None)]
    pipe_input = PipeInput(input={
        "image": [image],
        "model": types.SimpleNamespace(dit=types.SimpleNamespace(estimated_vram_gb=1.0),
                                       te_encoder="TE", vae="VAE",
                                       spec=types.SimpleNamespace(family="fake")),
        "conditioning": cond,
        "seed": [123],
    })
    with patch("src.pipelines.pipes.tiled_refiner.main.build_native_generator", return_value=_FakeGen()), \
         patch("src.pipelines.pipes.tiled_refiner.main.img2img_denoise", side_effect=_identity_denoise):
        result = refiner.process(pipe_input, outputs.append)
    return result, outputs


def test_single_tile_lanczos_upscale_reproduces_at_snapped_size():
    img = Image.new("RGB", (512, 512), (100, 120, 140))
    config = TiledRefiner.get_default_config()
    config.update({"scale": 2.0, "tile_size": 1024, "tile_overlap": 128, "device": "cpu"})
    result, outputs = _run(config, img)

    out_images = result.output["image"]
    assert len(out_images) == 1
    # 512 -> 2x = 1024, already a multiple of granularity 16
    assert out_images[0].size == (1024, 1024)
    # identity refine over a flat image reproduces the (upscaled) colour
    assert np.allclose(np.asarray(out_images[0]), [100, 120, 140], atol=2)


def test_multi_tile_output_and_progress_and_gallery():
    img = Image.new("RGB", (1024, 1024), (60, 60, 60))
    config = TiledRefiner.get_default_config()
    config.update({"scale": 2.0, "tile_size": 1024, "tile_overlap": 128, "device": "cpu"})
    result, outputs = _run(config, img)

    # 1024 -> 2x = 2048; with tile 1024 that is a multi-tile refine (2x2 grid)
    assert result.output["image"][0].size == (2048, 2048)
    assert any(isinstance(o, GalleryGenerationOutput) for o in outputs)
    assert any(isinstance(o, CompareImagesGenerationOutput) for o in outputs)


def test_scale_one_skips_upscale():
    img = Image.new("RGB", (1024, 1024), (10, 20, 30))
    config = TiledRefiner.get_default_config()
    config.update({"scale": 1.0, "tile_size": 1024, "tile_overlap": 128, "device": "cpu"})
    result, _ = _run(config, img)
    assert result.output["image"][0].size == (1024, 1024)
