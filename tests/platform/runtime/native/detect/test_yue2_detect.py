"""Detection tests for YuE2, driven by SYNTHETIC ``meta``-device state dicts built from the upstream module's own key/shape relationships (``modeling_yue2.py`` / ``modeling_vae.py``, ``multimodal-art-projection/YuE``, Apache-2.0) -- NOT a real safetensors header."""

from __future__ import annotations

import torch

from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from src.platform.runtime.native.detect.vae_detect import detect_yue2_vae_config


def _meta(shape: tuple[int, ...]) -> torch.Tensor:
    return torch.empty(shape, dtype=torch.float16, device="meta")


def _yue2_lm_state_dict(
    *,
    hidden_size: int = 2048,
    num_layers: int = 2,
    num_heads: int = 16,
    num_kv_heads: int = 8,
    head_dim: int = 128,
    intermediate_size: int = 6144,
    vocab_size: int = 184704,
    latent_dim: int = 64,
) -> dict[str, torch.Tensor]:
    sd: dict[str, torch.Tensor] = {
        "model.embed_tokens.weight": _meta((vocab_size, hidden_size)),
        "lm_head.weight": _meta((vocab_size, hidden_size)),
        "vae2llm.weight": _meta((hidden_size, latent_dim)),
        "vae2llm.bias": _meta((hidden_size,)),
        "llm2vae.weight": _meta((latent_dim, hidden_size)),
        "llm2vae.bias": _meta((latent_dim,)),
    }
    for i in range(num_layers):
        p = f"model.layers.{i}."
        for path in (f"{p}self_attn.", f"{p}nar_self_attn."):
            sd[path + "q_proj.weight"] = _meta((num_heads * head_dim, hidden_size))
            sd[path + "k_proj.weight"] = _meta((num_kv_heads * head_dim, hidden_size))
            sd[path + "v_proj.weight"] = _meta((num_kv_heads * head_dim, hidden_size))
            sd[path + "o_proj.weight"] = _meta((hidden_size, num_heads * head_dim))
            sd[path + "q_norm.weight"] = _meta((head_dim,))
            sd[path + "k_norm.weight"] = _meta((head_dim,))
        for path in (f"{p}mlp.", f"{p}nar_mlp."):
            sd[path + "gate_proj.weight"] = _meta((intermediate_size, hidden_size))
            sd[path + "up_proj.weight"] = _meta((intermediate_size, hidden_size))
            sd[path + "down_proj.weight"] = _meta((hidden_size, intermediate_size))
    return sd


def _yue2_vae_state_dict(
    *, latent_dim: int = 64, channels: int = 64, final_channels: int = 2048, out_channels: int = 2,
) -> dict[str, torch.Tensor]:
    return {
        "decoder.layers.0.weight_v": _meta((final_channels, latent_dim, 7)),
        "decoder.layers.0.weight_g": _meta((final_channels, 1, 1)),
        "decoder.layers.0.bias": _meta((final_channels,)),
        "decoder.layers.7.alpha": _meta((channels,)),
        "decoder.layers.7.beta": _meta((channels,)),
        "decoder.layers.8.weight_v": _meta((out_channels, channels, 7)),
        "decoder.layers.8.weight_g": _meta((out_channels, 1, 1)),
    }


class TestLMDetection:
    def test_default_shapes_detect_as_yue2(self):
        sd = _yue2_lm_state_dict()
        config = detect_unet_config(sd)
        assert config is not None
        assert config["image_model"] == "yue2"
        assert config["arch_type"] == "yue2"
        assert config["hidden_size"] == 2048
        assert config["num_hidden_layers"] == 2
        assert config["num_attention_heads"] == 16
        assert config["num_key_value_heads"] == 8
        assert config["head_dim"] == 128
        assert config["intermediate_size"] == 6144
        assert config["vocab_size"] == 184704
        assert config["latent_dim"] == 64

    def test_layer_count_stops_at_the_first_gap(self):
        sd = _yue2_lm_state_dict(num_layers=5)
        config = detect_unet_config(sd)
        assert config["num_hidden_layers"] == 5

    def test_a_narrower_variant_still_derives_correctly_from_shapes(self):
        sd = _yue2_lm_state_dict(
            hidden_size=1024, num_layers=1, num_heads=8, num_kv_heads=4,
            head_dim=128, intermediate_size=3072, vocab_size=1000, latent_dim=32,
        )
        config = detect_unet_config(sd)
        assert config["hidden_size"] == 1024
        assert config["num_attention_heads"] == 8
        assert config["num_key_value_heads"] == 4
        assert config["head_dim"] == 128
        assert config["intermediate_size"] == 3072
        assert config["vocab_size"] == 1000
        assert config["latent_dim"] == 32

    def test_non_shape_derived_fields_are_the_released_defaults(self):
        config = detect_unet_config(_yue2_lm_state_dict())
        assert config["rope_theta"] == 1_000_000.0
        assert config["rms_norm_eps"] == 1e-6
        assert config["max_position_embeddings"] == 24576
        assert config["max_latent_frames"] == 24576
        assert config["timestep_shift"] == 1.0

    def test_only_one_of_the_two_signature_keys_is_not_enough(self):
        sd = _yue2_lm_state_dict()
        del sd["vae2llm.weight"]
        assert detect_unet_config(sd) is None

    def test_a_foreign_checkpoint_is_not_claimed(self):
        assert detect_unet_config({"double_blocks.0.img_attn.norm.key_norm.scale": _meta((1,))}) is None


class TestVAEDetection:
    def test_default_shapes_detect_as_yue2(self):
        config = detect_yue2_vae_config(_yue2_vae_state_dict())
        assert config is not None
        assert config["latent_dim"] == 64
        assert config["out_channels"] == 2
        assert config["sample_rate"] == 48000
        assert config["downsampling_ratio"] == 1920

    def test_a_different_latent_dim_and_out_channels_are_read_from_shape(self):
        sd = _yue2_vae_state_dict(latent_dim=32, out_channels=1)
        config = detect_yue2_vae_config(sd)
        assert config["latent_dim"] == 32
        assert config["out_channels"] == 1

    def test_only_one_of_the_two_signature_keys_is_not_enough(self):
        sd = _yue2_vae_state_dict()
        del sd["decoder.layers.8.weight_v"]
        assert detect_yue2_vae_config(sd) is None

    def test_the_minimax_music3_dav_vocoder_is_not_mistaken_for_yue2(self):
        """Distinct attribute path: MiniMax's DAV nests under ``decoder.model.{N}``, never ``decoder.layers.{N}``."""
        sd = {
            "dec_in_proj.weight": _meta((1024, 64, 1)),
            "decoder.model.0.weight_v": _meta((1536, 1024, 7)),
            "decoder.model.6.weight_v": _meta((2, 64, 7)),
        }
        assert detect_yue2_vae_config(sd) is None

    def test_an_unrelated_checkpoint_is_not_claimed(self):
        assert detect_yue2_vae_config({"encoder.conv_in.weight": _meta((128, 3, 3, 3))}) is None
