"""Tests for the LTX-2.5 duration head.

``ltx-2.5-duration-head-bf16.safetensors`` is not present locally, so the real
header check skips; everything else runs against synthetic tiny configs.

The ``predict_num_frames`` expectations below are not hand-derived: they were
produced by transcribing the reference arithmetic
(diffusers ``pipelines/ltx2/duration_head.py:219-243``) and sweeping it against
this port over 650 (seconds, fps, temporal_compression_ratio, bounds)
combinations -- 0 mismatches. The five cases pinned here are the branch
representatives from that sweep.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.ltx.duration_head import (
    DURATION_HEAD_PREFIX,
    LTXDurationHead,
    convert_duration_head_state_dict,
    load_ltx_duration_head,
)
from src.platform.runtime.native.errors import (
    NativeEngineLoadIntegrityError,
    NativeEngineUnsupportedError,
)
from vendor.gpl.comfyui.ops import disable_weight_init

_REAL_HEAD_PATH = Path("models/ltx/ltx-2.5-duration-head-bf16.safetensors")

_TINY_CONFIG = {
    "video_cross_attention_dim": 32,
    "audio_cross_attention_dim": 16,
    "pooler_hidden_dim": 8,
    "num_queries": 1,
    "num_pooler_heads": 2,
    "mlp_hidden_dim": 8,
}


def _randomize(module: torch.nn.Module) -> None:
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)


def _build(**overrides) -> LTXDurationHead:
    module = LTXDurationHead.from_config({**_TINY_CONFIG, **overrides}, disable_weight_init)
    _randomize(module)
    return module


def _video_tokens(batch: int = 1, seq: int = 5) -> torch.Tensor:
    return torch.randn(batch, seq, _TINY_CONFIG["video_cross_attention_dim"])


def _audio_tokens(batch: int = 1, seq: int = 7) -> torch.Tensor:
    return torch.randn(batch, seq, _TINY_CONFIG["audio_cross_attention_dim"])


class _FixedSecondsHead(LTXDurationHead):
    """A head whose regression output is pinned, so the frame-grid arithmetic
    can be tested independently of the weights."""

    seconds = 1.0

    def forward(self, video_tokens=None, audio_tokens=None):  # noqa: D102
        return torch.tensor([self.seconds])


def _fixed(seconds: float) -> _FixedSecondsHead:
    module = _FixedSecondsHead.from_config(_TINY_CONFIG, disable_weight_init)
    module.seconds = seconds
    return module


class TestConstructionAndForward:
    def test_default_config_builds(self):
        LTXDurationHead.from_config({}, disable_weight_init)

    def test_video_only(self):
        module = _build()
        with torch.no_grad():
            out = module(_video_tokens())
        assert out.shape == (1,)
        assert torch.isfinite(out).all()

    def test_audio_only(self):
        module = _build()
        with torch.no_grad():
            out = module(audio_tokens=_audio_tokens())
        assert out.shape == (1,)

    def test_both_modalities(self):
        module = _build()
        with torch.no_grad():
            out = module(_video_tokens(), _audio_tokens())
        assert out.shape == (1,)

    def test_output_is_positive_seconds(self):
        """The regression target is a LOG duration, exponentiated on the way
        out -- so however the weights fall, seconds are positive."""
        module = _build()
        with torch.no_grad():
            out = module(_video_tokens())
        assert (out > 0).all()

    def test_batch_is_preserved(self):
        module = _build()
        with torch.no_grad():
            out = module(_video_tokens(batch=3))
        assert out.shape == (3,)

    def test_sequence_length_does_not_change_output_shape(self):
        """The pooler's job: a fixed-size output whatever the caption length."""
        module = _build()
        with torch.no_grad():
            short = module(_video_tokens(seq=2))
            long = module(_video_tokens(seq=64))
        assert short.shape == long.shape == (1,)

    def test_no_tokens_at_all_raises(self):
        module = _build()
        with pytest.raises(ValueError, match="at least one"):
            module()

    def test_pooler_hidden_dim_not_divisible_by_heads_raises(self):
        with pytest.raises(NativeEngineUnsupportedError, match="not divisible"):
            LTXDurationHead.from_config(
                {**_TINY_CONFIG, "pooler_hidden_dim": 9, "num_pooler_heads": 2}, disable_weight_init
            )

    def test_input_dtype_is_cast_to_the_head(self):
        """A fp32 text encoder feeding a bf16 head must not raise."""
        module = _build()
        module.to(torch.bfloat16)
        with torch.no_grad():
            out = module(_video_tokens().to(torch.float32))
        assert out.dtype == torch.bfloat16

    def test_post_load_is_safe_noop(self):
        _build().post_load()


class TestPredictNumFrames:
    """Reference: diffusers ``duration_head.py:212-253``."""

    def test_plain_prediction_lands_on_the_grid(self):
        # 5.0s @ 24fps -> 120 frames, floored onto 8k+1 -> 113.
        assert _fixed(5.0).predict_num_frames(
            _video_tokens(), frame_rate=24.0, temporal_compression_ratio=8
        ) == 113

    def test_clamped_to_max_seconds(self):
        # 100s clamps to 20s -> 480 frames, floored -> 473. Reference :225.
        assert _fixed(100.0).predict_num_frames(
            _video_tokens(), frame_rate=24.0, temporal_compression_ratio=8
        ) == 473

    def test_snaps_up_when_flooring_undershoots_the_minimum(self):
        # The edge case reference :228-231 exists for: 1.0s @ 24fps clamps to
        # 24 frames, which floors to 17 -- below the 24-frame minimum -- so the
        # NEXT grid point up (25) is used instead.
        assert _fixed(1.0).predict_num_frames(
            _video_tokens(), frame_rate=24.0, temporal_compression_ratio=8
        ) == 25

    def test_bounds_admitting_no_grid_point_take_the_nearest(self):
        # Reference :232-243: [1.0s, 1.02s] @ 24fps is [24, 24] frames, and 24
        # is not 8k+1. 25 overshoots by one frame and wins over 17.
        assert _fixed(1.0).predict_num_frames(
            _video_tokens(), frame_rate=24.0, temporal_compression_ratio=8,
            min_seconds=1.0, max_seconds=1.02,
        ) == 25

    def test_nearest_tie_keeps_the_floor(self):
        # Same no-grid-point branch, but the two candidates are equidistant
        # (25 and 33 are both 4 from 29): reference :237 uses a strict `<`, so
        # the floor stands.
        assert _fixed(25.0).predict_num_frames(
            _video_tokens(), frame_rate=1.0, temporal_compression_ratio=8,
            min_seconds=29.0, max_seconds=32.0,
        ) == 25

    def test_result_is_always_on_the_grid(self):
        for seconds in (0.05, 0.9, 2.0, 7.3, 19.9, 60.0):
            for tcr in (4, 8):
                frames = _fixed(seconds).predict_num_frames(
                    _video_tokens(), frame_rate=24.0, temporal_compression_ratio=tcr
                )
                assert (frames - 1) % tcr == 0, (seconds, tcr, frames)

    def test_batched_prediction_is_refused(self):
        module = _build()
        with pytest.raises(ValueError, match="single prediction only"):
            module.predict_num_frames(
                _video_tokens(batch=2), frame_rate=24.0, temporal_compression_ratio=8
            )


class TestStateDictConversion:
    """Reference: diffusers ``scripts/convert_ltx2_to_diffusers.py:646-658``."""

    def _fused_state_dict(self, module: LTXDurationHead, prefix: str = "") -> dict:
        """The checkpoint's own layout: pooler projections fused into
        ``nn.MultiheadAttention``'s ``in_proj_*``."""
        split = module.state_dict()
        pooler = "attention_pooler"
        fused = {
            f"{pooler}.cross_attn.in_proj_weight": torch.cat(
                [split[f"{pooler}.to_q.weight"], split[f"{pooler}.to_k.weight"], split[f"{pooler}.to_v.weight"]],
                dim=0,
            ),
            f"{pooler}.cross_attn.in_proj_bias": torch.cat(
                [split[f"{pooler}.to_q.bias"], split[f"{pooler}.to_k.bias"], split[f"{pooler}.to_v.bias"]],
                dim=0,
            ),
            f"{pooler}.cross_attn.out_proj.weight": split[f"{pooler}.to_out.weight"],
            f"{pooler}.cross_attn.out_proj.bias": split[f"{pooler}.to_out.bias"],
        }
        for key, value in split.items():
            if not key.startswith(f"{pooler}.to_"):
                fused[key] = value
        return {f"{prefix}{k}": v for k, v in fused.items()}

    def test_fused_qkv_is_split_back_to_the_original_tensors(self):
        module = _build()
        original = module.state_dict()
        converted = convert_duration_head_state_dict(self._fused_state_dict(module))
        for name in ("to_q", "to_k", "to_v", "to_out"):
            for part in ("weight", "bias"):
                key = f"attention_pooler.{name}.{part}"
                assert torch.equal(converted[key], original[key]), key

    def test_converted_keys_match_the_module_exactly(self):
        module = _build()
        converted = convert_duration_head_state_dict(self._fused_state_dict(module))
        assert set(converted) == set(module.state_dict())

    def test_prefix_is_stripped(self):
        module = _build()
        converted = convert_duration_head_state_dict(
            self._fused_state_dict(module, prefix=DURATION_HEAD_PREFIX)
        )
        assert set(converted) == set(module.state_dict())

    def test_already_split_state_dict_passes_through(self):
        module = _build()
        original = module.state_dict()
        assert set(convert_duration_head_state_dict(original)) == set(original)

    def test_conversion_is_idempotent(self):
        module = _build()
        once = convert_duration_head_state_dict(self._fused_state_dict(module))
        twice = convert_duration_head_state_dict(once)
        assert set(once) == set(twice)
        for key in once:
            assert torch.equal(once[key], twice[key])

    def test_conversion_does_not_mutate_its_input(self):
        module = _build()
        fused = self._fused_state_dict(module)
        before = set(fused)
        convert_duration_head_state_dict(fused)
        assert set(fused) == before


class TestLoadDurationHead:
    def _write(self, tmp_path: Path, sd: dict, monkeypatch) -> Path:
        path = tmp_path / "ltx-2.5-duration-head-bf16.safetensors"
        monkeypatch.setattr(
            "src.platform.runtime.native.arch.ltx.duration_head.load_torch_file",
            lambda p, device="cpu": (sd, {}),
        )
        return path

    def test_round_trip_from_a_fused_checkpoint(self, tmp_path, monkeypatch):
        module = _build(num_pooler_heads=4)
        fused = TestStateDictConversion()._fused_state_dict(module, prefix=DURATION_HEAD_PREFIX)
        path = self._write(tmp_path, fused, monkeypatch)

        loaded = load_ltx_duration_head(path, disable_weight_init)
        assert loaded.video_cross_attention_dim == _TINY_CONFIG["video_cross_attention_dim"]
        assert loaded.audio_cross_attention_dim == _TINY_CONFIG["audio_cross_attention_dim"]
        assert loaded.pooler_hidden_dim == _TINY_CONFIG["pooler_hidden_dim"]

        tokens = _video_tokens()
        with torch.no_grad():
            assert torch.allclose(loaded(tokens), module(tokens), atol=1e-6)

    def test_a_checkpoint_without_a_head_raises(self, tmp_path, monkeypatch):
        path = self._write(tmp_path, {"vae.encoder.conv_in.weight": torch.zeros(4, 4)}, monkeypatch)
        with pytest.raises(NativeEngineUnsupportedError, match="carries no LTX duration head"):
            load_ltx_duration_head(path, disable_weight_init)

    def test_a_missing_key_fails_load_integrity(self, tmp_path, monkeypatch):
        module = _build(num_pooler_heads=4)
        fused = TestStateDictConversion()._fused_state_dict(module)
        del fused["mlp_out.bias"]
        path = self._write(tmp_path, fused, monkeypatch)
        with pytest.raises(NativeEngineLoadIntegrityError):
            load_ltx_duration_head(path, disable_weight_init)

    def test_preloaded_state_dict_skips_the_file_read(self, tmp_path):
        module = _build(num_pooler_heads=4)
        fused = TestStateDictConversion()._fused_state_dict(module)
        loaded = load_ltx_duration_head(
            tmp_path / "never-read.safetensors", disable_weight_init, sd=fused, metadata={},
        )
        assert loaded.num_queries == _TINY_CONFIG["num_queries"]


@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL_HEAD_PATH.exists(), reason="real LTX-2.5 duration head not present")
def test_real_header_matches_the_detected_config():
    """Key-parity against the shipped checkpoint, once it is downloaded: every
    key the file carries must land on a parameter of the module built from the
    config detection derives from it, and vice versa."""
    from safetensors import safe_open

    from src.platform.runtime.native.detect.vae_detect import detect_ltx_duration_head_config

    with safe_open(_REAL_HEAD_PATH, framework="pt") as f:
        sd = {k: f.get_slice(k) for k in f.keys()}
        shapes = {k: tuple(v.get_shape()) for k, v in sd.items()}

    class _Shape:
        def __init__(self, shape):
            self.shape = shape

    config = detect_ltx_duration_head_config({k: _Shape(v) for k, v in shapes.items()})
    assert config is not None, f"detection failed on the real file; keys: {sorted(shapes)[:10]}"

    module = LTXDurationHead.from_config(config, disable_weight_init)
    converted = convert_duration_head_state_dict({k: torch.zeros(v) for k, v in shapes.items()})
    expected = {k: tuple(v.shape) for k, v in module.state_dict().items()}
    assert set(converted) == set(expected)
    for key, tensor in converted.items():
        assert tuple(tensor.shape) == expected[key], key
