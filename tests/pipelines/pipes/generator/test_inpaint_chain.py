from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.canvas_expand.main import CanvasExpandPipe
from src.pipelines.pipes.generator.krea2.main import GeneratorKrea2Pipe
from src.pipelines.pipes.generator.moss_image.main import GeneratorMossImagePipe
from src.pipelines.pipes.generator.qwen_image21.main import GeneratorQwenImage21Pipe
from src.pipelines.pipes.generator.z_image.main import GeneratorZImagePipe
from src.pipelines.pipes.inpaint_region_crop.main import InpaintRegionCropPipe
from src.pipelines.pipes.inpaint_region_restore.main import InpaintRegionRestorePipe
from src.platform.runtime.native.sampling.denoise_loop import denoise

SOURCE_TONE = 200
TONE_OFFSET = 18


@dataclass(frozen=True)
class _Spec:
    family: str = "fake"
    variant: str = "fake"
    sampling_settings: dict = field(default_factory=lambda: {"shift": 2.0, "guidance": None, "fixed_mu": 1.15})


def _fake_generator(five_d: bool, channels: int):
    class Fake:
        last = None

        def __init__(self, dit, te, vae, device_plan=None, **_):
            self.spec = _Spec()
            self.masks = []
            Fake.last = self

        def snap_resolution(self, width, height):
            return max(16, round(width / 16) * 16), max(16, round(height / 16) * 16)

        def latent_shape_for(self, width, height, batch=1):
            cells = (height // 16, width // 16)
            return (batch, 4, 1, *cells) if five_d else (batch, 4, *cells)

        def encode_image(self, pixels, **_):
            self.size = pixels.shape[:2]
            return torch.zeros(self.latent_shape_for(self.size[1], self.size[0]))

        def sample(self, conditioning, latents_shape, **kw):
            self.masks.append(kw.get("latent_mask"))
            target = torch.full(latents_shape, 0.5)

            def forward(x, sigma, cond):
                return (x.float() - target) / sigma.reshape(-1)[0].float()

            return denoise(
                forward, kw["init_latent"], {}, None, steps=3, sampling_settings=self.spec.sampling_settings,
                guidance_scale=0.0, seed_noise=torch.randn(latents_shape), latent_mask=kw.get("latent_mask"),
            )

        def decode(self, latent, **_):
            pixels = np.full((1, *self.size, channels), SOURCE_TONE + TONE_OFFSET, dtype=np.uint8)
            if channels == 4:
                pixels[..., 3] = 255
            return pixels

    return Fake


FAMILIES = {
    "z_image": (GeneratorZImagePipe, False, 3),
    "moss": (GeneratorMossImagePipe, False, 3),
    "krea2": (GeneratorKrea2Pipe, True, 3),
    "qwen21": (GeneratorQwenImage21Pipe, True, 4),
}


def _cond():
    embeds = {"context": torch.ones(1, 4, 8), "attention_mask": torch.ones(1, 4)}
    return SimpleNamespace(embeds=embeds, n_embeds={})


def _scene():
    original = Image.fromarray(np.full((256, 384, 3), SOURCE_TONE, dtype=np.uint8))
    painted = np.zeros((256, 384), dtype=np.uint8)
    painted[80:176, 140:260] = 255
    return original, Image.fromarray(painted)


def _run_chain(family, wire=lambda crop: (crop["images"], crop["masks"])):
    cls, five_d, channels = FAMILIES[family]
    original, mask = _scene()
    crop = InpaintRegionCropPipe({"strategy": "scaled", "multiple": 16, "working_area": 192 * 192}).process(
        PipeInput(input={"image": original, "mask": mask}), None
    ).output
    images, masks = wire(crop)
    config = {**cls.get_default_config(), "mode": "inpaint", "denoise": 1.0, "preview": False, "steps": 3}
    fake = _fake_generator(five_d, channels)
    with patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None), \
            patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", fake):
        out = cls(config=config).process(
            PipeInput(input={
                "model": SimpleNamespace(dit=SimpleNamespace(estimated_vram_gb=1.0), te_encoder=object(), vae=object()),
                "conditioning": [_cond()], "seed": [7], "image": images, "mask": masks,
            }),
            lambda *_: None,
        ).output
    restored = InpaintRegionRestorePipe({"strategy": "inpaint", "feather": 0.12, "color_match": True}).process(
        PipeInput(input={
            "inpainted_image": out["image"], "crop_region": crop["crop_region"],
            "original_image": crop["original_image"], "original_mask": crop["original_mask"],
        }), None
    ).output["image"]
    return np.asarray(restored[0]).astype(int), fake.last


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_real_chain_runs_and_only_the_painted_area_changes(family):
    result, gen = _run_chain(family)
    assert result.shape == (256, 384, 3)
    assert gen.masks[0] is not None and float(gen.masks[0].max()) == 1.0
    assert (result[:60] == SOURCE_TONE).all() and (result[:, :100] == SOURCE_TONE).all()
    assert (result[120:136, 190:210] > SOURCE_TONE + TONE_OFFSET // 2).all()


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_repaint_with_a_tone_offset_leaves_no_step_at_the_mask_edge(family):
    result, _ = _run_chain(family)
    row = result[128, :, 0]
    assert np.abs(np.diff(row)).max() <= 2
    column = result[:, 200, 0]
    assert np.abs(np.diff(column)).max() <= 2
    assert abs(int(result[128, 139, 0]) - SOURCE_TONE) <= 1
    assert result[128, 156, 0] - SOURCE_TONE <= TONE_OFFSET * 0.6
    assert result[128, 200, 0] - SOURCE_TONE >= TONE_OFFSET * 0.8


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_single_image_and_mask_instead_of_lists_still_runs(family):
    result, _ = _run_chain(family, wire=lambda crop: (crop["image"], crop["mask"]))
    assert result.shape == (256, 384, 3)


def test_outpaint_chain_keeps_the_source_native_and_blends_the_seam():
    cls, five_d, channels = FAMILIES["z_image"]
    source = Image.fromarray(np.full((320, 240, 3), SOURCE_TONE, dtype=np.uint8))
    expand = CanvasExpandPipe({"aspect": "16:9", "overlap": 32}).process(PipeInput(input={"image": source}), None).output
    config = {**cls.get_default_config(), "mode": "inpaint", "denoise": 1.0, "preview": False, "steps": 3,
              "max_pixels": 4194304}
    fake = _fake_generator(five_d, channels)
    with patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.make_device_plan", lambda **_: None), \
            patch("src.pipelines.pipes._shared.generation.flow_generator_pipe.NativeGenerator", fake):
        out = cls(config=config).process(
            PipeInput(input={
                "model": SimpleNamespace(dit=SimpleNamespace(estimated_vram_gb=1.0), te_encoder=object(), vae=object()),
                "conditioning": [_cond()], "seed": [7], "image": expand["images"], "mask": expand["masks"],
            }),
            lambda *_: None,
        ).output
    assert fake.last.size == (320, 576)
    restored = InpaintRegionRestorePipe({"strategy": "inpaint", "feather_px": 32}).process(
        PipeInput(input={
            "inpainted_image": out["image"], "crop_region": expand["crop_region"],
            "original_image": expand["original_image"], "original_mask": expand["original_mask"],
        }), None
    ).output["image"]
    result = np.asarray(restored[0]).astype(int)
    assert result.shape[:2] == expand["image"].size[::-1]
    left = (result.shape[1] - 240) // 2
    row = result[result.shape[0] // 2, :, 0]
    assert np.abs(np.diff(row)).max() <= 2
    assert (result[:, left + 60 : left + 180, 0] == SOURCE_TONE).all()
