"""Tests for the model_loader/ltx pipe (single-DiT all-in-one acquire scheme)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
from safetensors.torch import save_file

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.model_loader.ltx.main import ModelLoaderLtxPipe
from src.pipelines.pipes.model_loader.ltx.bundle import LTXModelBundle
from src.pipelines.pipes.model_loader.ltx.ltx_clip import LTXClipTextEncoder


class _FakeModels:
    def __init__(self):
        self.calls = []
        self.estimates = {}  # key -> estimated_vram_gb kwarg, for admission-math assertions

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        self.estimates[key] = estimated_vram_gb
        if key.startswith("native/ltx_proj/"):
            return {"video_projection_weight": torch.zeros(1)}
        return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=14.0, compute_dtype=torch.bfloat16)


def _config():
    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg.update({
        "model": {"file_path": "/m/ltx_dit.safetensors", "name": "ltx"},
        "text_encoder": {"file_path": "/m/gemma3.safetensors", "name": "gemma3"},
        "vae": {"file_path": "/m/ltx_vae.safetensors", "name": "vae"},
    })
    return cfg


def _run(cfg=None):
    models = _FakeModels()
    with patch("src.pipelines.pipes.model_loader.ltx.main.load_projection", return_value={"video_projection_weight": torch.zeros(1)}):
        out = ModelLoaderLtxPipe(config=cfg or _config()).process(PipeInput(input={"MODELS": models}), lambda o: None)
    return models, out


def test_name_and_outputs():
    assert ModelLoaderLtxPipe.name == "model_loader"
    out = {o.name: o.io_type for o in ModelLoaderLtxPipe.outputs()}
    assert out["model"] == IOType.MODEL and out["text_encoder"] == IOType.TEXT_ENCODER


def test_three_component_acquires_plus_projection():
    models, out = _run()
    keys = [k for k, _ in models.calls]
    assert keys == [
        "native/dit//m/ltx_dit.safetensors",
        "native/te//m/gemma3.safetensors",
        "native/vae//m/ltx_vae.safetensors",
        "native/ltx_proj//m/ltx_dit.safetensors",
    ]
    bundle = out.output["model"]
    assert isinstance(bundle, LTXModelBundle)
    assert isinstance(out.output["text_encoder"], LTXClipTextEncoder)
    assert bundle.projections == {"video_projection_weight": torch.zeros(1)}


def test_bundle_carries_te_cache_key_matching_the_te_acquire_key():
    """`latent_upscaler/ltx` needs the TE's own MODELS cache key
    (not derivable from the bundle's `te` field, which is a WeakModelRef view,
    not a str) to release it explicitly once it's dead weight -- this must
    match the exact key `acquire()` was called with above."""
    models, out = _run()
    bundle = out.output["model"]
    assert bundle.te_cache_key == "native/te//m/gemma3.safetensors"


def test_vae_defaults_to_checkpoint_when_unconfigured():
    """No separate VAE file configured -> acquire the VAE from the all-in-one
    checkpoint (`model`'s path), not a required-and-missing `vae` field."""
    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg.update({
        "model": {"file_path": "/m/ltx_dit.safetensors", "name": "ltx"},
        "text_encoder": {"file_path": "/m/gemma3.safetensors", "name": "gemma3"},
    })
    models, out = _run(cfg)
    keys = [k for k, _ in models.calls]
    assert "native/vae//m/ltx_dit.safetensors" in keys
    assert isinstance(out.output["model"], LTXModelBundle)


def test_vae_override_still_used_when_configured():
    models, out = _run()  # _config() sets an explicit vae path
    keys = [k for k, _ in models.calls]
    assert "native/vae//m/ltx_vae.safetensors" in keys
    assert isinstance(out.output["model"], LTXModelBundle)


def test_audio_off_by_default_no_extra_acquires_and_bundle_fields_none():
    models, out = _run()  # _config() doesn't set "audio" -> default False
    keys = [k for k, _ in models.calls]
    assert not any(k.startswith("native/audio_vae/") or k.startswith("native/vocoder/") for k in keys)
    bundle = out.output["model"]
    assert bundle.audio_vae is None
    assert bundle.vocoder is None


def test_audio_true_acquires_audio_vae_and_vocoder_from_checkpoint_path():
    cfg = _config()
    cfg["audio"] = True
    models, out = _run(cfg)
    keys = [k for k, _ in models.calls]
    assert "native/audio_vae//m/ltx_dit.safetensors" in keys
    assert "native/vocoder//m/ltx_dit.safetensors" in keys
    bundle = out.output["model"]
    assert bundle.audio_vae is not None
    assert bundle.vocoder is not None


def test_bundle_unload_covers_audio_vae_and_vocoder():
    from unittest.mock import MagicMock

    from src.pipelines.pipes.model_loader.ltx.bundle import LTXModelBundle

    dit, te, vae, audio_vae, vocoder, upsampler = (MagicMock() for _ in range(6))
    bundle = LTXModelBundle(
        dit=dit, te=te, vae=vae, audio_vae=audio_vae, vocoder=vocoder, upsampler=upsampler,
    )
    bundle.unload()
    for component in (dit, te, vae, audio_vae, vocoder, upsampler):
        component.unload.assert_called_once()


def test_bundle_unload_tolerates_none_audio_components():
    from src.pipelines.pipes.model_loader.ltx.bundle import LTXModelBundle
    from unittest.mock import MagicMock

    bundle = LTXModelBundle(dit=MagicMock(), te=MagicMock(), vae=MagicMock())
    bundle.unload()  # must not raise despite audio_vae/vocoder/upsampler defaulting to None


# --- optional spatial latent-upscaler component -----------------------


def test_upscale_off_by_default_no_extra_acquires_and_bundle_field_none():
    models, out = _run()  # _config() doesn't set "upscale_model" -> default None
    keys = [k for k, _ in models.calls]
    assert not any(k.startswith("native/ltx_upsampler/") for k in keys)
    bundle = out.output["model"]
    assert bundle.upsampler is None


def test_upscale_model_configured_acquires_from_own_standalone_file():
    cfg = _config()
    cfg["upscale_model"] = {"file_path": "/m/ltx-2.3-spatial-upscaler-x1.5.safetensors", "name": "upscaler"}
    models, out = _run(cfg)
    keys = [k for k, _ in models.calls]
    assert "native/ltx_upsampler//m/ltx-2.3-spatial-upscaler-x1.5.safetensors" in keys
    bundle = out.output["model"]
    assert bundle.upsampler is not None


def test_upscale_model_gets_own_file_size_estimate_not_suppressed():
    """Unlike audio_vae/vocoder (sliced from the all-in-one DiT checkpoint),
    the upscaler is its own standalone file -- its acquire must keep a real
    file-size estimate, not the all-in-one suppression path."""
    cfg = _config()
    cfg["upscale_model"] = {"file_path": "/m/ltx-2.3-spatial-upscaler-x1.5.safetensors", "name": "upscaler"}

    def fake_size(path):
        return 40.0 if path == "/m/ltx_dit.safetensors" else 0.3

    with patch("src.pipelines.pipes.model_loader.ltx.main.file_size_gb", side_effect=fake_size):
        models, out = _run(cfg)

    assert models.estimates["native/ltx_upsampler//m/ltx-2.3-spatial-upscaler-x1.5.safetensors"] == 0.3


def test_missing_required_paths_raises():
    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg["model"] = {"file_path": "/m/ltx_dit.safetensors"}
    models = _FakeModels()
    try:
        ModelLoaderLtxPipe(config=cfg).process(PipeInput(input={"MODELS": models}), lambda o: None)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "requires" in str(e)


def test_lora_changes_dit_fingerprint_only():
    def fps(loras):
        cfg = _config()
        cfg["loras"] = loras
        models, _ = _run(cfg)
        return dict(models.calls)

    dit = "native/dit//m/ltx_dit.safetensors"
    te = "native/te//m/gemma3.safetensors"
    vae = "native/vae//m/ltx_vae.safetensors"

    no_lora = fps([])
    with_lora = fps([{"model": "/m/motion.safetensors", "strength": 0.8}])
    assert no_lora[dit] != with_lora[dit]
    assert "motion.safetensors@0.8" in with_lora[dit]
    assert no_lora[te] == with_lora[te]
    assert no_lora[vae] == with_lora[vae]


def test_clip_fingerprint_includes_te_and_dit_path():
    _, out = _run()
    clip = out.output["text_encoder"]
    assert clip._model_fingerprint == "/m/gemma3.safetensors|/m/ltx_dit.safetensors"


# --- admission-control estimate multi-counting (Fix 3) -----------------------


def test_vae_audio_vocoder_from_same_file_as_dit_pass_no_estimate():
    """When the VAE/audio_vae/vocoder are acquired from the SAME all-in-one
    file as the DiT, the DiT's file-size estimate already covers that file's
    footprint -- passing file_size_gb() again for each component would count
    the ~40GB file up to 4x in the cache's admission math."""
    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg.update({
        "model": {"file_path": "/m/ltx_dit.safetensors", "name": "ltx"},
        "text_encoder": {"file_path": "/m/gemma3.safetensors", "name": "gemma3"},
        # No separate "vae" -> defaults to the all-in-one checkpoint path.
        "audio": True,
    })
    with patch("src.pipelines.pipes.model_loader.ltx.main.file_size_gb", return_value=40.0):
        models, out = _run(cfg)

    assert models.estimates["native/dit//m/ltx_dit.safetensors"] == 40.0  # dominant component keeps its estimate
    assert models.estimates["native/vae//m/ltx_dit.safetensors"] is None
    assert models.estimates["native/audio_vae//m/ltx_dit.safetensors"] is None
    assert models.estimates["native/vocoder//m/ltx_dit.safetensors"] is None
    # The TE lives in its own file -> keeps its own file-size estimate.
    assert models.estimates["native/te//m/gemma3.safetensors"] == 40.0


def test_vae_from_distinct_standalone_file_keeps_its_own_estimate():
    """A VAE override pointing at its own standalone file must still be
    estimated from its own size (not suppressed like the all-in-one case)."""
    def fake_size(path):
        return 40.0 if path == "/m/ltx_dit.safetensors" else 0.3

    with patch("src.pipelines.pipes.model_loader.ltx.main.file_size_gb", side_effect=fake_size):
        models, out = _run()  # _config() sets an explicit, distinct "vae" path

    assert models.estimates["native/dit//m/ltx_dit.safetensors"] == 40.0
    assert models.estimates["native/vae//m/ltx_vae.safetensors"] == 0.3


def test_no_models_service_loads_directly():
    cfg = _config()
    with patch("src.pipelines.pipes.model_loader.ltx.main.NativeEngineLoader") as MockLoader, \
         patch("src.pipelines.pipes.model_loader.ltx.main.load_projection", return_value={"video_projection_weight": torch.zeros(1)}):
        instance = MockLoader.return_value
        instance.load.return_value = SimpleNamespace(module=object(), spec=None, compute_dtype=torch.bfloat16)
        out = ModelLoaderLtxPipe(config=cfg).process(PipeInput(input={}), lambda o: None)
    assert isinstance(out.output["model"], LTXModelBundle)


# --- LTX-2.5 split-checkpoint layout ------------------------------------
#
# These write real (tiny) safetensors files to disk -- the split-checkpoint
# guard (`_require_embedded_component`) reads the target file's own header,
# so it only fires against paths that actually exist; every test above uses
# nonexistent stub paths (`/m/*.safetensors`) and is untouched by it.


def _write_tiny_safetensors(path: Path, keys: list[str], metadata: dict | None = None) -> None:
    tensors = {k: torch.zeros(2, dtype=torch.float32) for k in keys}
    save_file(tensors, str(path), metadata={str(k): str(v) for k, v in (metadata or {}).items()})


def test_split_transformer_only_file_raises_crisp_error_without_vae_config(tmp_path):
    model_path = tmp_path / "ltx-2.5-22b-dev-transformer-bf16.safetensors"
    _write_tiny_safetensors(model_path, ["patchify_proj.weight"], {"model_version": "2.5"})
    te_path = tmp_path / "gemma4-12b-with-proj-ltx-2.5-bf16.safetensors"
    _write_tiny_safetensors(te_path, ["dummy.weight"])

    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg.update({
        "model": {"file_path": str(model_path), "name": "ltx25"},
        "text_encoder": {"file_path": str(te_path), "name": "gemma4"},
    })
    models = _FakeModels()
    try:
        ModelLoaderLtxPipe(config=cfg).process(PipeInput(input={"MODELS": models}), lambda o: None)
        assert False, "expected ValueError"
    except ValueError as e:
        msg = str(e)
        assert "vae" in msg
        assert model_path.name in msg
        assert "`vae` config" in msg
    assert models.calls == []  # fails before any acquire happens


def test_split_video_vae_configured_bypasses_guard_and_acquires_from_own_file():
    model_path = "/m/ltx25_transformer.safetensors"  # nonexistent -> guard is a no-op regardless
    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg.update({
        "model": {"file_path": model_path, "name": "ltx25"},
        "text_encoder": {"file_path": "/m/gemma4.safetensors", "name": "gemma4"},
        "vae": {"file_path": "/m/ltx-2.5-video-vae-conv-bf16.safetensors", "name": "vae25"},
    })
    models, out = _run(cfg)
    keys = [k for k, _ in models.calls]
    assert "native/vae//m/ltx-2.5-video-vae-conv-bf16.safetensors" in keys
    assert isinstance(out.output["model"], LTXModelBundle)


def test_split_video_vae_configured_bypasses_guard_even_when_model_file_lacks_it(tmp_path):
    """A real on-disk transformer-only `model` file with an explicit `vae`
    override must not trip the guard -- the guard only cares about `model`
    when no override is given."""
    model_path = tmp_path / "ltx-2.5-transformer.safetensors"
    _write_tiny_safetensors(model_path, ["patchify_proj.weight"])
    te_path = tmp_path / "gemma4.safetensors"
    _write_tiny_safetensors(te_path, ["dummy.weight"])
    vae_path = tmp_path / "ltx-2.5-video-vae-conv-bf16.safetensors"
    _write_tiny_safetensors(vae_path, ["encoder.conv_in.weight"])

    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg.update({
        "model": {"file_path": str(model_path), "name": "ltx25"},
        "text_encoder": {"file_path": str(te_path), "name": "gemma4"},
        "vae": {"file_path": str(vae_path), "name": "vae25"},
    })
    models, out = _run(cfg)  # must not raise
    keys = [k for k, _ in models.calls]
    assert f"native/vae/{vae_path}" in keys
    assert isinstance(out.output["model"], LTXModelBundle)


def test_split_audio_true_without_audio_model_raises_crisp_error_when_model_lacks_it(tmp_path):
    model_path = tmp_path / "ltx-2.5-transformer.safetensors"
    _write_tiny_safetensors(model_path, ["patchify_proj.weight", "vae.dummy.weight"])  # vae embedded, audio not
    te_path = tmp_path / "gemma4.safetensors"
    _write_tiny_safetensors(te_path, ["dummy.weight"])

    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg.update({
        "model": {"file_path": str(model_path), "name": "ltx25"},
        "text_encoder": {"file_path": str(te_path), "name": "gemma4"},
        "audio": True,
    })
    models = _FakeModels()
    try:
        ModelLoaderLtxPipe(config=cfg).process(PipeInput(input={"MODELS": models}), lambda o: None)
        assert False, "expected ValueError"
    except ValueError as e:
        msg = str(e)
        assert "audio" in msg
        assert "`audio_model` config" in msg


def test_split_audio_model_configured_acquires_both_from_own_file(tmp_path):
    model_path = tmp_path / "ltx-2.5-transformer.safetensors"
    _write_tiny_safetensors(model_path, ["patchify_proj.weight", "vae.dummy.weight"])
    te_path = tmp_path / "gemma4.safetensors"
    _write_tiny_safetensors(te_path, ["dummy.weight"])
    audio_path = tmp_path / "ltx-2.5-audio-vae-bf16.safetensors"
    _write_tiny_safetensors(audio_path, ["audio_vae.dummy.weight", "vocoder.dummy.weight"])

    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg.update({
        "model": {"file_path": str(model_path), "name": "ltx25"},
        "text_encoder": {"file_path": str(te_path), "name": "gemma4"},
        "audio": True,
        "audio_model": {"file_path": str(audio_path), "name": "audio25"},
    })
    models, out = _run(cfg)  # must not raise
    keys = [k for k, _ in models.calls]
    assert f"native/audio_vae/{audio_path}" in keys
    assert f"native/vocoder/{audio_path}" in keys
    bundle = out.output["model"]
    assert bundle.audio_vae is not None
    assert bundle.vocoder is not None


def test_split_audio_model_gets_its_own_file_size_estimate():
    def fake_size(path):
        return 40.0 if path == "/m/ltx_dit.safetensors" else 0.4

    cfg = _config()
    cfg["audio"] = True
    cfg["audio_model"] = {"file_path": "/m/ltx-2.5-audio-vae-bf16.safetensors", "name": "audio25"}
    with patch("src.pipelines.pipes.model_loader.ltx.main.file_size_gb", side_effect=fake_size):
        models, out = _run(cfg)

    assert models.estimates["native/audio_vae//m/ltx-2.5-audio-vae-bf16.safetensors"] == 0.4
    assert models.estimates["native/vocoder//m/ltx-2.5-audio-vae-bf16.safetensors"] == 0.4


def test_describe_models_includes_audio_model_when_configured():
    cfg = _config()
    cfg["audio_model"] = {"file_path": "/m/ltx-2.5-audio-vae-bf16.safetensors", "name": "audio25"}
    pipe = ModelLoaderLtxPipe(config=cfg)
    types = {m.type for m in pipe.describe_models()}
    assert "ltx_audio_vae" in types


def test_describe_models_omits_audio_model_when_unconfigured():
    pipe = ModelLoaderLtxPipe(config=_config())
    types = {m.type for m in pipe.describe_models()}
    assert "ltx_audio_vae" not in types


def test_23_all_in_one_still_loads_identically_no_guard_trip(tmp_path):
    """A real on-disk 2.3-style all-in-one checkpoint (embedded `vae.*` +
    `audio_vae.*` keys) with no `vae`/`audio_model` override must load exactly
    like before -- the split-checkpoint guard must never fire for it."""
    model_path = tmp_path / "ltx23-allinone.safetensors"
    _write_tiny_safetensors(
        model_path,
        ["model.diffusion_model.patchify_proj.weight", "vae.dummy.weight", "audio_vae.dummy.weight", "vocoder.dummy.weight"],
    )
    te_path = tmp_path / "gemma3.safetensors"
    _write_tiny_safetensors(te_path, ["dummy.weight"])

    cfg = ModelLoaderLtxPipe.get_default_config()
    cfg.update({
        "model": {"file_path": str(model_path), "name": "ltx23"},
        "text_encoder": {"file_path": str(te_path), "name": "gemma3"},
        "audio": True,
    })
    models, out = _run(cfg)  # must not raise
    keys = [k for k, _ in models.calls]
    assert f"native/vae/{model_path}" in keys
    assert f"native/audio_vae/{model_path}" in keys
    assert f"native/vocoder/{model_path}" in keys
    assert isinstance(out.output["model"], LTXModelBundle)


def test_projection_fingerprint_includes_te_path_for_2_5_relocation():
    models, out = _run()
    fingerprints = dict(models.calls)
    assert fingerprints["native/ltx_proj//m/ltx_dit.safetensors"] == "/m/ltx_dit.safetensors|/m/gemma3.safetensors|torch.bfloat16"


def test_load_projection_called_with_te_path_kwarg():
    """`_FakeModels.acquire` short-circuits the `native/ltx_proj/` loader
    (returns a fake dict without calling it, like every other component here)
    -- exercise the real call by going through the no-MODELS path instead."""
    with patch("src.pipelines.pipes.model_loader.ltx.main.NativeEngineLoader") as MockLoader, \
         patch("src.pipelines.pipes.model_loader.ltx.main.load_projection", return_value={"video_projection_weight": torch.zeros(1)}) as mock_proj:
        instance = MockLoader.return_value
        instance.load.return_value = SimpleNamespace(module=object(), spec=None, compute_dtype=torch.bfloat16)
        ModelLoaderLtxPipe(config=_config()).process(PipeInput(input={}), lambda o: None)
    mock_proj.assert_called_once()
    _, kwargs = mock_proj.call_args
    assert kwargs.get("te_path") == "/m/gemma3.safetensors"


def test_bundle_model_version_reads_dit_module_config():
    from unittest.mock import MagicMock

    dit = MagicMock()
    dit.module.config.model_version = (2, 5)
    bundle = LTXModelBundle(dit=dit, te=MagicMock(), vae=MagicMock())
    assert bundle.model_version == (2, 5)


def test_bundle_model_version_none_when_dit_config_lacks_it():
    from unittest.mock import MagicMock

    dit = MagicMock()
    del dit.module.config.model_version
    bundle = LTXModelBundle(dit=dit, te=MagicMock(), vae=MagicMock())
    assert bundle.model_version is None


_TEMPORAL_FILE = "/m/ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors"
_DURATION_FILE = "/m/ltx-2.5-duration-head-bf16.safetensors"


def test_temporal_upscale_and_duration_head_off_by_default():
    models, out = _run()
    keys = [k for k, _ in models.calls]
    assert not any(k.startswith("native/ltx_duration_head/") for k in keys)
    bundle = out.output["model"]
    assert bundle.temporal_upsampler is None
    assert bundle.duration_head is None


def test_temporal_upscale_model_acquires_into_its_own_bundle_slot():
    cfg = _config()
    cfg["temporal_upscale_model"] = {"file_path": _TEMPORAL_FILE, "name": "temporal"}
    models, out = _run(cfg)
    assert f"native/ltx_upsampler/{_TEMPORAL_FILE}" in [k for k, _ in models.calls]
    bundle = out.output["model"]
    assert bundle.temporal_upsampler is not None
    # The spatial slot stays empty -- the two are independent.
    assert bundle.upsampler is None


def test_both_upscalers_can_be_loaded_at_once():
    """The whole reason the temporal upsampler gets its OWN slot: a temporal
    round needs a spatial and a temporal checkpoint resident together."""
    cfg = _config()
    cfg["upscale_model"] = {"file_path": "/m/ltx-2.3-spatial-upscaler-x2.safetensors", "name": "spatial"}
    cfg["temporal_upscale_model"] = {"file_path": _TEMPORAL_FILE, "name": "temporal"}
    models, out = _run(cfg)
    keys = [k for k, _ in models.calls]
    assert "native/ltx_upsampler//m/ltx-2.3-spatial-upscaler-x2.safetensors" in keys
    assert f"native/ltx_upsampler/{_TEMPORAL_FILE}" in keys
    bundle = out.output["model"]
    assert bundle.upsampler is not None and bundle.temporal_upsampler is not None
    assert bundle.upsampler is not bundle.temporal_upsampler


def test_duration_head_configured_acquires_under_its_own_key():
    cfg = _config()
    cfg["duration_head"] = {"file_path": _DURATION_FILE, "name": "duration"}
    models, out = _run(cfg)
    assert f"native/ltx_duration_head/{_DURATION_FILE}" in [k for k, _ in models.calls]
    assert out.output["model"].duration_head is not None


def test_duration_head_gets_its_own_file_size_estimate():
    cfg = _config()
    cfg["duration_head"] = {"file_path": _DURATION_FILE, "name": "duration"}

    def fake_size(path):
        return 40.0 if path == "/m/ltx_dit.safetensors" else 0.01

    with patch("src.pipelines.pipes.model_loader.ltx.main.file_size_gb", side_effect=fake_size):
        models, _out = _run(cfg)
    assert models.estimates[f"native/ltx_duration_head/{_DURATION_FILE}"] == 0.01


def test_duration_head_fingerprint_includes_the_dtype():
    cfg = _config()
    cfg["duration_head"] = {"file_path": _DURATION_FILE, "name": "duration"}
    models, _out = _run(cfg)
    fp = dict(models.calls)[f"native/ltx_duration_head/{_DURATION_FILE}"]
    assert fp == f"{_DURATION_FILE}|bfloat16"


def test_describe_models_names_the_new_slots():
    cfg = _config()
    cfg["temporal_upscale_model"] = {"file_path": _TEMPORAL_FILE, "name": "temporal"}
    cfg["duration_head"] = {"file_path": _DURATION_FILE, "name": "duration"}
    described = {m.type for m in ModelLoaderLtxPipe(config=cfg).describe_models()}
    assert "ltx_temporal_latent_upscaler" in described
    assert "ltx_duration_head" in described


def test_bundle_unload_covers_the_new_components():
    cfg = _config()
    cfg["temporal_upscale_model"] = {"file_path": _TEMPORAL_FILE, "name": "temporal"}
    cfg["duration_head"] = {"file_path": _DURATION_FILE, "name": "duration"}

    unloaded = []

    class _Recording:
        def __init__(self, tag):
            self.tag = tag

        def unload(self):
            unloaded.append(self.tag)

    # Local names keep the components alive: the bundle holds them through
    # `WeakModelRef`, so an inline construction would be collected at once.
    dit, te, vae = _Recording("dit"), _Recording("te"), _Recording("vae")
    temporal, duration = _Recording("temporal"), _Recording("duration")
    bundle = LTXModelBundle(
        dit=dit, te=te, vae=vae, temporal_upsampler=temporal, duration_head=duration,
    )
    bundle.unload()
    assert "temporal" in unloaded and "duration" in unloaded
