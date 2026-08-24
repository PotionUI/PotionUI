"""Tests for the generator/audio_minimax_music3 pipe: config validation, the
full generate_one flow against stubbed AR/flow engine components (progress
throttling, per-frame/per-step cancellation, LM-released-before-DiT-placed
ordering), and output emission shape/fields. No real weights, CPU-only."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import AudioGenerationOutput, GalleryGenerationOutput
from src.pipelines.pipes.generator.audio_minimax_music3.main import (
    GeneratorAudioMinimaxMusic3Pipe,
    MAX_DURATION,
)
from src.platform.runtime.native.errors import SamplingCancelled

MODULE = "src.pipelines.pipes.generator.audio_minimax_music3.main"


# -- config validation --------------------------------------------------------

def test_validate_config_rejects_empty_caption():
    with pytest.raises(ValueError, match="caption"):
        GeneratorAudioMinimaxMusic3Pipe.validate_config({"caption": "", "lyrics": "[instrumental]"})


def test_validate_config_rejects_blank_caption():
    with pytest.raises(ValueError, match="caption"):
        GeneratorAudioMinimaxMusic3Pipe.validate_config({"caption": "   ", "lyrics": ""})


def test_validate_config_accepts_caption_only():
    GeneratorAudioMinimaxMusic3Pipe.validate_config({"caption": "upbeat synth pop", "lyrics": ""})


def test_validate_config_rejects_negative_duration():
    with pytest.raises(ValueError, match="duration"):
        GeneratorAudioMinimaxMusic3Pipe.validate_config({"caption": "x", "duration": -1})


def test_validate_config_accepts_zero_duration_as_auto():
    GeneratorAudioMinimaxMusic3Pipe.validate_config({"caption": "x", "duration": 0})


def test_duration_spec_bounds_admit_auto_zero():
    """The generic spec-bounds validator runs BEFORE validate_config -- a spec
    min of 1.0 rejected duration=0 in production while validate_config
    accepted it (2026-08-18)."""
    spec = next(s for s in GeneratorAudioMinimaxMusic3Pipe.configuration() if s.name == "duration")
    assert spec.min_value == 0.0


def test_validate_config_rejects_duration_over_cap():
    with pytest.raises(ValueError, match="duration"):
        GeneratorAudioMinimaxMusic3Pipe.validate_config({"caption": "x", "duration": MAX_DURATION + 1})


# -- build_context -------------------------------------------------------------

def test_build_context_rejects_non_minimax_music3_family():
    pipe = GeneratorAudioMinimaxMusic3Pipe({**GeneratorAudioMinimaxMusic3Pipe.get_default_config(), "caption": "x"})
    bundle = SimpleNamespace(spec=SimpleNamespace(family="minimax_h3", variant="h3"))
    with pytest.raises(ValueError, match="not a MiniMax-Music3"):
        pipe.build_context(PipeInput(input={"model": bundle}))


def test_build_context_clamps_frames_to_hard_cap():
    pipe = GeneratorAudioMinimaxMusic3Pipe({
        **GeneratorAudioMinimaxMusic3Pipe.get_default_config(), "caption": "x", "duration": MAX_DURATION,
    })
    bundle = SimpleNamespace(spec=SimpleNamespace(family="minimax_music3", variant="music3"))
    ctx = pipe.build_context(PipeInput(input={"model": bundle}))
    assert ctx.extra.max_frames == 9000  # MAX_DURATION(360) * FPS(25)


def test_build_context_zero_duration_means_auto_at_model_max():
    """duration is a cap, never conditioning -- 0 caps at the model max and
    lets the AR stop token end the song naturally."""
    pipe = GeneratorAudioMinimaxMusic3Pipe({
        **GeneratorAudioMinimaxMusic3Pipe.get_default_config(), "caption": "x", "duration": 0,
    })
    bundle = SimpleNamespace(spec=SimpleNamespace(family="minimax_music3", variant="music3"))
    ctx = pipe.build_context(PipeInput(input={"model": bundle}))
    assert ctx.extra.max_frames == 9000


# -- fakes for the full generate_one flow --------------------------------------

class _FakeModel:
    """Duck-types NativeModel: `.module`, `.compute_dtype`, `.move_to`/`.offload`
    recording into a SHARED order list, tagged by component name."""

    def __init__(self, name, module, order, compute_dtype=torch.bfloat16):
        self.name = name
        self.module = module
        self.order = order
        self.compute_dtype = compute_dtype

    def move_to(self, device):
        self.order.append((self.name, "move_to"))

    def offload(self):
        self.order.append((self.name, "offload"))


class _FakeModels:
    """Duck-types the MODELS service, just enough for evict_dead_weight."""

    def __init__(self, order):
        self.order = order
        self.evicted = []

    def evict_dead_weight(self, key):
        self.order.append(("evict", key))
        self.evicted.append(key)
        return True


def _fake_dav_decode(latents):
    return torch.zeros(1, 2, latents.shape[-1] * 512)


def _make_bundle(order):
    tokenizer = SimpleNamespace(
        build_conditional_pair=lambda caption, lyrics: torch.zeros(2, 5, dtype=torch.long),
    )
    lm_module = SimpleNamespace(cfg=SimpleNamespace(max_position_embeddings=10240))
    dit_module = object()
    dav_module = SimpleNamespace(sample_rate=44100, hop_length=512, decode=_fake_dav_decode)

    lm = _FakeModel("lm", lm_module, order)
    dit = _FakeModel("dit", dit_module, order)
    dav = _FakeModel("dav", dav_module, order)

    bundle = SimpleNamespace(
        spec=SimpleNamespace(family="minimax_music3", variant="music3"),
        lm=lm, dit=dit, dav=dav, tokenizer=tokenizer, lm_cache_key="native/te/x",
    )
    return bundle, lm, dit, dav


def _pipe(**config_overrides):
    cfg = {**GeneratorAudioMinimaxMusic3Pipe.get_default_config(), "caption": "test caption", "device": "cpu"}
    cfg.update(config_overrides)
    return GeneratorAudioMinimaxMusic3Pipe(cfg)


def _fake_ar_generate_factory(order, num_frames=25, total_frames=25):
    """Mirrors ar_loop.generate's real per-frame contract: `is_cancelled()`
    is polled every iteration, unconditionally -- the test's own callable
    decides when (if ever) it flips."""
    def fake_ar_generate(lm_module, input_ids, generator, max_frames, cfg_scale, top_k, on_frame, is_cancelled):
        order.append(("ar", "generate"))
        for i in range(1, num_frames + 1):
            if is_cancelled():
                raise SamplingCancelled(step_index=i)
            on_frame(i, total_frames)
        return torch.zeros(1, num_frames, 32768)
    return fake_ar_generate


def _fake_denoise_windowed_factory(order, num_windows=1, window_latents=10, steps=4):
    """Mirrors flow.denoise_windowed's real per-step contract: `is_cancelled()`
    polled every euler step, unconditionally."""
    def fake_denoise_windowed(model, frame_hiddens, *, steps, cfg_scale, generator, device, dtype, on_step, is_cancelled):
        order.append(("flow", "denoise"))
        total = num_windows * steps
        chunks = []
        for w in range(num_windows):
            for s in range(steps):
                global_step = w * steps + s
                if is_cancelled():
                    raise SamplingCancelled(step_index=global_step)
                on_step(global_step, total)
            chunks.append(torch.zeros(1, 128, window_latents))
        return chunks
    return fake_denoise_windowed


def _run_generate_one(pipe, bundle, order, is_cancelled=None, ar_kwargs=None, flow_kwargs=None):
    # `order` is the SAME list `_make_bundle`'s fake components append
    # move_to/offload events into -- shared so the returned order interleaves
    # component placement with the ar/flow/evict events below.
    ar_fn = _fake_ar_generate_factory(order, **(ar_kwargs or {}))
    flow_fn = _fake_denoise_windowed_factory(order, **(flow_kwargs or {}))
    outputs = []
    progress = _make_progress(outputs)

    with patch(f"{MODULE}.ar_loop.generate", side_effect=ar_fn), \
         patch(f"{MODULE}.flow.denoise_windowed", side_effect=flow_fn), \
         patch(f"{MODULE}.flow.crop_bounds", return_value=(0, 0)):
        pipe._models = _FakeModels(order)
        ctx = pipe.build_context(PipeInput(input={"model": bundle}))
        result = pipe.generate_one(ctx, 0, 12345, progress, is_cancelled)
    return result, order, outputs


def _make_progress(outputs):
    from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
    return ProgressEmitter(lambda o: outputs.append(o), title="generator")


# -- full happy-path flow -------------------------------------------------------

def test_generate_one_returns_audio_output_with_expected_fields():
    order = []
    bundle, lm, dit, dav = _make_bundle(order)
    pipe = _pipe(cfg_scale=1.7, ar_cfg_scale=1.5)
    result, order, _outputs = _run_generate_one(pipe, bundle, order, ar_kwargs={"num_frames": 25, "total_frames": 25})

    assert isinstance(result, AudioGenerationOutput)
    assert result.temporary is False
    assert result.track_type == "mixed"
    assert result.seed == 12345
    assert result.sample_rate == 44100
    assert result.channels == 2
    assert result.duration == pytest.approx(25 / 25.0)
    assert result.guidance_scale == 1.7


def test_generate_one_moves_and_offloads_every_component():
    order = []
    bundle, lm, dit, dav = _make_bundle(order)
    pipe = _pipe()
    _result, order, _outputs = _run_generate_one(pipe, bundle, order)

    tags = [entry for entry in order if entry[0] in ("lm", "dit", "dav")]
    assert ("lm", "move_to") in tags and ("lm", "offload") in tags
    assert ("dit", "move_to") in tags and ("dit", "offload") in tags
    assert ("dav", "move_to") in tags and ("dav", "offload") in tags


# -- stage handoff: LM released before DiT places -------------------------------

def test_lm_released_before_dit_placed():
    order = []
    bundle, lm, dit, dav = _make_bundle(order)
    pipe = _pipe()
    _result, order, _outputs = _run_generate_one(pipe, bundle, order)

    evict_index = order.index(("evict", "native/te/x"))
    dit_place_index = order.index(("dit", "move_to"))
    assert evict_index < dit_place_index, f"LM must be evicted before the DiT places: {order}"


# -- progress throttling ---------------------------------------------------------

def test_ar_progress_is_throttled():
    order = []
    bundle, lm, dit, dav = _make_bundle(order)
    pipe = _pipe()
    _result, _order, outputs = _run_generate_one(
        pipe, bundle, order, ar_kwargs={"num_frames": 30, "total_frames": 30},
    )
    from src.pipelines.outputs import ProgressGenerationOutput
    composing = [
        o for o in outputs
        if isinstance(o, ProgressGenerationOutput) and o.state == "composing"
    ]
    # ~2Hz throttle at 25fps (min interval 12 frames): expect far fewer than
    # the 30 raw on_frame calls, but at least one (the last frame, i>=total).
    assert 1 <= len(composing) < 30


# -- cancellation --------------------------------------------------------------

def test_cancellation_mid_ar_raises_and_emits_no_audio():
    order = []
    bundle, lm, dit, dav = _make_bundle(order)
    pipe = _pipe()
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] > 2  # cancel partway through the AR loop

    with pytest.raises(SamplingCancelled):
        _run_generate_one(pipe, bundle, order, is_cancelled=is_cancelled, ar_kwargs={"num_frames": 25})

    # the flow stage never ran, and no AudioGenerationOutput was produced
    assert ("flow", "denoise") not in order
    assert ("dit", "move_to") not in order


def test_cancellation_mid_flow_raises_after_ar_completes():
    order = []
    bundle, lm, dit, dav = _make_bundle(order)
    pipe = _pipe()
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        # False for every one of the 25 AR frame checks (the whole AR loop
        # completes), True from the very first flow-stage step check onward.
        return calls["n"] > 25

    with pytest.raises(SamplingCancelled):
        _run_generate_one(
            pipe, bundle, order, is_cancelled=is_cancelled,
            ar_kwargs={"num_frames": 25, "total_frames": 25},
            flow_kwargs={"num_windows": 1, "steps": 4},
        )
    assert ("ar", "generate") in order
    assert ("flow", "denoise") in order


def test_process_emits_no_gallery_output_when_cancelled_before_generate_one():
    bundle, lm, dit, dav = _make_bundle([])
    pipe = _pipe()
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
