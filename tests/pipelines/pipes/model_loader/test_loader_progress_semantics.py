"""The exact progress sequence each native loader emits, per family.

A loader's progress line is the only thing standing between the user and a bar
that looks stuck through minutes of a multi-GB cold load, and the sequence is
easy to break silently while moving acquisition around: one advance too few
leaves the fraction short of its total, one too many overshoots it, and a
reordered pair mislabels which component is loading. These pin the number,
order and N-of-total of the advances for both the cached path (a lifecycle
service is injected, so the text encoder is deferred and announced last, when
its consumer first asks for it) and the uncached one (no service to defer
through, so everything loads up front in declaration order).
"""

from __future__ import annotations

import pytest

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import ProgressGenerationOutput
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel
from src.pipelines.pipes.model_loader.anima.main import ModelLoaderAnimaPipe
from src.pipelines.pipes.model_loader.flux.main import ModelLoaderFluxPipe
from src.pipelines.pipes.model_loader.krea2.main import ModelLoaderKrea2Pipe
from src.pipelines.pipes.model_loader.qwen.main import ModelLoaderQwenPipe
from src.pipelines.pipes.model_loader.seedvr2.main import ModelLoaderSeedVR2Pipe
from src.pipelines.pipes.model_loader.wan22.main import ModelLoaderWan22Pipe
from src.pipelines.pipes.model_loader.z_image.main import ModelLoaderZImagePipe


class _FakeModule:
    def named_modules(self):
        return iter(())


class _FakeModels:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.keys.append(key)
        return loader()

    def is_cached(self, key):
        return True


@pytest.fixture(autouse=True)
def _fake_engine(monkeypatch):
    monkeypatch.setattr(
        NativeEngineLoader, "load",
        lambda self, path, kind, **kw: NativeModel(kind, _FakeModule(), spec=None, estimated_vram_gb=1.0),
    )
    monkeypatch.setattr(
        "src.pipelines.pipes.model_loader.z_image.main.load_text_encoder",
        lambda path, device, te_variant: _FakeModule(),
    )
    monkeypatch.setattr(
        "src.pipelines.pipes.model_loader.seedvr2.main.load_seedvr2_prompt_embedding",
        lambda path: object(),
    )
    for pipe in (ModelLoaderFluxPipe, ModelLoaderKrea2Pipe):
        monkeypatch.setattr(pipe, "_apply_loras", staticmethod(lambda dit, loras: None))


def _components(pipe, models):
    """The ordered ``component (n of total)`` tail of every progress line, with
    the text encoder's deferred acquisition resolved at the end when the pipe
    handed one out (which is what its consumer does on a cache miss)."""
    states = []
    out = pipe.process(PipeInput(input={"MODELS": models}), lambda o: (
        states.append(o.state) if isinstance(o, ProgressGenerationOutput) else None
    ))
    clip = out.output.get("text_encoder")
    if clip is not None:
        clip.encoder  # noqa: B018 - resolves a deferred acquisition, as the first encode does
    return [state.split("— ", 1)[1].split(".")[0] for state in states]


def _cfg(pipe_cls, **over):
    cfg = pipe_cls.get_default_config()
    cfg.update(over)
    return cfg


_TRIO = {
    "diffusion_model": {"file_path": "/m/dit.safetensors"},
    "text_encoder": {"file_path": "/m/te.safetensors"},
    "vae": {"file_path": "/m/vae.safetensors"},
}

_DEFERRED_TE = ["VAE (1 of 3)", "DiT (2 of 3)", "text encoder (3 of 3)"]
_EAGER_TE = ["text encoder (1 of 3)", "VAE (2 of 3)", "DiT (3 of 3)"]

_FAMILIES = [
    (ModelLoaderAnimaPipe, _TRIO, _DEFERRED_TE, _EAGER_TE),
    (ModelLoaderQwenPipe, _TRIO, _DEFERRED_TE, _EAGER_TE),
    (ModelLoaderZImagePipe, _TRIO, _DEFERRED_TE, _EAGER_TE),
    (ModelLoaderKrea2Pipe, _TRIO, _DEFERRED_TE, _EAGER_TE),
    (
        ModelLoaderFluxPipe,
        {**_TRIO, "clip_l": {"file_path": "/m/clip_l.safetensors"}},
        _DEFERRED_TE,
        _EAGER_TE,
    ),
    (
        ModelLoaderSeedVR2Pipe,
        {
            "diffusion_model": {"file_path": "/m/dit.safetensors"},
            "vae": {"file_path": "/m/vae.safetensors"},
            "prompt_embedding": {"file_path": "/m/emb.safetensors"},
        },
        ["VAE (1 of 2)", "DiT (2 of 2)"],
        ["VAE (1 of 2)", "DiT (2 of 2)"],
    ),
    (
        ModelLoaderWan22Pipe,
        {
            "high_noise_model": {"file_path": "/m/high.safetensors"},
            "text_encoder": {"file_path": "/m/te.safetensors"},
            "vae": {"file_path": "/m/vae.safetensors"},
        },
        ["DiT (1 of 3)", "VAE (2 of 3)", "text encoder (3 of 3)"],
        ["DiT (1 of 3)", "VAE (2 of 3)", "text encoder (3 of 3)"],
    ),
    (
        ModelLoaderWan22Pipe,
        {
            "high_noise_model": {"file_path": "/m/high.safetensors"},
            "low_noise_model": {"file_path": "/m/low.safetensors"},
            "text_encoder": {"file_path": "/m/te.safetensors"},
            "vae": {"file_path": "/m/vae.safetensors"},
        },
        [
            "high-noise DiT (1 of 4)", "low-noise DiT (2 of 4)",
            "VAE (3 of 4)", "text encoder (4 of 4)",
        ],
        [
            "high-noise DiT (1 of 4)", "low-noise DiT (2 of 4)",
            "VAE (3 of 4)", "text encoder (4 of 4)",
        ],
    ),
]

_IDS = [
    "anima", "qwen", "z_image", "krea2", "flux", "seedvr2",
    "wan22-single-expert", "wan22-dual-expert",
]


@pytest.mark.parametrize("pipe_cls,config,cached,_uncached", _FAMILIES, ids=_IDS)
def test_progress_sequence_with_a_lifecycle_service(pipe_cls, config, cached, _uncached):
    assert _components(pipe_cls(_cfg(pipe_cls, **config)), _FakeModels()) == cached


@pytest.mark.parametrize("pipe_cls,config,_cached,uncached", _FAMILIES, ids=_IDS)
def test_progress_sequence_without_a_lifecycle_service(pipe_cls, config, _cached, uncached):
    assert _components(pipe_cls(_cfg(pipe_cls, **config)), None) == uncached


_DEFERS_TE = [entry for entry in _FAMILIES if entry[0] is not ModelLoaderSeedVR2Pipe]
_DEFERS_TE_IDS = [name for name in _IDS if name != "seedvr2"]


@pytest.mark.parametrize("pipe_cls,config,_cached,_uncached", _DEFERS_TE, ids=_DEFERS_TE_IDS)
def test_text_encoder_is_never_acquired_by_process_itself(pipe_cls, config, _cached, _uncached):
    """A generation whose prompt-embed cache hits must not pay a from-disk
    encoder load for a component the generator's idle-TE release would evict a
    few pipes later. The encoder is acquired only when its consumer asks."""
    models = _FakeModels()
    out = pipe_cls(_cfg(pipe_cls, **config)).process(
        PipeInput(input={"MODELS": models}), lambda o: None
    )

    assert not [key for key in models.keys if key.startswith("native/te/")]
    assert out.output["model"].te is None

    out.output["text_encoder"].encoder

    assert [key for key in models.keys if key.startswith("native/te/")]
