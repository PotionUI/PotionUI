"""Detection + registry tests for MiniMax-Music3, driven by the REAL Comfy-Org
repack safetensors headers (``ai/minimax_music3/*_header.json`` -- key/shape/
dtype metadata only, fetched via range request; no weights).

State dicts are built on ``device="meta"`` (shape/dtype only, no allocation) --
both detectors here read only ``.shape``, never values, so this exercises the
real shape-derivation arithmetic against the real numbers without materializing
~24 GB of tensors.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.te_detect import detect_te_config
from src.platform.runtime.native.detect.unet_detect import detect_unet_config

_MUSIC3_DIR = Path("ai/minimax_music3")
_H3_DIR = Path("ai/minimax_h3")

_DTYPES = {
    "F32": torch.float32,
    "F16": torch.float16,
    "BF16": torch.bfloat16,
    "I8": torch.int8,
    "U8": torch.uint8,
}


def _load_header(path: Path) -> dict:
    if not path.exists():
        pytest.skip(f"{path} not present (fetched once via range request; not part of the repo checkout)")
    with path.open() as f:
        header = json.load(f)
    header.pop("__metadata__", None)
    return header


def _meta_state_dict(header: dict) -> dict[str, torch.Tensor]:
    return {
        key: torch.empty(entry["shape"], dtype=_DTYPES[entry["dtype"]], device="meta")
        for key, entry in header.items()
    }


class TestDiTDetectionOnRealHeader:
    def test_fp16_header_detects_minimax_music3(self):
        header = _load_header(_MUSIC3_DIR / "minimax_music3_dit_fp16_header.json")
        sd = _meta_state_dict(header)
        config = detect_unet_config(sd)
        assert config is not None
        assert config["image_model"] == "minimax_music3_dit"
        assert config["hidden_size"] == 2048
        assert config["num_layers"] == 36
        assert config["num_attention_heads"] == 32
        assert config["attention_head_dim"] == 64
        assert config["ffn_inner_dim"] == 8192
        assert config["in_channels"] == 128
        assert config["condition_dim"] == 2048
        assert config["condition_hidden_dim"] == 4096
        assert config["num_condition_layers"] == 8
        # `inv_freq` has 16 rows (frequency pairs); `rotary_dim` counts rotated
        # head DIMENSIONS (2 per pair) -- doubled, matching
        # MiniMaxMusic3DitConfig's own field semantics, not the raw tensor shape.
        assert config["rotary_dim"] == 32
        # `timestep_features.weight` has 128 rows; `fourier_dim` is the
        # concatenated sin+cos width the embedding actually emits -- doubled.
        assert config["fourier_dim"] == 256

    def test_fp32_header_detects_the_same_shapes(self):
        """The fp32 repack is the same architecture at a different storage
        dtype -- shape derivation must not depend on dtype at all."""
        header = _load_header(_MUSIC3_DIR / "minimax_music3_dit_fp32_header.json")
        sd = _meta_state_dict(header)
        config = detect_unet_config(sd)
        assert config is not None
        assert config["image_model"] == "minimax_music3_dit"
        assert config["hidden_size"] == 2048
        assert config["num_layers"] == 36

    def test_int8_convrot_header_still_detects_by_key_presence(self):
        """Quantised Linears keep their key names and shapes (int8 is not
        nvfp4-packed -- codes are stored 1:1, unlike nvfp4's half-width pack),
        so shape-derivation must still land on the real values even though the
        signature weight's dtype is I8, not float."""
        header = _load_header(_MUSIC3_DIR / "minimax_music3_dit_int8_convrot_header.json")
        sd = _meta_state_dict(header)
        config = detect_unet_config(sd)
        assert config is not None
        assert config["image_model"] == "minimax_music3_dit"
        assert config["hidden_size"] == 2048

    def test_registry_matches_the_detected_config(self):
        header = _load_header(_MUSIC3_DIR / "minimax_music3_dit_fp16_header.json")
        sd = _meta_state_dict(header)
        config = detect_unet_config(sd)
        spec = match_model_spec(config)
        assert spec.family == "minimax_music3"
        assert spec.variant == "music3"
        assert spec.sampling_settings["steps"] == 30
        assert spec.sampling_settings["cfg"] == 1.7

    def test_registrys_model_class_actually_accepts_the_detected_config(self):
        """End-to-end proof that the detector's ``image_model`` discriminator
        string agrees with the arch class it resolves to (S3's
        ``MiniMaxMusic3DitConfig.from_detect_config`` guards on its own
        ``MINIMAX_MUSIC3_DIT`` constant, independently of this detector) --
        not just two hardcoded literals that happen to match today."""
        from src.platform.runtime.native.arch.minimax_music3 import (
            MiniMaxMusic3DitConfig,
            MiniMaxMusic3Model,
        )

        header = _load_header(_MUSIC3_DIR / "minimax_music3_dit_fp16_header.json")
        sd = _meta_state_dict(header)
        config = detect_unet_config(sd)
        spec = match_model_spec(config)
        assert spec.resolve_model_class() is MiniMaxMusic3Model
        dit_config = MiniMaxMusic3DitConfig.from_detect_config(config)
        # Every one of these is `config.get(key, default)` on the model side --
        # asserting against the DETECTED value (not the arch module's own
        # default) is what actually catches a key-name mismatch between the
        # two independently-written modules; a mismatch silently falls back
        # to the default instead of raising, and every one of these detected
        # values happens to equal the real checkpoint's default too, so this
        # only fails if the wiring is broken, not if the numbers are wrong.
        assert dit_config.in_channels == config["in_channels"] == 128
        assert dit_config.condition_dim == config["condition_dim"] == 2048
        assert dit_config.condition_hidden_dim == config["condition_hidden_dim"] == 4096
        assert dit_config.num_condition_layers == config["num_condition_layers"] == 8
        assert dit_config.num_layers == config["num_layers"] == 36
        assert dit_config.num_attention_heads == config["num_attention_heads"] == 32
        assert dit_config.attention_head_dim == config["attention_head_dim"] == 64
        assert dit_config.ffn_inner_dim == config["ffn_inner_dim"] == 8192
        assert dit_config.rotary_dim == config["rotary_dim"] == 32
        assert dit_config.fourier_dim == config["fourier_dim"] == 256
        assert spec.sampling_settings["ar_cfg"] == 1.5

    def test_a_minimax_h3_dit_header_does_not_match(self):
        """Bite-check: MiniMax-H3's DiT is a completely different key
        namespace (`blocks.*`/`video_patch_proj`) -- it must not be
        misdetected as Music3, and Music3's signature keys must not
        accidentally appear in H3's real header."""
        header = _load_header(_H3_DIR / "full_bf16_header.json")
        sd = _meta_state_dict(header)
        config = detect_unet_config(sd)
        assert config is not None
        assert config["image_model"] == "minimax_h3"


class TestTextEncoderDetectionOnRealHeader:
    def test_pruned_bf16_header_detects_the_pruned_layout(self):
        header = _load_header(_MUSIC3_DIR / "minimax_music3_text_encoder_pruned_bf16_header.json")
        sd = _meta_state_dict(header)
        config = detect_te_config(sd)
        assert config is not None
        assert config["te_type"] == "minimax_music3"
        assert config["hidden_size"] == 4096
        assert config["intermediate_size"] == 12288
        assert config["num_layers"] == 36
        assert config["head_dim"] == 128
        assert config["decoder_intermediate_size"] == 6144
        assert config["decoder_num_layers"] == 4
        assert config["audio_vocab_size"] == 1024
        assert config["num_codebooks"] == 8
        assert config["merged_qkv"] is True
        assert config["merged_mlp"] is True
        assert config["decoder_merged_qkv"] is True
        assert config["decoder_merged_mlp"] is True
        assert config["pruned_embeddings"] is True
        assert config["pruned_lm_head"] is True

    def test_full_bf16_header_detects_the_full_layout(self):
        """The un-pruned file flips every one of the five layout booleans the
        other way -- this is the real-world proof the two shapes genuinely
        diverge and neither can be assumed from the other."""
        header = _load_header(_MUSIC3_DIR / "minimax_music3_text_encoder_bf16_header.json")
        sd = _meta_state_dict(header)
        config = detect_te_config(sd)
        assert config is not None
        assert config["te_type"] == "minimax_music3"
        assert config["hidden_size"] == 4096
        assert config["intermediate_size"] == 12288
        assert config["num_layers"] == 36
        assert config["decoder_num_layers"] == 4
        assert config["audio_vocab_size"] == 1024
        assert config["num_codebooks"] == 8
        assert config["merged_qkv"] is False
        assert config["merged_mlp"] is False
        assert config["decoder_merged_qkv"] is False
        assert config["decoder_merged_mlp"] is False
        assert config["pruned_embeddings"] is False
        assert config["pruned_lm_head"] is False

    def test_int8_convrot_header_still_detects_by_key_presence(self):
        header = _load_header(_MUSIC3_DIR / "minimax_music3_text_encoder_pruned_int8_convrot_header.json")
        sd = _meta_state_dict(header)
        config = detect_te_config(sd)
        assert config is not None
        assert config["te_type"] == "minimax_music3"
        assert config["merged_qkv"] is True
        assert config["pruned_embeddings"] is True

    def test_full_layout_is_not_misdetected_as_plain_qwen3(self):
        """Bite-check for the exact collision this branch exists to prevent:
        the full-layout header has `model.embed_tokens.weight` AND per-head
        `q_norm`/`k_norm` -- structurally a superset of a bare Qwen3-8B
        checkpoint. Without the audio_decoder/tokenizer_json branch running
        FIRST, this header would silently detect as `te_type="qwen3"`."""
        header = _load_header(_MUSIC3_DIR / "minimax_music3_text_encoder_bf16_header.json")
        sd = _meta_state_dict(header)
        config = detect_te_config(sd)
        assert config["te_type"] == "minimax_music3"
        assert config["te_type"] != "qwen3"

    def test_a_minimax_h3_te_header_does_not_match(self):
        """Bite-check the other direction: H3's Qwen3-VL-32B TE has neither
        `audio_decoder.norm.weight` nor an embedded `tokenizer_json` and must
        keep detecting as its own family, unaffected by this new branch."""
        header = _load_header(_H3_DIR / "te_bf16_header.json")
        sd = _meta_state_dict(header)
        config = detect_te_config(sd)
        assert config is not None
        assert config["te_type"] != "minimax_music3"
