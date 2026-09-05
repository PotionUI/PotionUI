"""Per-adapter LoRA application evidence surfaced through ``sync_loras`` /
``apply_loras_to`` (the shared reconciliation path Flux and Krea-2 share).

``sync_loras``'s ``apply`` callback used to return nothing: the aggregate
"some params patched" from ``apply_loras`` hid a stack of one working adapter
and one wholly-dead one, and a cache-HIT no-op reconciliation had nothing to
re-emit at all. This drives the REAL ``sync_loras`` + ``apply_loras_to`` +
``apply_loras_with_report`` chain against a REAL tiny Flux DiT patched by REAL
kohya LoRAs — only the LoRA files' disk reads are faked — and asserts on the
generation-visible diagnostics, the stored evidence, and (for the failure
case) the weight tensor.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ModelsGenerationOutput, ProgressGenerationOutput
from src.pipelines.pipes._shared.generation.loader_helpers import apply_loras_to
from src.pipelines.pipes._shared.generation.loader_lifecycle import NO_LORAS, sync_loras
from src.pipelines.pipes.model_loader.flux.main import ModelLoaderFluxPipe
from src.pipelines.pipes.model_loader.krea2.main import ModelLoaderKrea2Pipe
from vendor.gpl.comfyui.ops import pick_operations

TINY = {
    "image_model": "flux2", "hidden_size": 64, "num_heads": 2, "depth": 1,
    "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
    "context_in_dim": 32, "axes_dim": [8, 8, 8, 8], "mlp_ratio": 3.0,
    "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False,
}
QKV = "double_blocks.0.img_attn.qkv"

VALID = "/m/valid.safetensors"
BOGUS = "/m/bogus.safetensors"


def _build_dit() -> torch.nn.Module:
    m = Flux.from_config(TINY, pick_operations(torch.float32, torch.float32))
    sd = {}
    g = torch.Generator().manual_seed(1)
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


def _target_weight(module: torch.nn.Module) -> torch.Tensor:
    return dict(module.named_modules())[QKV].weight.detach().clone()


@pytest.fixture
def fake_lora_files(monkeypatch):
    files = {
        VALID: _kohya_lora("lora_unet_double_blocks_0_img_attn_qkv", seed=1),
        BOGUS: _kohya_lora("lora_unet_totally_bogus", seed=2),
    }
    monkeypatch.setattr(
        "src.pipelines.pipes._shared.generation.loader_helpers.load_torch_file",
        lambda path, device="cpu": (files[path], {}),
    )
    return files


@pytest.fixture
def dit() -> NativeModel:
    model = NativeModel("diffusion_model", _build_dit())
    model._active_lora_fp = NO_LORAS
    return model


def _entry(path: str, weight: float = 0.8) -> dict:
    return {"file_path": path, "weight": weight}


def _apply(dit_model, loras):
    """The shape a family's ``_apply_loras`` staticmethod has: apply, and
    (once wired) hand the evidence back to ``sync_loras``."""
    return apply_loras_to(dit_model, loras, "TEST")


class _Recorder:
    def __init__(self) -> None:
        self.events: list = []

    def outputs(self, output) -> None:
        self.events.append(output)

    @property
    def warnings(self) -> list:
        """Only the alert-triangle LoRA warning -- a real ``process()`` also
        emits plain ``ProgressGenerationOutput``s for component-load progress
        (``ComponentProgress.advance``), which are not what this is testing."""
        return [
            e for e in self.events
            if isinstance(e, ProgressGenerationOutput) and e.icon is not None and e.icon.name == "alert-triangle"
        ]

    @property
    def model_artifacts(self) -> list:
        """Only the per-adapter EVIDENCE artifact -- a real ``process()`` also
        emits one ``ModelsGenerationOutput`` up front for the REQUESTED stack
        identity (``describe_models()``), whose entries never carry
        ``matched_params``."""
        return [
            e for e in self.events
            if isinstance(e, ModelsGenerationOutput)
            and any(m.matched_params is not None for m in e.models)
        ]


# -- a stack of one working + one dead adapter -------------------------------

def test_valid_and_wholly_unmatched_stack_warns_once_and_records_evidence(dit, fake_lora_files):
    rec = _Recorder()
    loras = [_entry(VALID), _entry(BOGUS)]

    result = sync_loras(dit, loras, "valid+bogus", _apply, generation_outputs=rec.outputs, log_tag="TEST")

    assert len(rec.warnings) == 1
    assert len(rec.model_artifacts) == 1
    diag = {m.name: m for m in rec.model_artifacts[0].models}
    assert diag["valid"].zero_effect is False
    assert diag["bogus"].zero_effect is True

    assert dit._active_lora_fp == "valid+bogus"
    assert result == dit._active_lora_application
    assert len(result) == 2
    assert any(r.zero_effect for r in result)
    assert any(not r.zero_effect for r in result)


def test_fully_matched_stack_emits_no_diagnostics(dit, fake_lora_files):
    rec = _Recorder()

    sync_loras(dit, [_entry(VALID)], "valid-only", _apply, generation_outputs=rec.outputs, log_tag="TEST")

    assert rec.events == []
    assert len(dit._active_lora_application) == 1
    assert not dit._active_lora_application[0].zero_effect


# -- cached reuse: re-emit, never recompute ----------------------------------

def test_cached_reuse_re_emits_stored_evidence_without_recomputing(dit, fake_lora_files, monkeypatch):
    import src.platform.runtime.native.lora.apply as apply_mod

    calls = []
    real = apply_mod.map_lora_keys

    def _spy(lora_sd, module):
        calls.append(1)
        return real(lora_sd, module)

    monkeypatch.setattr(apply_mod, "map_lora_keys", _spy)

    rec = _Recorder()
    loras = [_entry(VALID), _entry(BOGUS)]

    first = sync_loras(dit, loras, "fp-a", _apply, generation_outputs=rec.outputs, log_tag="TEST")
    assert len(calls) == 2  # one per file
    assert len(rec.warnings) == 1

    second = sync_loras(dit, loras, "fp-a", _apply, generation_outputs=rec.outputs, log_tag="TEST")

    assert len(calls) == 2, "an unchanged lora_fp must not recompute the mapping"
    assert second == first
    assert len(rec.warnings) == 2, "the cache-HIT no-op still re-emits the stored evidence"


def test_changed_stack_reconciliation_records_fresh_evidence(dit, fake_lora_files, monkeypatch):
    import src.platform.runtime.native.lora.apply as apply_mod

    calls = []
    real = apply_mod.map_lora_keys
    monkeypatch.setattr(apply_mod, "map_lora_keys",
                         lambda sd, m: (calls.append(1), real(sd, m))[1])

    rec = _Recorder()

    first = sync_loras(dit, [_entry(VALID)], "valid-only", _apply,
                        generation_outputs=rec.outputs, log_tag="TEST")
    assert len(first) == 1 and not first[0].zero_effect
    assert len(calls) == 1

    second = sync_loras(dit, [_entry(VALID), _entry(BOGUS)], "valid+bogus", _apply,
                         generation_outputs=rec.outputs, log_tag="TEST")

    assert len(calls) == 3, "the changed stack recomputes: 1 (unchanged file) + 2 (new stack)"
    assert len(second) == 2
    assert second != first
    assert any(r.zero_effect for r in second)


# -- a failed reconciliation leaves no success stamp -------------------------

def test_failed_reconciliation_leaves_no_success_stamp_or_evidence(dit, fake_lora_files):
    rec = _Recorder()
    before = _target_weight(dit.module)

    def _boom(dit_model, loras):
        raise RuntimeError("disk read failed")

    with pytest.raises(RuntimeError, match="disk read failed"):
        sync_loras(dit, [_entry(VALID)], "valid-only", _boom,
                   generation_outputs=rec.outputs, log_tag="TEST")

    assert dit._active_lora_fp == NO_LORAS
    assert dit._active_lora_application == ()
    assert rec.events == [], "a failed reconciliation must not emit a success diagnostic"
    assert torch.equal(_target_weight(dit.module), before)


# -- through the real Flux/Krea-2 process() ----------------------------------
#
# Everything above drives sync_loras/apply_loras_to directly; this drives the
# actual pipe entry point a generation calls, proving the wiring from
# `process()` down to `generation_outputs` -- not just that the shared helper
# CAN emit, but that Flux/Krea-2 actually pass their emitter to it.

class _FakeModels:
    """Real hit/miss cache semantics keyed on (key, fingerprint)."""

    def __init__(self) -> None:
        self._entries: dict = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        entry = self._entries.get(key)
        if entry is not None and entry[0] == fingerprint:
            return entry[1]
        value = loader()
        self._entries[key] = (fingerprint, value)
        return value

    def is_cached(self, key: str) -> bool:
        return key in self._entries

    def evict_dead_weight(self, key: str) -> bool:
        return self._entries.pop(key, None) is not None


def _pipe_config(pipe_cls, loras) -> dict:
    cfg = pipe_cls.get_default_config()
    cfg.update({
        "diffusion_model": {"file_path": "/m/dit.safetensors", "name": "dit"},
        "text_encoder": {"file_path": "/m/te.safetensors", "name": "te"},
        "vae": {"file_path": "/m/vae.safetensors", "name": "vae"},
        "loras": [{"model": path, "strength": 0.8} for path in loras],
    })
    return cfg


@pytest.fixture
def fake_checkpoint_load(monkeypatch):
    """Every non-DiT component loads as a bare object; the DiT loads as a
    REAL tiny Flux module every time (no real ~GB checkpoint on disk)."""
    def _load(self, path, kind, **kwargs):
        module = _build_dit() if kind == "diffusion_model" else object()
        return NativeModel(kind, module, estimated_vram_gb=1.0)

    monkeypatch.setattr(NativeEngineLoader, "load", _load)


def _run(pipe_cls, loras, models, rec):
    pipe = pipe_cls(config=_pipe_config(pipe_cls, loras))
    return pipe.process(PipeInput(input={"MODELS": models}), rec.outputs)


@pytest.mark.parametrize("pipe_cls", [ModelLoaderFluxPipe, ModelLoaderKrea2Pipe])
def test_process_warns_once_for_a_valid_plus_unmatched_stack(pipe_cls, fake_lora_files, fake_checkpoint_load):
    rec = _Recorder()
    models = _FakeModels()

    _run(pipe_cls, [VALID, BOGUS], models, rec)

    assert len(rec.warnings) == 1
    assert len(rec.model_artifacts) == 1
    diag = {m.name: m for m in rec.model_artifacts[0].models}
    assert diag["valid"].zero_effect is False
    assert diag["bogus"].zero_effect is True


@pytest.mark.parametrize("pipe_cls", [ModelLoaderFluxPipe, ModelLoaderKrea2Pipe])
def test_process_emits_nothing_for_a_fully_matched_stack(pipe_cls, fake_lora_files, fake_checkpoint_load):
    rec = _Recorder()
    models = _FakeModels()

    _run(pipe_cls, [VALID], models, rec)

    assert rec.warnings == []
    assert rec.model_artifacts == []


@pytest.mark.parametrize("pipe_cls", [ModelLoaderFluxPipe, ModelLoaderKrea2Pipe])
def test_process_reemits_stored_evidence_on_a_cache_hit_without_reapplying(
    pipe_cls, fake_lora_files, fake_checkpoint_load, monkeypatch,
):
    """A second generation with the SAME LoRA stack is a MODELS cache hit on
    the DiT; sync_loras's no-op branch must still re-surface the warning from
    the stored evidence, without recomputing the mapping."""
    import src.platform.runtime.native.lora.apply as apply_mod

    calls = []
    real = apply_mod.map_lora_keys
    monkeypatch.setattr(apply_mod, "map_lora_keys",
                         lambda sd, m: (calls.append(1), real(sd, m))[1])

    models = _FakeModels()

    rec1 = _Recorder()
    _run(pipe_cls, [VALID, BOGUS], models, rec1)
    assert len(rec1.warnings) == 1
    assert len(calls) == 2  # one per file, first generation

    rec2 = _Recorder()
    _run(pipe_cls, [VALID, BOGUS], models, rec2)
    assert len(rec2.warnings) == 1, "the cache-HIT reconciliation must still re-emit"
    assert len(calls) == 2, "an unchanged stack on a cache HIT must not recompute the mapping"
