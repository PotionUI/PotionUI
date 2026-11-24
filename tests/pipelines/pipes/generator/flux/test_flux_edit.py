from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes.generator.flux.main import GeneratorFluxPipe

FLOW = "src.pipelines.pipes._shared.generation.flow_generator_pipe"
VARIANT = {"value": "flux2"}


@dataclass(frozen=True)
class _FakeSpec:
    family: str = "flux"
    variant: str = "flux2"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 32})
    sampling_settings: dict = field(default_factory=lambda: {"shift": 2.02, "guidance": "none"})


class _FakeGenerator:
    instances: list["_FakeGenerator"] = []

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.spec = _FakeSpec(variant=VARIANT["value"])
        self.sample_calls = []
        self.encoded = []
        self._shape = (1, 128, 64, 64)
        _FakeGenerator.instances.append(self)

    def snap_resolution(self, width, height):
        return (max(16, round(width / 16) * 16), max(16, round(height / 16) * 16))

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 128, height // 16, width // 16)

    def sample(self, conditioning, latents_shape, **kw):
        self.sample_calls.append({"conditioning": conditioning, "latents_shape": latents_shape, **kw})
        self._shape = latents_shape
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, self._shape[2] * 16, self._shape[3] * 16, 3), dtype=np.uint8)

    def encode_image(self, pixels, **_):
        self.encoded.append(pixels.shape)
        return torch.zeros(1, 128, pixels.shape[0] // 16, pixels.shape[1] // 16)


def _cond_model(with_negative=False):
    embeds = {"context": torch.ones(1, 4, 8), "attention_mask": None}
    n_embeds = {"context": torch.ones(1, 4, 8), "attention_mask": None} if with_negative else {}
    return SimpleNamespace(embeds=embeds, n_embeds=n_embeds)


def _input(quantity=1, images=None, with_negative=False):
    inp = {
        "model": SimpleNamespace(dit=SimpleNamespace(estimated_vram_gb=9.0), te_encoder=object(), vae=object()),
        "conditioning": [_cond_model(with_negative) for _ in range(quantity)],
        "seed": list(range(1, quantity + 1)),
    }
    if images is not None:
        inp["image"] = list(images)
    return PipeInput(input=inp)


def _edit(**over):
    cfg = GeneratorFluxPipe.get_default_config()
    cfg.update({"mode": "edit", **over})
    return GeneratorFluxPipe(config=cfg)


def _last_call():
    return _FakeGenerator.instances[-1].sample_calls[-1]


def setup_function(_):
    _FakeGenerator.instances.clear()
    VARIANT["value"] = "flux2"


@pytest.fixture(autouse=True)
def _patched():
    with patch(f"{FLOW}.make_device_plan", lambda **_: None), patch(f"{FLOW}.NativeGenerator", _FakeGenerator):
        yield


def test_edit_is_a_declared_mode_and_output_size_defaults_to_match():
    specs = {s.name: s for s in GeneratorFluxPipe.configuration()}
    assert "edit" in specs["mode"].choices
    assert specs["output_size"].default == ""


def test_flux1_refuses_edit_before_sampling():
    VARIANT["value"] = "flux1"
    with pytest.raises(GenerationExecutionError, match="FLUX.2 Klein"):
        _edit().build_context(_input(images=[Image.new("RGB", (256, 256))]))
    assert not _FakeGenerator.instances[-1].sample_calls


def test_flux1_still_renders_txt2img():
    VARIANT["value"] = "flux1"
    cfg = GeneratorFluxPipe.get_default_config()
    GeneratorFluxPipe(config=cfg).process(_input(), lambda o: None)
    assert _last_call()["latents_shape"] == (1, 128, 64, 64)


def test_edit_without_a_reference_is_an_error():
    with pytest.raises(GenerationExecutionError, match="at least one reference"):
        _edit().process(_input(images=[]), lambda o: None)


def test_edit_attaches_every_reference_latent_in_order():
    sources = [Image.new("RGB", (640, 480)), Image.new("RGB", (320, 320)), Image.new("RGB", (256, 512))]
    _edit().process(_input(images=sources), lambda o: None)
    refs = _last_call()["conditioning"].cond["ref_latents"]
    assert [tuple(r.shape[-2:]) for r in refs] == [(30, 40), (20, 20), (32, 16)]
    assert _last_call()["latents_shape"] == (1, 128, 30, 40)


def test_edit_uses_at_most_four_references():
    _edit().process(_input(images=[Image.new("RGB", (64, 64)) for _ in range(6)]), lambda o: None)
    assert len(_last_call()["conditioning"].cond["ref_latents"]) == 4
    assert len(_FakeGenerator.instances[-1].encoded) == 4


def test_edit_single_reference_pads_then_crops_back():
    result = _edit().process(_input(images=[Image.new("RGB", (1000, 600))]), lambda o: None)
    assert _last_call()["latents_shape"] == (1, 128, 608 // 16, 1008 // 16)
    assert result.output["image"][0].size == (1000, 600)


def test_edit_explicit_output_size_overrides_the_match():
    result = _edit(output_size="512x768").process(_input(images=[Image.new("RGB", (1000, 600))]), lambda o: None)
    assert _last_call()["latents_shape"] == (1, 128, 768 // 16, 512 // 16)
    assert result.output["image"][0].size == (512, 768)


def test_edit_match_keyword_means_no_override():
    result = _edit(output_size="match").process(_input(images=[Image.new("RGB", (1008, 640))]), lambda o: None)
    assert result.output["image"][0].size == (1008, 640)


def test_edit_encodes_references_once_per_generation():
    _edit(quantity=3).process(_input(quantity=3, images=[Image.new("RGB", (256, 256))]), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert len(gen.sample_calls) == 3
    assert len(gen.encoded) == 1


def test_edit_has_no_uncond_and_never_warm_starts_without_a_negative():
    _edit(iterate_mode=True).process(_input(images=[Image.new("RGB", (256, 256))]), lambda o: None)
    call = _last_call()
    assert call["conditioning"].uncond is None
    assert "warm_start" not in call


def test_edit_passes_the_prompt_attention_mask_through():
    _edit().process(_input(images=[Image.new("RGB", (256, 256))]), lambda o: None)
    assert "attention_mask" in _last_call()["conditioning"].cond


def test_edit_attaches_references_to_the_negative_branch_too():
    _edit().process(_input(images=[Image.new("RGB", (256, 256))], with_negative=True), lambda o: None)
    conditioning = _last_call()["conditioning"]
    assert conditioning.uncond["ref_latents"] is conditioning.cond["ref_latents"]
