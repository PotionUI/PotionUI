from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.moss_image.main import GeneratorMossImagePipe

SETTINGS = {
    "prediction": "const", "shift": 5.0, "guidance": "cfg", "default_steps": 50,
    "schedule": "moss_grid", "uncond_skip_layers": [22],
}
FLOW = "src.pipelines.pipes._shared.generation.flow_generator_pipe"


@dataclass(frozen=True)
class _FakeSpec:
    family: str = "moss_image"
    variant: str = "moss_image"
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 32})
    sampling_settings: dict = field(default_factory=lambda: dict(SETTINGS))


class _FakeGenerator:
    instances: list["_FakeGenerator"] = []

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.dit = dit
        self.spec = _FakeSpec()
        self.sample_calls = []
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

    def encode_image(self, pixels, **_):
        self.encoded = getattr(self, "encoded", []) + [pixels]
        return torch.zeros(1, 128, pixels.shape[0] // 16, pixels.shape[1] // 16)

    def decode(self, latent, **_):
        return np.zeros((1, self._shape[2] * 16, self._shape[3] * 16, 3), dtype=np.uint8)


def _cond_model(with_negative=True):
    embeds = {"context": torch.ones(1, 4, 8), "attention_mask": None}
    n_embeds = {"context": torch.ones(1, 4, 8), "attention_mask": None} if with_negative else {}
    return SimpleNamespace(embeds=embeds, n_embeds=n_embeds)


def _bundle():
    return SimpleNamespace(dit=SimpleNamespace(estimated_vram_gb=10.0, module=object()), te_encoder=object(), vae=object())


def _pipe(**over):
    cfg = GeneratorMossImagePipe.get_default_config()
    cfg.update(over)
    return GeneratorMossImagePipe(config=cfg)


def _input(quantity=1, with_negative=True):
    return PipeInput(input={
        "model": _bundle(),
        "conditioning": [_cond_model(with_negative) for _ in range(quantity)],
        "seed": list(range(1, quantity + 1)),
    })


def setup_function(_):
    _FakeGenerator.instances.clear()


@pytest.fixture(autouse=True)
def _patched():
    with patch(f"{FLOW}.make_device_plan", lambda **_: None), patch(f"{FLOW}.NativeGenerator", _FakeGenerator):
        yield


def _last_call():
    return _FakeGenerator.instances[-1].sample_calls[-1]


def test_defaults_are_the_base_profile():
    cfg = GeneratorMossImagePipe.get_default_config()
    assert cfg["steps"] == 50
    assert cfg["guidance"] == 3.0
    assert cfg["sampler"] == "euler"


def test_true_cfg_runs_with_both_branches_and_cfg_zero_star_off():
    _pipe().process(_input(), lambda o: None)
    call = _last_call()
    assert call["cfg_scale"] == 3.0
    assert call["steps"] == 50
    assert call["conditioning"].uncond is not None
    assert call["guidance_options"] == {"cfg_zero_star": False}


def test_preset_cannot_turn_cfg_zero_star_on():
    _pipe(guidance_options={"cfg_zero_star": True}).process(_input(), lambda o: None)
    assert _last_call()["guidance_options"]["cfg_zero_star"] is False


@pytest.mark.parametrize("raw, expected", [("22", [22]), ("", []), ("3, 22", [3, 22]), ([5], [5])])
def test_uncond_skip_layers_are_parsed_into_a_sorted_list(raw, expected):
    _pipe(guidance_options={"uncond_skip_layers": raw}).process(_input(), lambda o: None)
    assert _last_call()["guidance_options"]["uncond_skip_layers"] == expected


def test_unset_skip_layers_leave_the_checkpoint_default():
    _pipe().process(_input(), lambda o: None)
    assert "uncond_skip_layers" not in _last_call()["guidance_options"]


def test_step_cache_options_are_forwarded():
    _pipe(step_cache={"rel_threshold": 0.2}).process(_input(), lambda o: None)
    assert _last_call()["step_cache_options"] == {"rel_threshold": 0.2}


def test_step_cache_is_off_when_unset():
    _pipe().process(_input(), lambda o: None)
    assert _last_call()["step_cache_options"] is None


def test_guidance_of_one_warns(caplog):
    with caplog.at_level(logging.WARNING):
        _pipe(guidance=1.0).process(_input(), lambda o: None)
    assert any("without CFG" in r.getMessage() for r in caplog.records)


def test_resolution_is_snapped_and_shift_override_reaches_the_spec():
    _pipe(resolution="1000x600", shift=3.0).process(_input(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert _last_call()["latents_shape"] == (1, 128, 38, 62)
    assert gen.spec.sampling_settings["shift"] == 3.0


def test_blank_shift_keeps_the_checkpoint_shift():
    _pipe().process(_input(), lambda o: None)
    assert _FakeGenerator.instances[-1].spec.sampling_settings["shift"] == 5.0


def test_one_image_per_seed():
    _pipe(quantity=2).process(_input(quantity=2), lambda o: None)
    assert len(_FakeGenerator.instances[-1].sample_calls) == 2


def _img2img_input(image=None):
    inp = _input()
    if image is not None:
        inp.input["image"] = [image]
    return inp

def test_mode_choices_include_img2img():
    spec = next(s for s in GeneratorMossImagePipe.configuration() if s.name == "mode")
    assert spec.choices == ["txt2img", "img2img", "inpaint"]

def test_img2img_mode_denoises_from_the_encoded_source_at_the_given_strength():
    _pipe(mode="img2img", denoise=0.6, steps=12, preview=False).process(
        _img2img_input(Image.new("RGB", (64, 64), (10, 20, 30))), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert len(gen.encoded) == 1
    call = _last_call()
    assert call["denoise_strength"] == 0.6
    assert call["init_latent"] is not None
    assert call["steps"] == 12

def test_img2img_mode_without_a_source_falls_back_to_txt2img():
    _pipe(mode="img2img", preview=False).process(_img2img_input(), lambda o: None)
    gen = _FakeGenerator.instances[-1]
    assert getattr(gen, "encoded", []) == []
    assert _last_call().get("init_latent") is None
