"""Meta-device key-parity dry run for MiniMax-Music3's AR core
(``MiniMaxMusic3AudioLM``), driven by the REAL Comfy-Org repack safetensors
headers (``ai/minimax_music3/minimax_music3_text_encoder_{,pruned_}bf16_header.json``
— no weights, only key/shape/dtype metadata fetched via range request).

Unlike the H3 VAE dry-run template this mirrors, nothing here needs
shrinking: every module width this class builds is either detected straight
off the real header's own tensor shapes, or a hardcoded constant that IS the
real production value already (``AUDIO_CODE_OFFSET``/``SEMANTIC_VOCAB_SIZE``
from the prompt contract, ``_FULL_VOCAB_SIZE`` in ``lm.py``) — so the module
is built at REAL scale on the meta device (``torch.device("meta")``, the same
idiom ``engine.py``'s own DiT loader uses), which costs no memory at all and
lets this test assert EXACT shape equality, not just key presence.

This is exactly the class of bug the pruned/full divergence produces: S1's
five independent layout booleans mean the wrong combination silently builds
a module whose key SET doesn't match the checkpoint it will actually be
loaded from — caught here before any real load path exists (that's S5's).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.minimax_music3.config import MiniMaxMusic3TextEncoderConfig
from src.platform.runtime.native.arch.minimax_music3.lm import MiniMaxMusic3AudioLM
from src.platform.runtime.native.detect.te_detect import detect_te_config
from vendor.gpl.comfyui.ops import disable_weight_init

_HEADER_DIR = Path("ai/minimax_music3")
_PRUNED_HEADER = "minimax_music3_text_encoder_pruned_bf16_header.json"
_FULL_HEADER = "minimax_music3_text_encoder_bf16_header.json"

_DTYPES = {"BF16": torch.bfloat16, "F32": torch.float32, "F16": torch.float16, "U8": torch.uint8}


def _load_real_header(name: str) -> dict:
    path = _HEADER_DIR / name
    if not path.exists():
        pytest.skip(f"{path} not present (fetched once via range request; not part of the repo checkout)")
    with path.open() as f:
        header = json.load(f)
    header.pop("__metadata__", None)
    return header


def _meta_state_dict(header: dict) -> dict[str, torch.Tensor]:
    """Real shapes, real dtypes, ``device="meta"`` — no bytes ever allocated,
    so detection (which only reads presence/``.shape``) sees the real layout
    without downloading or materializing a single weight.
    """
    return {
        key: torch.empty(entry["shape"], dtype=_DTYPES[entry["dtype"]], device="meta")
        for key, entry in header.items()
    }


def _build_module(header_name: str) -> tuple[dict, MiniMaxMusic3AudioLM]:
    header = _load_real_header(header_name)
    sd = _meta_state_dict(header)
    config = detect_te_config(sd)
    assert config is not None and config["te_type"] == "minimax_music3"
    cfg = MiniMaxMusic3TextEncoderConfig.from_detect_config(config)
    with torch.device("meta"):
        module = MiniMaxMusic3AudioLM(cfg, disable_weight_init)
    return header, module


@pytest.mark.parametrize("header_name, pruned", [(_PRUNED_HEADER, True), (_FULL_HEADER, False)])
class TestMetaModuleMatchesRealHeader:
    def test_layout_booleans_detected_as_expected(self, header_name, pruned):
        header, module = _build_module(header_name)
        assert module.cfg.pruned_embeddings is pruned
        assert module.cfg.pruned_lm_head is pruned
        assert module.cfg.merged_qkv is pruned
        assert module.cfg.merged_mlp is pruned
        assert module.cfg.decoder_merged_qkv is pruned
        assert module.cfg.decoder_merged_mlp is pruned

    def test_exact_key_set(self, header_name, pruned):
        """``tokenizer_json`` is captured Gemma4-style by the loader before
        the state dict reaches this module (see ``prompt.py``'s
        ``MiniMaxMusic3Tokenizer`` docstring) — it is never one of this
        module's own state-dict keys, so it's the one header key excluded
        here, not a gap in this module."""
        header, module = _build_module(header_name)
        module_keys = set(module.state_dict().keys())
        header_keys = set(header.keys()) - {"tokenizer_json"}
        assert module_keys == header_keys

    def test_exact_shapes_match(self, header_name, pruned):
        """Every checkpoint key EXACTLY (not just its rank), at real scale —
        possible here (unlike the H3 VAE template) because meta-device
        construction costs nothing regardless of width."""
        header, module = _build_module(header_name)
        sd = module.state_dict()
        for key, tensor in sd.items():
            assert list(tensor.shape) == header[key]["shape"], key


def test_pruned_and_full_headers_produce_disjoint_embedding_key_sets():
    """Bite-check for the pruned/full divergence itself: build both layouts
    and confirm neither module's key set is a subset of the other's around
    the five layout booleans -- if the layout dispatch degenerated to always
    building one layout regardless of ``cfg.pruned_*``, this would catch it
    even without the real headers (no skip possible)."""
    pruned_cfg = MiniMaxMusic3TextEncoderConfig(
        pruned_embeddings=True, pruned_lm_head=True, merged_qkv=True, merged_mlp=True,
        decoder_merged_qkv=True, decoder_merged_mlp=True,
        hidden_size=8, intermediate_size=16, num_layers=1, head_dim=4,
        num_attention_heads=2, num_key_value_heads=1,
        decoder_intermediate_size=12, decoder_num_layers=1, decoder_num_heads=2, decoder_head_dim=4,
        audio_vocab_size=4, num_codebooks=8,
    )
    full_cfg = MiniMaxMusic3TextEncoderConfig(
        pruned_embeddings=False, pruned_lm_head=False, merged_qkv=False, merged_mlp=False,
        decoder_merged_qkv=False, decoder_merged_mlp=False,
        hidden_size=8, intermediate_size=16, num_layers=1, head_dim=4,
        num_attention_heads=2, num_key_value_heads=1,
        decoder_intermediate_size=12, decoder_num_layers=1, decoder_num_heads=2, decoder_head_dim=4,
        audio_vocab_size=4, num_codebooks=8,
    )
    with torch.device("meta"):
        pruned_keys = set(MiniMaxMusic3AudioLM(pruned_cfg, disable_weight_init).state_dict().keys())
        full_keys = set(MiniMaxMusic3AudioLM(full_cfg, disable_weight_init).state_dict().keys())
    assert "model.embed_tokens_prefill.weight" in pruned_keys
    assert "model.embed_tokens_prefill.weight" not in full_keys
    assert "model.embed_tokens.weight" in full_keys
    assert "model.embed_tokens.weight" not in pruned_keys
    assert "model.lm_head_pruned.weight" in pruned_keys
    assert "model.lm_head.weight" in full_keys
    assert "model.layers.0.self_attn.qkv_proj.weight" in pruned_keys
    assert "model.layers.0.self_attn.q_proj.weight" in full_keys
