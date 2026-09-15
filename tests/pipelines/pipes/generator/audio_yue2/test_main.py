"""Tests for the generator/audio_yue2 pipe: config validation, the full
generate_one flow against stubbed AR/NAR engine components (prompt/budget
assembly, ABC-phase splicing vs. user-supplied abc vs. cot=off, progress
throttling, cancellation, LM-released-before-VAE-placed ordering), and output
emission shape/fields. No real weights, CPU-only."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import AudioGenerationOutput, GalleryGenerationOutput
from src.pipelines.pipes.generator.audio_yue2.main import GeneratorAudioYuE2Pipe, MAX_DURATION
from src.platform.runtime.native.arch.yue2 import protocol
from src.platform.runtime.native.errors import SamplingCancelled

MODULE = "src.pipelines.pipes.generator.audio_yue2.main"


def test_validate_config_rejects_empty_style():
    with pytest.raises(ValueError, match="style"):
        GeneratorAudioYuE2Pipe.validate_config({"style": "", "lyrics": ""})


def test_validate_config_rejects_blank_style():
    with pytest.raises(ValueError, match="style"):
        GeneratorAudioYuE2Pipe.validate_config({"style": "   "})


def test_validate_config_accepts_style_only():
    GeneratorAudioYuE2Pipe.validate_config({"style": "upbeat synth pop"})


def test_validate_config_rejects_negative_duration():
    with pytest.raises(ValueError, match="duration"):
        GeneratorAudioYuE2Pipe.validate_config({"style": "x", "duration": -1})


def test_validate_config_accepts_zero_duration_as_auto():
    GeneratorAudioYuE2Pipe.validate_config({"style": "x", "duration": 0})


def test_duration_spec_bounds_admit_auto_zero():
    spec = next(s for s in GeneratorAudioYuE2Pipe.configuration() if s.name == "duration")
    assert spec.min_value == 0.0


def test_validate_config_rejects_duration_over_cap():
    with pytest.raises(ValueError, match="duration"):
        GeneratorAudioYuE2Pipe.validate_config({"style": "x", "duration": MAX_DURATION + 1})


def test_validate_config_rejects_invalid_cot():
    with pytest.raises(ValueError, match="cot"):
        GeneratorAudioYuE2Pipe.validate_config({"style": "x", "cot": "bogus"})


def test_validate_config_rejects_abc_when_cot_off():
    with pytest.raises(ValueError, match="abc"):
        GeneratorAudioYuE2Pipe.validate_config({"style": "x", "cot": "off", "abc": "X:1\nK:C\n"})


def test_validate_config_accepts_abc_with_cot_full():
    GeneratorAudioYuE2Pipe.validate_config({"style": "x", "cot": "full", "abc": "X:1\nK:C\n"})


def test_build_context_rejects_non_yue2_family():
    pipe = GeneratorAudioYuE2Pipe({**GeneratorAudioYuE2Pipe.get_default_config(), "style": "x"})
    bundle = SimpleNamespace(spec=SimpleNamespace(family="minimax_music3", variant="music3"))
    with pytest.raises(ValueError, match="not a YuE2"):
        pipe.build_context(PipeInput(input={"model": bundle}))


def test_build_context_clamps_tokens_to_hard_cap():
    pipe = GeneratorAudioYuE2Pipe({
        **GeneratorAudioYuE2Pipe.get_default_config(), "style": "x", "duration": MAX_DURATION,
    })
    bundle = SimpleNamespace(spec=SimpleNamespace(family="yue2", variant="yue2_3b"))
    ctx = pipe.build_context(PipeInput(input={"model": bundle}))
    assert ctx.extra.max_tokens == 9000


def test_build_context_zero_duration_means_auto_at_model_max():
    pipe = GeneratorAudioYuE2Pipe({**GeneratorAudioYuE2Pipe.get_default_config(), "style": "x", "duration": 0})
    bundle = SimpleNamespace(spec=SimpleNamespace(family="yue2", variant="yue2_3b"))
    ctx = pipe.build_context(PipeInput(input={"model": bundle}))
    assert ctx.extra.max_tokens == 9000


def test_build_context_short_duration_yields_smaller_budget():
    pipe = GeneratorAudioYuE2Pipe({**GeneratorAudioYuE2Pipe.get_default_config(), "style": "x", "duration": 10})
    bundle = SimpleNamespace(spec=SimpleNamespace(family="yue2", variant="yue2_3b"))
    ctx = pipe.build_context(PipeInput(input={"model": bundle}))
    assert ctx.extra.max_tokens == 250


def test_build_context_default_cfg_scale_depends_on_cot():
    bundle = SimpleNamespace(spec=SimpleNamespace(family="yue2", variant="yue2_3b"))
    pipe_off = GeneratorAudioYuE2Pipe({**GeneratorAudioYuE2Pipe.get_default_config(), "style": "x", "cot": "off"})
    pipe_full = GeneratorAudioYuE2Pipe({**GeneratorAudioYuE2Pipe.get_default_config(), "style": "x", "cot": "full"})
    assert pipe_off.build_context(PipeInput(input={"model": bundle})).extra.cfg_scale == 1.01
    assert pipe_full.build_context(PipeInput(input={"model": bundle})).extra.cfg_scale == 1.0


class _FakeModel:
    """Duck-types NativeModel: `.module`, `.move_to`/`.offload` recording into
    a SHARED order list, tagged by component name."""

    def __init__(self, name, module, order):
        self.name = name
        self.module = module
        self.order = order

    def move_to(self, device):
        self.order.append((self.name, "move_to"))

    def offload(self):
        self.order.append((self.name, "offload"))


class _FakeModels:
    def __init__(self, order):
        self.order = order
        self.evicted = []

    def evict_dead_weight(self, key):
        self.order.append(("evict", key))
        self.evicted.append(key)
        return True


def _fake_vae_decode(latents):
    return torch.zeros(1, 2, latents.shape[-1] * 1920)


def _make_bundle(order):
    tokenizer = SimpleNamespace(encode=lambda text: [10, 11, 12])
    lm_module = SimpleNamespace(cfg=SimpleNamespace(max_position_embeddings=24576))
    vae_module = SimpleNamespace(sample_rate=48000, decode=_fake_vae_decode)

    lm = _FakeModel("lm", lm_module, order)
    vae = _FakeModel("vae", vae_module, order)

    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="yue2", variant="yue2_3b"),
        lm=lm, vae=vae, tokenizer=tokenizer, lm_cache_key="native/dit/x",
    )
    return bundle, lm, vae


def _pipe(**config_overrides):
    cfg = {**GeneratorAudioYuE2Pipe.get_default_config(), "style": "test style", "device": "cpu"}
    cfg.update(config_overrides)
    return GeneratorAudioYuE2Pipe(cfg)


def _fake_ar_generate_factory(order, abc_frames=5, abc_stopped=True, semantic_frames=25, semantic_stopped=True):
    def fake_generate(model, prefix_ids, sampling, seed, phase, negative_ids=None, cfg_scale=1.0,
                       legacy_off=False, is_cancelled=None, on_frame=None):
        order.append(("ar", phase))
        n = abc_frames if phase == "abc" else semantic_frames
        stopped = abc_stopped if phase == "abc" else semantic_stopped
        total = n
        history = []
        for i in range(1, n + 1):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(i)
            if on_frame is not None:
                on_frame(i, total)
            history.append(i if phase == "abc" else protocol.CODEC_OFFSET + i)
        return history, stopped
    return fake_generate


def _fake_nar_synthesize_factory(order, num_frames_out=10, steps=4):
    total = steps

    def fake_synthesize(model, prefix_ids, codec_ids, seed, steps=32, context=24576,
                         is_cancelled=None, on_step=None):
        order.append(("nar", "synthesize"))
        for s in range(1, total + 1):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(s)
            if on_step is not None:
                on_step(s, total)
        return torch.zeros(1, num_frames_out, 64)
    return fake_synthesize


def _make_progress(outputs):
    from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
    return ProgressEmitter(lambda o: outputs.append(o), title="generator")


def _run_generate_one(pipe, bundle, order, is_cancelled=None, ar_kwargs=None, nar_kwargs=None):
    ar_fn = _fake_ar_generate_factory(order, **(ar_kwargs or {}))
    nar_fn = _fake_nar_synthesize_factory(order, **(nar_kwargs or {}))
    outputs = []
    progress = _make_progress(outputs)

    with patch(f"{MODULE}.ar_loop.generate", side_effect=ar_fn), \
         patch(f"{MODULE}.nar.synthesize", side_effect=nar_fn):
        pipe._models = _FakeModels(order)
        ctx = pipe.build_context(PipeInput(input={"model": bundle}))
        result = pipe.generate_one(ctx, 0, 12345, progress, is_cancelled)
    return result, order, outputs


def test_generate_one_returns_audio_output_with_expected_fields():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off")
    result, order, _outputs = _run_generate_one(pipe, bundle, order, ar_kwargs={"semantic_frames": 25})

    assert isinstance(result, AudioGenerationOutput)
    assert result.temporary is False
    assert result.track_type == "mixed"
    assert result.seed == 12345
    assert result.sample_rate == 48000
    assert result.channels == 2
    assert result.duration == pytest.approx(25 / 25.0)
    assert result.guidance_scale == pytest.approx(1.01)


def test_generate_one_moves_and_offloads_every_component():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off")
    _result, order, _outputs = _run_generate_one(pipe, bundle, order)

    tags = [entry for entry in order if entry[0] in ("lm", "vae")]
    assert ("lm", "move_to") in tags and ("lm", "offload") in tags
    assert ("vae", "move_to") in tags and ("vae", "offload") in tags


def test_lm_released_before_vae_placed():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off")
    _result, order, _outputs = _run_generate_one(pipe, bundle, order)

    evict_index = order.index(("evict", "native/dit/x"))
    vae_place_index = order.index(("vae", "move_to"))
    assert evict_index < vae_place_index, f"LM must be evicted before the VAE places: {order}"


def test_nar_runs_before_lm_offloads():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off")
    _result, order, _outputs = _run_generate_one(pipe, bundle, order)

    nar_index = order.index(("nar", "synthesize"))
    offload_index = order.index(("lm", "offload"))
    assert nar_index < offload_index, f"NAR must run on the still-resident LM: {order}"


def test_cot_off_runs_semantic_phase_only():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off")
    _result, order, _outputs = _run_generate_one(pipe, bundle, order)

    ar_calls = [entry for entry in order if entry[0] == "ar"]
    assert ar_calls == [("ar", "semantic")]


def test_cot_full_without_abc_runs_abc_then_semantic():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="full")
    _result, order, _outputs = _run_generate_one(pipe, bundle, order)

    ar_calls = [entry for entry in order if entry[0] == "ar"]
    assert ar_calls == [("ar", "abc"), ("ar", "semantic")]


def test_cot_full_with_user_abc_skips_abc_phase():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="full", abc="X:1\nK:C\nCDEF|")
    _result, order, _outputs = _run_generate_one(pipe, bundle, order)

    ar_calls = [entry for entry in order if entry[0] == "ar"]
    assert ar_calls == [("ar", "semantic")]


def test_abc_stage_truncated_raises():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="full")
    with pytest.raises(ValueError, match="ABC transcription"):
        _run_generate_one(pipe, bundle, order, ar_kwargs={"abc_stopped": False})


def test_semantic_stage_truncated_at_auto_duration_raises():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off", duration=0)
    with pytest.raises(ValueError, match="token budget"):
        _run_generate_one(pipe, bundle, order, ar_kwargs={"semantic_stopped": False})


def test_semantic_stage_truncated_at_capped_duration_does_not_raise():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off", duration=30)
    result, _order, _outputs = _run_generate_one(pipe, bundle, order, ar_kwargs={"semantic_stopped": False})
    assert isinstance(result, AudioGenerationOutput)


def test_semantic_stage_zero_frames_raises():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off")
    with pytest.raises(ValueError, match="frame 0"):
        _run_generate_one(pipe, bundle, order, ar_kwargs={"semantic_frames": 0, "semantic_stopped": True})


def test_ar_progress_is_throttled():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off")
    _result, _order, outputs = _run_generate_one(pipe, bundle, order, ar_kwargs={"semantic_frames": 30})
    from src.pipelines.outputs import ProgressGenerationOutput
    composing = [
        o for o in outputs
        if isinstance(o, ProgressGenerationOutput) and o.state == "composing"
    ]
    assert 1 <= len(composing) < 30


def test_cancellation_mid_ar_raises_and_emits_no_audio():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off")
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(SamplingCancelled):
        _run_generate_one(pipe, bundle, order, is_cancelled=is_cancelled, ar_kwargs={"semantic_frames": 25})

    assert ("nar", "synthesize") not in order
    assert ("vae", "move_to") not in order


def test_cancellation_mid_nar_raises_after_ar_completes():
    order = []
    bundle, lm, vae = _make_bundle(order)
    pipe = _pipe(cot="off")
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] > 25

    with pytest.raises(SamplingCancelled):
        _run_generate_one(
            pipe, bundle, order, is_cancelled=is_cancelled,
            ar_kwargs={"semantic_frames": 25}, nar_kwargs={"steps": 4},
        )
    assert ("ar", "semantic") in order
    assert ("nar", "synthesize") in order
    assert ("vae", "move_to") not in order


def test_process_emits_no_gallery_output_when_cancelled_before_generate_one():
    bundle, lm, vae = _make_bundle([])
    pipe = _pipe(cot="off")
    outputs = []

    with patch(f"{MODULE}.ar_loop.generate") as mock_ar:
        result = pipe.process(
            PipeInput(input={"model": bundle, "MODELS": None}),
            lambda o: outputs.append(o),
            is_cancelled=lambda: True,
        )
    mock_ar.assert_not_called()
    assert not any(isinstance(o, GalleryGenerationOutput) and o.audios for o in outputs)
    assert result.output["audio"] == []
