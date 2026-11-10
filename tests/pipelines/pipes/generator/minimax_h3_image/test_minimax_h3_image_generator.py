from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.generator.minimax_h3_image.engine import MiniMaxH3ImageNativeGenerator
from src.pipelines.pipes.generator.minimax_h3_image.main import GeneratorMiniMaxH3ImagePipe
from src.pipelines.pipes.generator.minimax_h3_image.references import (
    resolve_canvas,
    vae_reference_size,
    vision_reference_size,
)
from src.platform.runtime.native.arch.minimax_h3.image import ReferenceLatents
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.kernels import pick_operations
from src.platform.runtime.native.vae.minimax_h3_image import MiniMaxH3ImageVAE

GRID = (1.0, 0.9675236940383911, 0.8875485062599182, 0.75, 0.5327739119529724, 0.23200887441635132, 0.0)
FLOW = "src.pipelines.pipes._shared.generation.flow_generator_pipe"
MAIN = "src.pipelines.pipes.generator.minimax_h3_image.main"


@dataclass(frozen=True)
class _FakeSpec:
    family: str = "minimax_h3_image"
    variant: str = "h3_image"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 24})
    sampling_settings: dict = field(default_factory=lambda: {"guidance": "flow_map", "schedule": "h3_image_pdd"})


class _FakeGenerator:
    instances: list["_FakeGenerator"] = []
    family = "minimax_h3_image"

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.dit = dit
        self.spec = _FakeSpec(family=_FakeGenerator.family)
        self.sample_calls = []
        self.encoded = []
        _FakeGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        return (width // 32 * 32, height // 32 * 32)

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 24, 1, height // 16, width // 16)

    def encode_reference(self, image):
        self.encoded.append(image.size)
        width, height = image.size
        return torch.ones(1, 24, 1, height // 16, width // 16)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, latent.shape[-2] * 16, latent.shape[-1] * 16, 3), dtype=np.uint8)


def _bundle():
    module = SimpleNamespace(config=SimpleNamespace(pdd_sigmas=GRID, patch_size=(1, 2, 2)))
    return SimpleNamespace(dit=SimpleNamespace(estimated_vram_gb=21.0, module=module), te_encoder=object(), vae=object())


def _input(quantity=1, pictures=()):
    cond = SimpleNamespace(embeds={"context": torch.ones(1, 4, 8), "token_tags": torch.ones(4, dtype=torch.long)},
                           n_embeds={})
    return PipeInput(input={
        "model": _bundle(), "conditioning": [cond] * quantity, "seed": list(range(11, 11 + quantity)),
        "reference_images": list(pictures),
    })


def _pipe(**over):
    config = GeneratorMiniMaxH3ImagePipe.get_default_config()
    config.update(over)
    return GeneratorMiniMaxH3ImagePipe(config=config)


@pytest.fixture(autouse=True)
def _patched():
    _FakeGenerator.instances.clear()
    _FakeGenerator.family = "minimax_h3_image"
    with patch(f"{FLOW}.make_device_plan", lambda **_: None), patch(f"{MAIN}.MiniMaxH3ImageNativeGenerator", _FakeGenerator):
        yield


def _calls():
    return _FakeGenerator.instances[-1].sample_calls


def test_text_to_image_runs_six_euler_steps_on_the_checkpoint_grid_without_guidance():
    _pipe().process(_input(), lambda o: None)
    call = _calls()[-1]
    assert call["steps"] == 6
    assert call["sampler"] == "euler"
    assert call["cfg_scale"] == 1.0
    assert call["step_cache_options"] is None
    assert call["schedule_settings"] == {"schedule": "h3_image_pdd", "schedule_options": {"grid": list(GRID)}}
    assert call["latents_shape"] == (1, 24, 1, 128, 128)
    assert call["conditioning"].uncond is None
    assert "ref_latents" not in call["conditioning"].cond


def test_a_preset_cannot_switch_the_sampler_or_add_guidance():
    pipe = _pipe(guidance=4.0)
    pipe.config["sampler"] = "res_multistep"
    pipe.process(_input(), lambda o: None)
    assert _calls()[-1]["cfg_scale"] == 1.0
    assert _calls()[-1]["sampler"] == "euler"


def test_edit_pictures_are_encoded_once_and_attached_with_seeded_noise():
    pictures = [Image.new("RGB", (800, 600), "red"), Image.new("RGB", (3000, 3000), "blue")]
    _pipe(mode="edit", resolution="auto", quantity=2).process(_input(quantity=2, pictures=pictures), lambda o: None)
    generator = _FakeGenerator.instances[-1]
    assert generator.encoded == [(1376, 1024), (2048, 2048)]
    first, second = (call["conditioning"].cond["ref_latents"] for call in generator.sample_calls)
    assert isinstance(first, ReferenceLatents)
    assert [tuple(z.shape) for z in first.latents] == [(1, 24, 1, 64, 86), (1, 24, 1, 128, 128)]
    assert not torch.equal(first.latents[0], second.latents[0])
    assert torch.allclose(first.latents[0], torch.ones_like(first.latents[0]), atol=0.01)
    assert generator.sample_calls[0]["latents_shape"] == (1, 24, 1, 64, 86)


def test_an_explicit_resolution_wins_over_picture_one():
    _pipe(mode="edit", resolution="2368x1760").process(_input(pictures=[Image.new("RGB", (800, 600))]), lambda o: None)
    assert _calls()[-1]["latents_shape"] == (1, 24, 1, 110, 148)


def test_more_than_sixteen_pictures_are_refused():
    with pytest.raises(ValueError, match="16"):
        _pipe(mode="edit").build_context(_input(pictures=[Image.new("RGB", (64, 64))] * 17))


def test_a_video_dit_in_this_preset_is_refused():
    _FakeGenerator.family = "minimax_h3"
    with pytest.raises(NativeEngineUnsupportedError, match="MiniMax-H3-Image DiT"):
        _pipe().build_context(_input())


@pytest.mark.parametrize("size, vae, vision", [
    ((800, 600), (1376, 1024), (1376, 1024)),
    ((3000, 3000), (2048, 2048), (1024, 1024)),
    ((1500, 1200), (1504, 1216), (1280, 1024)),
])
def test_pictures_are_sized_like_the_reference_node(size, vae, vision):
    assert vae_reference_size(*size) == vae
    assert vision_reference_size(*size) == vision


def test_auto_resolution_uses_picture_one_or_the_default_canvas():
    assert resolve_canvas("auto", []) == (2048, 2048)
    assert resolve_canvas("auto", [Image.new("RGB", (800, 600))]) == (1376, 1024)
    assert resolve_canvas("2720x1536", [Image.new("RGB", (800, 600))]) == (2720, 1536)


def _real_generator(vae):
    generator = object.__new__(MiniMaxH3ImageNativeGenerator)
    generator.spec = _FakeSpec()
    generator.placement = None
    generator.device_plan = SimpleNamespace(vae_device="cpu")
    generator.vae = SimpleNamespace(move_to=lambda device: None, module=vae, compute_dtype=torch.float32)
    generator.dit = SimpleNamespace(offload=lambda: None)
    return generator


def _tiny_vae():
    with torch.device("meta"):
        vae = MiniMaxH3ImageVAE(decoder_num_layers=1, decoder_num_attention_heads=2, decoder_attention_head_dim=16,
                                operations=pick_operations(torch.float32, torch.float32))
    vae.drop_encoder()
    vae.to_empty(device="cpu")
    with torch.no_grad():
        for parameter in vae.parameters():
            parameter.copy_(torch.randn(parameter.shape, generator=torch.Generator().manual_seed(1)) * 0.1)
    vae.post_load()
    return vae.eval()


def test_the_image_generator_decodes_one_frame_to_an_rgb_array():
    generator = _real_generator(_tiny_vae())
    assert generator.latent_shape_for(2048, 1536) == (1, 24, 1, 96, 128)
    pixels = generator.decode(torch.zeros(1, 24, 1, 2, 3))
    assert pixels.shape == (1, 32, 48, 3)
    assert pixels.dtype == np.uint8


def test_a_picture_is_encoded_imagenet_normalised_with_the_fixed_posterior_seed():
    seen = {}

    class _Encoder:
        latents_mean = torch.zeros(24)

        def encode(self, x, *, sample_posterior, generator):
            seen.update(x=x, sample_posterior=sample_posterior, draw=torch.randn(1, generator=generator))
            return torch.full((1, 24, 1, x.shape[-2] // 16, x.shape[-1] // 16), 2.0)

        def normalize(self, latent):
            return latent - 1.0

    latent = _real_generator(_Encoder()).encode_reference(Image.new("RGB", (32, 48), (255, 0, 0)))
    assert latent.shape == (1, 24, 1, 3, 2)
    assert torch.all(latent == 1.0)
    assert seen["sample_posterior"] is True
    assert torch.equal(seen["draw"], torch.randn(1, generator=torch.Generator().manual_seed(42)))
    red = seen["x"][0, :, 0, 0, 0]
    assert torch.allclose(red, torch.tensor([(1.0 - 0.485) / 0.229, (0.0 - 0.456) / 0.224, (0.0 - 0.406) / 0.225]))
