"""Tests for model_loader/minimax_music3's own fused-TE-file loader
(te_loader.py) -- the part of S5 that is genuinely new: tokenizer_json
capture BEFORE strip, the module never seeing that key, and the resulting
NativeModel's `.module`/`.tokenizer` wiring.

Uses a TINY (hidden_size=8, num_layers=1) REAL (non-meta) state dict built
from the arch module's own `state_dict()` -- the exact same tiny pruned
config already validated by the committed S1/S2 real-header dry-run test
(`tests/platform/runtime/native/arch/test_minimax_music3_real_header_dry_run.py`'s
`test_pruned_and_full_headers_produce_disjoint_embedding_key_sets`), so
`detect_te_config` recovers the same config back out of the real tensor
shapes -- this exercises the actual `detect_te_config` -> `from_config` ->
`load_into_module` path `te_loader.py` runs in production, just at a scale
that costs nothing to allocate.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import torch
from safetensors.torch import save_file

from src.pipelines.pipes.model_loader.minimax_music3.te_loader import load_minimax_music3_te
from src.platform.runtime.native.arch.minimax_music3.config import MiniMaxMusic3TextEncoderConfig
from src.platform.runtime.native.arch.minimax_music3.lm import MiniMaxMusic3AudioLM
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from vendor.gpl.comfyui.ops import disable_weight_init

_TINY_CFG = MiniMaxMusic3TextEncoderConfig(
    pruned_embeddings=True, pruned_lm_head=True, merged_qkv=True, merged_mlp=True,
    decoder_merged_qkv=True, decoder_merged_mlp=True,
    hidden_size=8, intermediate_size=16, num_layers=1, head_dim=4,
    decoder_intermediate_size=12, decoder_num_layers=1,
    audio_vocab_size=4, num_codebooks=8,
    # num_attention_heads/num_key_value_heads/decoder_num_heads/decoder_head_dim
    # left at their PRODUCTION defaults on purpose: te_detect.py's
    # minimax_music3 branch never populates these keys in the detected
    # config (config.py's own docstring: "architecture constants no
    # released checkpoint varies"), so `from_detect_config` always falls
    # back to them regardless of checkpoint scale. This fixture is reloaded
    # through the REAL `detect_te_config` -> `from_detect_config` path
    # below, so it has to be self-consistent with that fallback, not with
    # whatever head count would make the fixture smaller.
)

_TOKENIZER_JSON_BYTES = b'{"fake": "tokenizer json bytes -- never actually parsed, see the module patch below"}'


def _tiny_state_dict(*, with_tokenizer: bool = True) -> dict:
    module = MiniMaxMusic3AudioLM(_TINY_CFG, disable_weight_init, dtype=torch.bfloat16)
    # `nn.Parameter(torch.empty(...))` is UNINITIALIZED memory -- zero every
    # tensor explicitly, or load_into_module's own NaN/Inf integrity check
    # trips on whatever garbage happened to be there.
    sd = {k: torch.zeros_like(v) for k, v in module.state_dict().items()}
    if with_tokenizer:
        sd["tokenizer_json"] = torch.frombuffer(bytearray(_TOKENIZER_JSON_BYTES), dtype=torch.uint8).clone()
    return sd


def _write(tmp_path, name: str, sd: dict):
    path = tmp_path / name
    save_file(sd, str(path))
    return str(path)


def _run_loader(tmp_path):
    path = _write(tmp_path, "minimax_music3_te_tiny.safetensors", _tiny_state_dict())
    with patch(
        "src.pipelines.pipes.model_loader.minimax_music3.te_loader.MiniMaxMusic3Tokenizer"
    ) as fake_tokenizer_cls:
        fake_tokenizer_cls.return_value = MagicMock(name="tokenizer_instance")
        model = load_minimax_music3_te(path, device="cpu")
    return model, fake_tokenizer_cls


def test_tokenizer_bytes_captured_before_strip(tmp_path):
    _model, fake_tokenizer_cls = _run_loader(tmp_path)
    fake_tokenizer_cls.assert_called_once()
    (captured_bytes,), _kwargs = fake_tokenizer_cls.call_args
    assert captured_bytes == _TOKENIZER_JSON_BYTES


def test_tokenizer_attached_to_native_model(tmp_path):
    model, fake_tokenizer_cls = _run_loader(tmp_path)
    assert isinstance(model, NativeModel)
    assert model.tokenizer is fake_tokenizer_cls.return_value


def test_module_never_sees_tokenizer_json_key(tmp_path):
    model, _fake = _run_loader(tmp_path)
    assert isinstance(model.module, MiniMaxMusic3AudioLM)
    # A strict load would have raised NativeEngineLoadIntegrityError on an
    # unexpected key if `tokenizer_json` had reached `load_into_module` --
    # reaching this line at all is half the proof; the other half is that
    # the config it built with matches the tiny checkpoint exactly.
    assert model.module.cfg.hidden_size == 8
    assert model.module.cfg.num_layers == 1
    assert model.module.cfg.pruned_embeddings is True


def test_unrecognised_checkpoint_raises(tmp_path):
    path = _write(tmp_path, "not_music3.safetensors", {"some.other.weight": torch.zeros(4, 4)})
    try:
        load_minimax_music3_te(path, device="cpu")
        assert False, "expected NativeEngineUnsupportedError"
    except NativeEngineUnsupportedError:
        pass


def test_missing_tokenizer_json_raises(tmp_path):
    path = _write(tmp_path, "no_tokenizer.safetensors", _tiny_state_dict(with_tokenizer=False))
    try:
        load_minimax_music3_te(path, device="cpu")
        assert False, "expected NativeEngineUnsupportedError"
    except NativeEngineUnsupportedError:
        pass
