"""``YuE2Config`` round-trip, defaults, and shape validation."""

from __future__ import annotations

import pytest

from src.platform.runtime.native.arch.yue2.config import YUE2, YuE2Config


class TestDefaults:
    def test_released_checkpoint_defaults(self):
        cfg = YuE2Config()
        assert cfg.hidden_size == 2048
        assert cfg.num_hidden_layers == 28
        assert cfg.num_attention_heads == 16
        assert cfg.num_key_value_heads == 8
        assert cfg.head_dim == 128
        assert cfg.intermediate_size == 6144
        assert cfg.vocab_size == 184704
        assert cfg.max_position_embeddings == 24576
        assert cfg.latent_dim == 64
        assert cfg.max_latent_frames == 24576
        assert cfg.timestep_shift == 1.0
        assert cfg.rope_theta == 1_000_000.0


class TestValidation:
    def test_rejects_hidden_size_not_matching_heads_times_head_dim(self):
        with pytest.raises(ValueError):
            YuE2Config(hidden_size=100, num_attention_heads=16, head_dim=128)

    def test_rejects_kv_heads_not_dividing_attention_heads(self):
        with pytest.raises(ValueError):
            YuE2Config(hidden_size=16 * 8, num_attention_heads=16, head_dim=8, num_key_value_heads=5)


class TestFromDetectConfig:
    def test_round_trips_shape_derived_fields(self):
        detected = {
            "arch_type": YUE2,
            "hidden_size": 16,
            "num_hidden_layers": 2,
            "num_attention_heads": 2,
            "num_key_value_heads": 1,
            "head_dim": 8,
            "intermediate_size": 24,
            "vocab_size": 500,
        }
        cfg = YuE2Config.from_detect_config(detected)
        assert cfg.hidden_size == 16
        assert cfg.num_hidden_layers == 2
        assert cfg.num_attention_heads == 2
        assert cfg.num_key_value_heads == 1
        assert cfg.head_dim == 8
        assert cfg.intermediate_size == 24
        assert cfg.vocab_size == 500
        assert cfg.rope_theta == 1_000_000.0

    def test_rejects_wrong_arch_type(self):
        with pytest.raises(ValueError):
            YuE2Config.from_detect_config({"arch_type": "not_yue2"})

    def test_num_key_value_heads_defaults_to_num_attention_heads(self):
        detected = {
            "arch_type": YUE2,
            "hidden_size": 16,
            "num_hidden_layers": 1,
            "num_attention_heads": 2,
            "head_dim": 8,
            "intermediate_size": 24,
        }
        cfg = YuE2Config.from_detect_config(detected)
        assert cfg.num_key_value_heads == 2
