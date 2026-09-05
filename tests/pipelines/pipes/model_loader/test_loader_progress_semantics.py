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
from src.platform.runtime.native.arch.trellis2 import load as trellis2_load
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel
from src.pipelines.pipes.model_loader.anima.main import ModelLoaderAnimaPipe
from src.pipelines.pipes.model_loader.flux.main import ModelLoaderFluxPipe
from src.pipelines.pipes.model_loader.krea2.main import ModelLoaderKrea2Pipe
from src.pipelines.pipes.model_loader.ltx.main import ModelLoaderLtxPipe
from src.pipelines.pipes.model_loader.minimax_h3.main import ModelLoaderMinimaxH3Pipe
from src.pipelines.pipes.model_loader.minimax_music3.main import ModelLoaderMinimaxMusic3Pipe
from src.pipelines.pipes.model_loader.qwen.main import ModelLoaderQwenPipe
from src.pipelines.pipes.model_loader.seedvr2.main import ModelLoaderSeedVR2Pipe
from src.pipelines.pipes.model_loader.trellis2.main import ModelLoaderTrellis2Pipe
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
    monkeypatch.setattr(
        "src.pipelines.pipes.model_loader.ltx.main.load_projection", lambda *a, **kw: {},
    )
    monkeypatch.setattr(
        "src.pipelines.pipes.model_loader.minimax_music3.main.load_minimax_music3_te",
        lambda path, device: NativeModel("text_encoder", _FakeModule()),
    )
    monkeypatch.setattr(
        "src.pipelines.pipes.model_loader.trellis2.main._load_matting", lambda path: _FakeModule(),
    )
    monkeypatch.setattr(
        "src.pipelines.pipes.model_loader.trellis2.main.prefix_size_gb", lambda *a, **kw: 1.0,
    )
    for name in [n for n in dir(trellis2_load) if n.startswith("load_")]:
        monkeypatch.setattr(trellis2_load, name, lambda *a, **kw: _FakeModule())
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
    _resolve_encoder(out)
    return [state.split("— ", 1)[1].split(".")[0] for state in states]


def _resolve_encoder(out) -> None:
    """Touch the adapter's encoder, which is what the first cold encode does:
    on a family that deferred acquisition this is where the thunk runs and the
    component announces itself. LTX exposes it as ``te_encoder``; everyone
    else as ``encoder``. A family with no text-encoder output has nothing to
    resolve."""
    clip = out.output.get("text_encoder")
    if clip is None:
        return
    getattr(clip, "te_encoder" if hasattr(type(clip), "te_encoder") else "encoder")


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

_TRELLIS = [
    "image encoder", "sparse-structure flow", "sparse-structure decoder", "shape flow",
    "shape decoder", "texture flow (1024)", "texture decoder", "high-resolution shape flow",
]

_FAMILIES += [
    (
        ModelLoaderMinimaxH3Pipe,
        {
            "model": {"file_path": "/m/dit.safetensors"},
            "text_encoder": {"file_path": "/m/te.safetensors"},
            "video_vae": {"file_path": "/m/vvae.safetensors"},
            "audio_vae": {"file_path": "/m/avae.safetensors"},
        },
        # The text encoder is deferred on BOTH paths here (H3 has no eager
        # branch), so the cold encode's announce closes the run at 4 of 4.
        ["DiT (1 of 4)", "video VAE (2 of 4)", "audio VAE (3 of 4)", "text encoder (4 of 4)"],
        ["DiT (1 of 4)", "video VAE (2 of 4)", "audio VAE (3 of 4)", "text encoder (4 of 4)"],
    ),
    (
        ModelLoaderLtxPipe,
        {
            "model": {"file_path": "/m/ltx.safetensors"},
            "text_encoder": {"file_path": "/m/te.safetensors"},
            "vae": {"file_path": "/m/vae.safetensors"},
        },
        ["DiT (1 of 4)", "VAE (2 of 4)", "text embedding projection (3 of 4)", "text encoder (4 of 4)"],
        ["DiT (1 of 4)", "text encoder (2 of 4)", "VAE (3 of 4)", "text embedding projection (4 of 4)"],
    ),
    (
        ModelLoaderLtxPipe,
        {
            "model": {"file_path": "/m/ltx.safetensors"},
            "text_encoder": {"file_path": "/m/te.safetensors"},
            "vae": {"file_path": "/m/vae.safetensors"},
            "audio": True,
            "audio_model": {"file_path": "/m/audio.safetensors"},
            "upscale_model": {"file_path": "/m/up.safetensors"},
            "temporal_upscale_model": {"file_path": "/m/tup.safetensors"},
            "duration_head": {"file_path": "/m/dh.safetensors"},
        },
        [
            "DiT (1 of 9)", "VAE (2 of 9)", "audio VAE (3 of 9)", "vocoder (4 of 9)",
            "spatial upsampler (5 of 9)", "temporal upsampler (6 of 9)", "duration head (7 of 9)",
            "text embedding projection (8 of 9)", "text encoder (9 of 9)",
        ],
        [
            "DiT (1 of 9)", "text encoder (2 of 9)", "VAE (3 of 9)", "audio VAE (4 of 9)",
            "vocoder (5 of 9)", "spatial upsampler (6 of 9)", "temporal upsampler (7 of 9)",
            "duration head (8 of 9)", "text embedding projection (9 of 9)",
        ],
    ),
    (
        ModelLoaderMinimaxMusic3Pipe,
        {
            "model": {"file_path": "/m/dit.safetensors"},
            "text_encoder": {"file_path": "/m/te.safetensors"},
            "vae": {"file_path": "/m/vae.safetensors"},
        },
        # Music3 acquires its language model eagerly on both paths -- there is
        # no clip adapter to defer through.
        ["DiT (1 of 3)", "audio VAE (2 of 3)", "text encoder (3 of 3)"],
        ["DiT (1 of 3)", "audio VAE (2 of 3)", "text encoder (3 of 3)"],
    ),
    (
        ModelLoaderTrellis2Pipe,
        {
            "diffusion_model": {"file_path": "/m/dit.safetensors"},
            "shape_vae": {"file_path": "/m/svae.safetensors"},
            "texture_vae": {"file_path": "/m/tvae.safetensors"},
            "image_encoder": {"file_path": "/m/dino.safetensors"},
        },
        [f"{label} ({n} of 8)" for n, label in enumerate(_TRELLIS, 1)],
        [f"{label} ({n} of 8)" for n, label in enumerate(_TRELLIS, 1)],
    ),
    (
        ModelLoaderTrellis2Pipe,
        {
            "diffusion_model": {"file_path": "/m/dit.safetensors"},
            "shape_vae": {"file_path": "/m/svae.safetensors"},
            "texture_vae": {"file_path": "/m/tvae.safetensors"},
            "image_encoder": {"file_path": "/m/dino.safetensors"},
            "matting_model": {"file_path": "/m/matting.safetensors"},
            "resolution_tier": "1536",
        },
        [f"{label} ({n} of 9)" for n, label in enumerate(_TRELLIS + ["matting model"], 1)],
        [f"{label} ({n} of 9)" for n, label in enumerate(_TRELLIS + ["matting model"], 1)],
    ),
]

_IDS = [
    "anima", "qwen", "z_image", "krea2", "flux", "seedvr2",
    "wan22-single-expert", "wan22-dual-expert",
    "minimax_h3", "ltx-minimal", "ltx-audio-and-upsamplers", "minimax_music3",
    "trellis2-1024", "trellis2-1536-matting",
]


@pytest.mark.parametrize("pipe_cls,config,cached,_uncached", _FAMILIES, ids=_IDS)
def test_progress_sequence_with_a_lifecycle_service(pipe_cls, config, cached, _uncached):
    assert _components(pipe_cls(_cfg(pipe_cls, **config)), _FakeModels()) == cached


@pytest.mark.parametrize("pipe_cls,config,_cached,uncached", _FAMILIES, ids=_IDS)
def test_progress_sequence_without_a_lifecycle_service(pipe_cls, config, _cached, uncached):
    assert _components(pipe_cls(_cfg(pipe_cls, **config)), None) == uncached


# Families whose bundle carries a ``te`` the loader deliberately leaves unset.
# SeedVR2 has no text encoder; Music3's language model is acquired eagerly and
# Trellis2's image encoder is not a text encoder at all.
_EAGER_OR_NO_TE = (ModelLoaderSeedVR2Pipe, ModelLoaderMinimaxMusic3Pipe, ModelLoaderTrellis2Pipe)
_DEFERS_TE = [entry for entry in _FAMILIES if entry[0] not in _EAGER_OR_NO_TE]
_DEFERS_TE_IDS = [
    name for entry, name in zip(_FAMILIES, _IDS) if entry[0] not in _EAGER_OR_NO_TE
]


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

    _resolve_encoder(out)

    assert [key for key in models.keys if key.startswith("native/te/")]
