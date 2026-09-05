"""Step-windowed LoRA application evidence, through the REAL generator hook
lifecycle: a real ``GeneratorKrea2Pipe.process()`` call drives a real
``LoraStepWindowHook`` applying REAL kohya-dialect LoRAs to a REAL tiny Flux
DiT, and this asserts on the real ``ProgressEmitter`` capture and the
SERIALIZED ``ModelGenerationOutput`` -- not the hook's internal
``last_application``/``history`` records, which
``tests/platform/runtime/native/lora/test_lora_step_window.py`` already
covers directly.

Sibling to ``test_krea2_step_windowed_lora.py`` (which stubs the LoRA apply
entirely to pin the sampler wiring/step edges); this file is the one that
lets the apply run for real, to prove the loader/generator boundary's
diagnostic idiom (see ``loader_helpers.emit_lora_application_diagnostics``)
actually reaches a windowed adapter, not just a baked one.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import torch

from src.features.generation.handlers.artifact_handlers import serialize_models_output
from src.features.generation.output_types import SerializeContext
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ModelsGenerationOutput, ProgressGenerationOutput
from src.pipelines.pipes.generator.krea2.main import GeneratorKrea2Pipe
from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.lora.step_window import LoraStepWindow
from vendor.gpl.comfyui.ops import pick_operations

_FLOW = "src.pipelines.pipes._shared.generation.flow_generator_pipe"

TINY = {
    "image_model": "flux2", "hidden_size": 64, "num_heads": 2, "depth": 1,
    "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
    "context_in_dim": 32, "axes_dim": [8, 8, 8, 8], "mlp_ratio": 3.0,
    "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False,
}
STEM = "lora_unet_double_blocks_0_img_attn_qkv"

VALID = "/m/valid.safetensors"
BOGUS = "/m/bogus.safetensors"


def _build_dit() -> torch.nn.Module:
    m = Flux.from_config(TINY, pick_operations(torch.float32, torch.float32))
    sd = {}
    g = torch.Generator().manual_seed(7)
    for k, v in m.state_dict().items():
        if k.endswith(".scale") and "norm" in k:
            sd[k] = torch.ones_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn(v.shape, dtype=v.dtype, generator=g) * 0.05
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, match_model_spec(TINY))
    m.eval()
    return m


def _kohya_lora(stem: str, seed: int) -> dict:
    g = torch.Generator().manual_seed(seed)
    return {
        f"{stem}.lora_up.weight": torch.randn(192, 4, generator=g) * 0.1,
        f"{stem}.lora_down.weight": torch.randn(4, 64, generator=g) * 0.1,
        f"{stem}.alpha": torch.tensor(4.0),
    }


class _FakeSpec:
    family = "krea2"
    variant = "krea2_turbo"
    latent_format = {"latent_channels": 16}
    sampling_settings = {"guidance": "none"}


class _SteppingGenerator:
    """A generator whose ``sample`` drives the real sampler hook protocol,
    exactly as the real euler loop does — see ``test_krea2_step_windowed_lora.py``."""

    def __init__(self, dit, te, vae, device_plan=None, **_):
        self.dit = dit
        self.spec = _FakeSpec()

    def snap_resolution(self, width, height):
        return width, height

    def latent_shape_for(self, width, height, batch=1):
        return (batch, 16, 1, height // 8, width // 8)

    def sample(self, conditioning, latents_shape, **kw):
        steps, hooks = kw["steps"], kw["hooks"]
        for hook in hooks:
            hook.on_start(steps)
        for i in range(steps):
            for hook in hooks:
                hook.on_step(i, steps, torch.zeros(1), 1.0, None)
        for hook in hooks:
            hook.on_end()
        return torch.zeros(latents_shape)

    def decode(self, latent, **_):
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)


def _bundle(windowed_loras):
    return SimpleNamespace(
        dit=NativeModel("diffusion_model", _build_dit(), estimated_vram_gb=1.0),
        te_encoder=object(),
        vae=object(),
        te_cache_key=None,
        windowed_loras=tuple(windowed_loras),
    )


def _entry(start, end, path: str, weight: float = 1.0) -> dict:
    return {"file_path": path, "weight": weight, "window": LoraStepWindow(start, end)}


def _pipe_input(windowed_loras) -> PipeInput:
    return PipeInput(input={
        "model": _bundle(windowed_loras),
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={})],
        "seed": [1],
    })


def _make_pipe(**over):
    cfg = GeneratorKrea2Pipe.get_default_config()
    cfg.update(over)
    return GeneratorKrea2Pipe(config=cfg)


class _Recorder:
    def __init__(self) -> None:
        self.events: list = []

    def outputs(self, output) -> None:
        self.events.append(output)

    @property
    def warnings(self) -> list:
        return [
            e for e in self.events
            if isinstance(e, ProgressGenerationOutput) and e.icon is not None and e.icon.name == "alert-triangle"
        ]

    @property
    def model_artifacts(self) -> list:
        return [e for e in self.events if isinstance(e, ModelsGenerationOutput)]


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    monkeypatch.setattr(f"{_FLOW}.make_device_plan", lambda **_: None)
    monkeypatch.setattr(f"{_FLOW}.NativeGenerator", _SteppingGenerator)
    files = {
        VALID: _kohya_lora(STEM, seed=1),
        BOGUS: _kohya_lora("lora_unet_totally_bogus", seed=2),
    }
    monkeypatch.setattr(
        "src.pipelines.pipes._shared.generation.loader_helpers.load_torch_file",
        lambda path, device="cpu": (files[path], {}),
    )
    yield


def test_windowed_valid_plus_unmatched_warns_once_through_the_real_pipe():
    rec = _Recorder()
    pipe = _make_pipe(steps=4, preview=False)

    pipe.process(_pipe_input([_entry(1, 4, VALID), _entry(1, 4, BOGUS)]), rec.outputs)

    assert len(rec.warnings) == 1
    (artifact,) = rec.model_artifacts
    serialized = serialize_models_output(artifact, SerializeContext(generation_id="test"))
    models = {m["name"]: m for m in serialized["artifact_data"]["models"]}

    assert models["valid"]["zero_effect"] is False
    assert models["bogus"]["zero_effect"] is True
    assert models["bogus"]["unmatched_sample"] == ["lora_unet_totally_bogus"]
    assert models["valid"]["source_id"] != models["bogus"]["source_id"]


def test_windowed_batch_of_two_items_emits_once_not_per_item():
    """The overwhelmingly common batch case: N items, same windowed LoRA
    config -- the diagnostic must not repeat once per item."""
    rec = _Recorder()
    pipe = _make_pipe(steps=4, quantity=2, preview=False)

    pipe.process(PipeInput(input={
        "model": _bundle([_entry(1, 4, BOGUS)]),
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={})] * 2,
        "seed": [1, 2],
    }), rec.outputs)

    assert len(rec.warnings) == 1
    assert len(rec.model_artifacts) == 1


def test_fully_matched_window_emits_no_diagnostics():
    rec = _Recorder()
    pipe = _make_pipe(steps=4, preview=False)

    pipe.process(_pipe_input([_entry(1, 4, VALID)]), rec.outputs)

    assert rec.warnings == []
    assert rec.model_artifacts == []
