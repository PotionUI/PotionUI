"""Tests for adopting a single-file text encoder as a chat model
(gemma3 adoption, fp8 dequant, single-file quantization).

CPU-only, no downloads. Candidates are synthetic single-file safetensors: for
the header-level scanner/gate/detection tests, a hand-built dict of a few
correctly-KEYED (near-empty) tensors; for the end-to-end adoption tests, a tiny
randomly-initialized Qwen3ForCausalLM / Gemma3ForCausalLM saved as ONE
safetensors with the comfy-style `model.*` layout and NO config.json/tokenizer,
exactly like a real TE repack. The bundled qwen3 tokenizer assets in-repo serve
the qwen3 chat tokenizer; the gemma3 chat tokenizer is NEVER downloaded in
these tests — a tiny fixture tokenizer.json/tokenizer_config.json is written
directly to the on-disk cache location `ensure_gemma3_chat_tokenizer` would
otherwise populate.
"""

from __future__ import annotations

import pytest
import torch
from safetensors.torch import save_file

from src.features.llm.native_te_adoption import (
    AdoptedTEEntry,
    GEMMA3_CHAT_TOKENIZER_REPO,
    _dequantize_fp8_state_dict,
    _dequantize_nvfp4_state_dict,
    _detect_quantization,
    _eligibility,
    _family_from_keys,
    build_adopted_te,
    ensure_gemma3_chat_tokenizer,
    gemma3_chat_tokenizer_dir,
    gemma3_chat_tokenizer_ready,
    list_adopted_te_checkpoints,
    read_safetensors_header,
    resolve_adopted_te_path,
)


@pytest.fixture(autouse=True)
def _no_real_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None, raising=False)


def _write_synthetic(path, keys_shapes, dtype=torch.bfloat16, extra=None):
    """Write a safetensors with the given {key: shape} as tiny tensors."""
    sd = {k: torch.zeros(*shape, dtype=dtype) for k, shape in keys_shapes.items()}
    if extra:
        sd.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_file(sd, str(path), metadata={"format": "pt"})


_QWEN3_MIN_KEYS = {
    "model.embed_tokens.weight": (64, 32),
    "model.layers.0.self_attn.q_norm.weight": (8,),
    "model.layers.0.self_attn.q_proj.weight": (16, 32),
    "model.layers.0.self_attn.k_proj.weight": (8, 32),
    "model.layers.0.mlp.gate_proj.weight": (48, 32),
    "model.norm.weight": (32,),
}
_GEMMA3_MIN_KEYS = {
    "model.embed_tokens.weight": (64, 32),
    "model.layers.0.pre_feedforward_layernorm.weight": (32,),
    "model.layers.0.self_attn.q_norm.weight": (8,),
    "model.norm.weight": (32,),
}


# --- header read ----------------------------------------------------------

def test_read_safetensors_header_lists_keys_dtype_shape_without_loading(tmp_path):
    p = tmp_path / "x.safetensors"
    _write_synthetic(p, {"model.embed_tokens.weight": (64, 32)})
    header = read_safetensors_header(p)
    assert "model.embed_tokens.weight" in header
    assert header["model.embed_tokens.weight"]["shape"] == [64, 32]
    assert header["model.embed_tokens.weight"]["dtype"] == "BF16"


# --- family detection -----------------------------------------------------

def test_family_detects_qwen3():
    assert _family_from_keys(set(_QWEN3_MIN_KEYS)) == "qwen3"


def test_family_detects_gemma3_before_qwen3():
    # Gemma-3 also has q_norm; the pre_feedforward_layernorm key must win.
    assert _family_from_keys(set(_GEMMA3_MIN_KEYS)) == "gemma3"


def test_family_none_for_non_causal_te():
    # A T5/CLIP-shaped dict is not a causal LM and must be skipped.
    assert _family_from_keys({"shared.weight", "encoder.block.0.layer.0.SelfAttention.q.weight"}) is None
    assert _family_from_keys({"text_model.embeddings.token_embedding.weight"}) is None


# --- lm_head / eligibility gate -------------------------------------------

def test_eligibility_qwen3_bf16_with_lm_head_is_adoptable():
    adoptable, reason = _eligibility("qwen3", has_lm_head=True, tied=False)
    assert adoptable is True
    assert reason is None


def test_eligibility_qwen3_absent_lm_head_tied_is_adoptable():
    adoptable, reason = _eligibility("qwen3", has_lm_head=False, tied=True)
    assert adoptable is True


def test_eligibility_qwen3_absent_lm_head_untied_is_rejected():
    adoptable, reason = _eligibility("qwen3", has_lm_head=False, tied=False)
    assert adoptable is False
    assert "lm_head" in reason and "untied" in reason


def test_eligibility_gemma3_gated_without_tokenizer_assets():
    adoptable, reason = _eligibility("gemma3", has_lm_head=True, tied=True, gemma3_tokenizer_ready=False)
    assert adoptable is False
    assert "tokenizer" in reason and "not downloaded yet" in reason
    assert GEMMA3_CHAT_TOKENIZER_REPO in reason


def test_eligibility_gemma3_adoptable_once_tokenizer_assets_present():
    adoptable, reason = _eligibility("gemma3", has_lm_head=True, tied=True, gemma3_tokenizer_ready=True)
    assert adoptable is True
    assert reason is None


def test_eligibility_no_longer_gates_on_fp8():
    """A fp8-scaled checkpoint is eligible on the same terms as bf16
    (dequantized to bf16 at adoption time) — fp8 isn't even a parameter here
    anymore, only family/lm_head/tied/tokenizer-readiness matter."""
    adoptable, reason = _eligibility("qwen3", has_lm_head=True, tied=True)
    assert adoptable is True
    assert reason is None


# --- scanner + listing ----------------------------------------------------

def test_list_adopted_skips_non_safetensors_and_non_causal(tmp_path):
    _write_synthetic(tmp_path / "clip" / "t5.safetensors", {"shared.weight": (64, 32)})
    (tmp_path / "clip" / "notes.txt").write_text("nope")
    assert list_adopted_te_checkpoints(tmp_path) == []


def test_list_adopted_flags_qwen3_adoptable(tmp_path):
    _write_synthetic(tmp_path / "clip" / "qwen3-te.safetensors", _QWEN3_MIN_KEYS,
                     extra={"lm_head.weight": torch.zeros(64, 32, dtype=torch.bfloat16)})
    entries = list_adopted_te_checkpoints(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e.name == "clip/qwen3-te.safetensors"
    assert e.model_type == "qwen3"
    assert e.has_lm_head is True
    assert e.adoptable is True
    assert e.reason is None


def test_list_adopted_flags_gemma3_gated_without_tokenizer_assets(tmp_path):
    _write_synthetic(tmp_path / "clip" / "gemma3-te.safetensors", _GEMMA3_MIN_KEYS)
    entries = list_adopted_te_checkpoints(tmp_path)
    assert entries[0].model_type == "gemma3"
    assert entries[0].adoptable is False
    assert "tokenizer" in entries[0].reason
    assert "not downloaded yet" in entries[0].reason


def test_list_adopted_gemma3_gate_lifts_once_tokenizer_assets_are_present(tmp_path):
    """The gate is informational, not permanent — once the on-demand
    chat tokenizer assets land at clip/_chat_tokenizer/gemma3/, the SAME
    checkpoint file becomes chat-capable without touching the checkpoint."""
    _write_synthetic(tmp_path / "clip" / "gemma3-te.safetensors", _GEMMA3_MIN_KEYS)
    d = gemma3_chat_tokenizer_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "tokenizer.json").write_text("{}")
    (d / "tokenizer_config.json").write_text("{}")

    entries = list_adopted_te_checkpoints(tmp_path)
    assert entries[0].adoptable is True
    assert entries[0].reason is None


def test_gemma3_chat_tokenizer_ready_false_until_both_files_present(tmp_path):
    assert gemma3_chat_tokenizer_ready(tmp_path) is False
    d = gemma3_chat_tokenizer_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "tokenizer.json").write_text("{}")
    assert gemma3_chat_tokenizer_ready(tmp_path) is False  # tokenizer_config.json still missing
    (d / "tokenizer_config.json").write_text("{}")
    assert gemma3_chat_tokenizer_ready(tmp_path) is True


def test_ensure_gemma3_chat_tokenizer_fetches_via_download_manager(tmp_path):
    """No network: the download manager itself is a stand-in recording the
    call, exactly the seam ``ensure_gemma3_chat_tokenizer`` is supposed to
    drive (``DownloadManager.ensure_local_hf_repo`` — src/features/downloads)."""
    calls = []

    class _FakeDownloadManager:
        def ensure_local_hf_repo(self, repo_id, target_dir, allow_patterns=None, **kwargs):
            calls.append((repo_id, target_dir, allow_patterns))

    target = ensure_gemma3_chat_tokenizer(_FakeDownloadManager(), tmp_path)

    assert target == gemma3_chat_tokenizer_dir(tmp_path)
    assert target.is_dir()  # created even though the fake download wrote nothing
    assert len(calls) == 1
    repo_id, target_dir, allow_patterns = calls[0]
    assert repo_id == GEMMA3_CHAT_TOKENIZER_REPO
    assert target_dir == str(gemma3_chat_tokenizer_dir(tmp_path))
    assert set(allow_patterns) == {"tokenizer.json", "tokenizer_config.json", "chat_template.jinja"}


def test_list_adopted_rejects_untied_qwen3_missing_lm_head(tmp_path):
    # hidden_size 4096 -> untied variant; no lm_head -> not adoptable.
    keys = dict(_QWEN3_MIN_KEYS)
    keys["model.embed_tokens.weight"] = (64, 4096)
    keys["model.layers.0.self_attn.q_proj.weight"] = (16, 4096)
    keys["model.layers.0.self_attn.k_proj.weight"] = (8, 4096)
    keys["model.layers.0.mlp.gate_proj.weight"] = (48, 4096)
    keys["model.norm.weight"] = (4096,)
    _write_synthetic(tmp_path / "clip" / "qwen3-8b-te.safetensors", keys)
    e = list_adopted_te_checkpoints(tmp_path)[0]
    assert e.tied is False
    assert e.has_lm_head is False
    assert e.adoptable is False
    assert "logits" in e.reason


def test_list_adopted_detects_fp8_marker_but_no_longer_gates_it(tmp_path):
    """fp8 is metadata on the listing (`e.fp8`), not a rejection —
    this fixture is tied qwen3 (hidden_size 32 < 4096) with no lm_head, which
    is adoptable via the tied-reconstruction path regardless of fp8."""
    _write_synthetic(tmp_path / "clip" / "qwen3-fp8.safetensors", _QWEN3_MIN_KEYS,
                     extra={"scaled_fp8": torch.zeros(1, dtype=torch.float32)})
    e = list_adopted_te_checkpoints(tmp_path)[0]
    assert e.fp8 is True
    assert e.adoptable is True
    assert e.reason is None


# --- resolve / traversal guard --------------------------------------------

def test_resolve_adopted_te_path_success(tmp_path):
    p = tmp_path / "clip" / "qwen3-te.safetensors"
    _write_synthetic(p, {"model.embed_tokens.weight": (8, 8)})
    assert resolve_adopted_te_path("clip/qwen3-te.safetensors", tmp_path) == str(p.resolve())


def test_resolve_adopted_te_path_rejects_traversal(tmp_path):
    (tmp_path / "clip").mkdir(parents=True)
    with pytest.raises(ValueError, match="Invalid adopted text-encoder name"):
        resolve_adopted_te_path("clip/../../etc/passwd", tmp_path)


def test_resolve_adopted_te_path_missing_raises(tmp_path):
    (tmp_path / "clip").mkdir(parents=True)
    with pytest.raises(ValueError, match="not found"):
        resolve_adopted_te_path("clip/nope.safetensors", tmp_path)


# --- end-to-end adoption of a tiny real Qwen3 single-file -----------------

@pytest.fixture(scope="module")
def tiny_qwen3_single_file(tmp_path_factory):
    """Build a tiny Qwen3ForCausalLM, save its state dict as ONE comfy-style
    safetensors (model.* + lm_head), sized to the bundled qwen3 tokenizer's
    vocab so real tokenization never indexes out of range."""
    from transformers import AutoTokenizer, Qwen3Config, Qwen3ForCausalLM
    from src.features.llm.native_te_adoption import _CHAT_TOKENIZER_DIRS

    tok = AutoTokenizer.from_pretrained(str(_CHAT_TOKENIZER_DIRS["qwen3"]), local_files_only=True)
    vocab = len(tok)
    config = Qwen3Config(
        vocab_size=vocab, hidden_size=32, intermediate_size=64,
        num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2,
        head_dim=8, max_position_embeddings=64, tie_word_embeddings=False,
    )
    model = Qwen3ForCausalLM(config)
    model.eval()
    sd = {k: v.clone().contiguous() for k, v in model.state_dict().items()}

    base = tmp_path_factory.mktemp("te_models")
    (base / "clip").mkdir()
    full = base / "clip" / "qwen3-tiny-te.safetensors"
    save_file(sd, str(full), metadata={"format": "pt"})

    tied_sd = {k: v for k, v in sd.items() if k != "lm_head.weight"}
    tied = base / "clip" / "qwen3-tiny-tied-te.safetensors"
    save_file(tied_sd, str(tied), metadata={"format": "pt"})
    return base, str(full), str(tied)


def test_build_adopted_te_loads_and_generates(tiny_qwen3_single_file):
    _, full, _ = tiny_qwen3_single_file
    model, tokenizer, model_type = build_adopted_te(full)
    assert model_type == "qwen3"
    assert hasattr(tokenizer, "apply_chat_template")
    prompt = tokenizer.apply_chat_template(
        [{"role": "user", "content": "hi"}], add_generation_prompt=True, tokenize=False
    )
    inputs = tokenizer(prompt, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=2, do_sample=False)
    assert out.shape[-1] >= inputs["input_ids"].shape[-1]


def test_build_adopted_te_reconstructs_tied_lm_head(tiny_qwen3_single_file):
    """A checkpoint missing lm_head loads via the tied-embedding path."""
    _, _, tied = tiny_qwen3_single_file
    model, _tok, _mt = build_adopted_te(tied)
    # lm_head is tied to embed_tokens -> same underlying storage.
    assert model.lm_head.weight.data_ptr() == model.model.embed_tokens.weight.data_ptr()


def test_build_adopted_te_rejects_unsupported_family(tmp_path):
    """A non-causal TE (T5/CLIP-shaped) never reaches a family builder."""
    _write_synthetic(tmp_path / "clip" / "t5.safetensors", {"shared.weight": (64, 32)})
    with pytest.raises(ValueError, match="qwen3/gemma3 families only"):
        build_adopted_te(str(tmp_path / "clip" / "t5.safetensors"))


# --- end-to-end adoption of a tiny real Gemma3 single-file -------

@pytest.fixture(scope="module")
def tiny_gemma3_chat_tokenizer_files(tmp_path_factory):
    """A tiny FAKE gemma3 chat tokenizer — never downloaded. Stands in for
    what ``ensure_gemma3_chat_tokenizer`` would otherwise fetch: a
    ``tokenizer.json`` + ``tokenizer_config.json`` with a trivial chat
    template, sized to a small vocab so the tiny Gemma3 fixture model's
    vocab_size can match it exactly."""
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    vocab = {"[UNK]": 0, "[PAD]": 1, "[BOS]": 2, "[EOS]": 3}
    for i, w in enumerate(["hello", "world", "assistant", "user", "system", ":", "\n"], start=4):
        vocab[w] = i

    tok = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    fast_tok = PreTrainedTokenizerFast(
        tokenizer_object=tok, unk_token="[UNK]", pad_token="[PAD]", bos_token="[BOS]", eos_token="[EOS]"
    )
    fast_tok.chat_template = (
        "{% for m in messages %}{{ m.role }}: {{ m.content }}\n{% endfor %}"
        "{% if add_generation_prompt %}assistant:\n{% endif %}"
    )
    d = tmp_path_factory.mktemp("gemma3_chat_tokenizer")
    fast_tok.save_pretrained(d)
    return d, len(vocab) + 4  # a little headroom, matching the qwen3 fixture's own pattern


@pytest.fixture(scope="module")
def tiny_gemma3_single_file(tmp_path_factory, tiny_gemma3_chat_tokenizer_files):
    """A tiny real Gemma3ForCausalLM (head_dim MUST be 256 - a hardcoded family
    constant, not recovered from shapes, see ``_GEMMA3_HEAD_DIM``), saved as
    ONE comfy-style safetensors with NO config.json/tokenizer - exactly like a
    real LTX-2 Gemma3 TE repack. The fixture chat tokenizer is written to the
    on-disk location this checkpoint's OWN clip/_chat_tokenizer/gemma3/ cache
    dir resolves to (derived from the checkpoint's path, not passed in)."""
    from transformers import Gemma3TextConfig, Gemma3ForCausalLM

    tok_dir, vocab = tiny_gemma3_chat_tokenizer_files
    config = Gemma3TextConfig(
        vocab_size=vocab, hidden_size=32, intermediate_size=64, num_hidden_layers=1,
        num_attention_heads=1, num_key_value_heads=1, head_dim=256, query_pre_attn_scalar=256,
        rms_norm_eps=1e-6, sliding_window=1024, tie_word_embeddings=False,
        max_position_embeddings=64,
    )
    model = Gemma3ForCausalLM(config)
    model.eval()
    sd = {k: v.clone().contiguous() for k, v in model.state_dict().items()}

    base = tmp_path_factory.mktemp("gemma3_te_models")
    (base / "clip").mkdir()
    full = base / "clip" / "gemma3-tiny-te.safetensors"
    save_file(sd, str(full), metadata={"format": "pt"})

    # Land the fixture chat tokenizer at the exact cache dir build_adopted_te
    # derives from this checkpoint's own path — including chat_template.jinja,
    # which is where THIS transformers version actually reads the chat
    # template from (not embedded in tokenizer_config.json — see the
    # _GEMMA3_CHAT_TOKENIZER_OPTIONAL_FILES comment).
    chat_dir = base / "clip" / "_chat_tokenizer" / "gemma3"
    chat_dir.mkdir(parents=True)
    for name in ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja"):
        src = tok_dir / name
        if src.is_file():
            (chat_dir / name).write_bytes(src.read_bytes())

    tied_sd = {k: v for k, v in sd.items() if k != "lm_head.weight"}
    tied = base / "clip" / "gemma3-tiny-tied-te.safetensors"
    save_file(tied_sd, str(tied), metadata={"format": "pt"})
    return base, str(full), str(tied)


def test_build_adopted_te_loads_and_generates_gemma3(tiny_gemma3_single_file):
    _, full, _ = tiny_gemma3_single_file
    model, tokenizer, model_type = build_adopted_te(full)
    assert model_type == "gemma3"
    assert hasattr(tokenizer, "apply_chat_template")
    prompt = tokenizer.apply_chat_template(
        [{"role": "user", "content": "hi"}], add_generation_prompt=True, tokenize=False
    )
    inputs = tokenizer(prompt, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=2, do_sample=False)
    assert out.shape[-1] >= inputs["input_ids"].shape[-1]


def test_build_adopted_te_gemma3_reconstructs_tied_lm_head(tiny_gemma3_single_file):
    _, _, tied = tiny_gemma3_single_file
    model, _tok, model_type = build_adopted_te(tied)
    assert model_type == "gemma3"
    assert model.lm_head.weight.data_ptr() == model.model.embed_tokens.weight.data_ptr()


@pytest.fixture(scope="module")
def tiny_gemma3_multimodal_single_file(tiny_gemma3_single_file):
    """The SAME tiny Gemma3ForCausalLM state dict as ``tiny_gemma3_single_file``,
    repacked with the extra keys a real LTX-2 Gemma3 TE file carries alongside
    its LM weights: a SigLIP ``vision_model.*`` tower and a
    ``multi_modal_projector.*`` (plus an embedded ``spiece_model`` blob, as some
    real repacks also carry). Shapes are arbitrary — they get stripped before
    ``load_state_dict``, never loaded into the LM."""
    from safetensors.torch import load_file

    base, full, _tied = tiny_gemma3_single_file
    sd = load_file(full)
    sd["vision_model.embeddings.patch_embedding.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    sd["vision_model.encoder.layers.0.self_attn.q_proj.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    sd["multi_modal_projector.mm_input_projection_weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    sd["spiece_model"] = torch.zeros(4, dtype=torch.uint8)
    path = base / "clip" / "gemma3-tiny-multimodal-te.safetensors"
    save_file(sd, str(path), metadata={"format": "pt"})
    return str(path)


def test_list_adopted_gemma3_multimodal_repack_lists_as_adoptable(tiny_gemma3_multimodal_single_file):
    from pathlib import Path

    models_dir = Path(tiny_gemma3_multimodal_single_file).parent.parent
    entries = list_adopted_te_checkpoints(models_dir)
    matches = [e for e in entries if e.path == tiny_gemma3_multimodal_single_file]
    assert len(matches) == 1
    assert matches[0].model_type == "gemma3"
    assert matches[0].adoptable is True
    assert matches[0].reason is None


def test_build_adopted_te_strips_vision_and_projector_keys_gemma3(tiny_gemma3_multimodal_single_file):
    """A listed-as-adoptable multimodal repack must actually load, not fail
    inside load_state_dict on the vision tower / projector / spiece keys."""
    model, tokenizer, model_type = build_adopted_te(tiny_gemma3_multimodal_single_file)
    assert model_type == "gemma3"
    assert hasattr(tokenizer, "apply_chat_template")
    prompt = tokenizer.apply_chat_template(
        [{"role": "user", "content": "hi"}], add_generation_prompt=True, tokenize=False
    )
    inputs = tokenizer(prompt, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=2, do_sample=False)
    assert out.shape[-1] >= inputs["input_ids"].shape[-1]
    assert not hasattr(model, "vision_model")


def test_build_adopted_te_still_rejects_genuine_unknown_key_gemma3(tiny_gemma3_single_file):
    """A key outside the stripped set is a real integrity problem and must
    still fail loudly rather than being silently tolerated."""
    from safetensors.torch import load_file

    base, full, _tied = tiny_gemma3_single_file
    sd = load_file(full)
    sd["totally_unrecognized_junk_key"] = torch.zeros(2, 2, dtype=torch.bfloat16)
    path = base / "clip" / "gemma3-junk-te.safetensors"
    save_file(sd, str(path), metadata={"format": "pt"})

    with pytest.raises(ValueError, match="did not map cleanly"):
        build_adopted_te(str(path))


def test_build_adopted_te_gemma3_without_tokenizer_assets_raises_actionable_error(tmp_path):
    """Defence in depth: even if something calls build_adopted_te directly on
    a gemma3 checkpoint that was never gate-checked, the error names exactly
    what's missing rather than crashing inside AutoTokenizer.from_pretrained."""
    from transformers import Gemma3TextConfig, Gemma3ForCausalLM

    config = Gemma3TextConfig(
        vocab_size=32, hidden_size=32, intermediate_size=64, num_hidden_layers=1,
        num_attention_heads=1, num_key_value_heads=1, head_dim=256, query_pre_attn_scalar=256,
        tie_word_embeddings=True,
    )
    model = Gemma3ForCausalLM(config)
    (tmp_path / "clip").mkdir(parents=True)
    path = tmp_path / "clip" / "gemma3-no-tok.safetensors"
    save_file({k: v.clone().contiguous() for k, v in model.state_dict().items()}, str(path))

    with pytest.raises(ValueError, match="tokenizer assets are not present"):
        build_adopted_te(str(path))


# --- fp8-scaled dequant ------------------------------------------

def _quantize_tensor_fp8(weight: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Inverse of the dequant formula under test: quantize a bf16/fp32 tensor
    to fp8_e4m3fn + a per-tensor scale such that ``q.to(bf16) * scale``
    recovers the original value (within fp8 precision)."""
    e4m3_max = 448.0
    amax = weight.detach().abs().amax().clamp(min=1e-6).to(torch.float32)
    scale = amax / e4m3_max
    q = (weight.to(torch.float32) / scale).clamp(-e4m3_max, e4m3_max).to(torch.float8_e4m3fn)
    return q, scale


def test_dequantize_fp8_state_dict_modern_weight_scale_spelling():
    original = torch.randn(4, 4, dtype=torch.float32) * 2.0
    q, scale = _quantize_tensor_fp8(original)
    sd = {
        "model.layers.0.self_attn.q_proj.weight": q,
        "model.layers.0.self_attn.q_proj.weight_scale": scale,
        "model.norm.weight": torch.ones(4, dtype=torch.bfloat16),  # untouched, non-quantized
    }
    out = _dequantize_fp8_state_dict(sd)

    assert set(out) == {"model.layers.0.self_attn.q_proj.weight", "model.norm.weight"}
    assert out["model.layers.0.self_attn.q_proj.weight"].dtype == torch.bfloat16
    expected = (q.to(torch.bfloat16) * scale.to(torch.bfloat16))
    assert torch.equal(out["model.layers.0.self_attn.q_proj.weight"], expected)
    assert torch.equal(out["model.norm.weight"], torch.ones(4, dtype=torch.bfloat16))


def test_dequantize_fp8_state_dict_legacy_scale_weight_spelling():
    original = torch.randn(3, 3, dtype=torch.float32)
    q, scale = _quantize_tensor_fp8(original)
    sd = {"lm_head.weight": q, "lm_head.scale_weight": scale}
    out = _dequantize_fp8_state_dict(sd)
    assert out["lm_head.weight"].dtype == torch.bfloat16
    assert "lm_head.scale_weight" not in out


def test_dequantize_fp8_state_dict_drops_sidecar_keys():
    original = torch.randn(2, 2, dtype=torch.float32)
    q, scale = _quantize_tensor_fp8(original)
    sd = {
        "model.layers.0.mlp.gate_proj.weight": q,
        "model.layers.0.mlp.gate_proj.weight_scale": scale,
        "model.layers.0.mlp.gate_proj.input_scale": torch.tensor(1.0),
        "scaled_fp8": torch.zeros(1),
        "comfy_quant": torch.zeros(1, dtype=torch.uint8),
    }
    out = _dequantize_fp8_state_dict(sd)
    assert set(out) == {"model.layers.0.mlp.gate_proj.weight"}


def test_dequantize_fp8_state_dict_raises_on_orphan_fp8_tensor():
    q, _scale = _quantize_tensor_fp8(torch.randn(2, 2))
    with pytest.raises(ValueError, match="no matching weight_scale/scale_weight sibling"):
        _dequantize_fp8_state_dict({"model.layers.0.self_attn.q_proj.weight": q})


@pytest.fixture(scope="module")
def tiny_qwen3_fp8_single_file(tmp_path_factory):
    """The SAME tiny Qwen3 architecture as ``tiny_qwen3_single_file``, but its
    Linear weights are fp8-quantized with a per-tensor weight_scale — a
    synthetic stand-in for a real fp8-scaled TE repack, built without any
    network access."""
    from transformers import AutoTokenizer, Qwen3Config, Qwen3ForCausalLM
    from src.features.llm.native_te_adoption import _CHAT_TOKENIZER_DIRS

    tok = AutoTokenizer.from_pretrained(str(_CHAT_TOKENIZER_DIRS["qwen3"]), local_files_only=True)
    config = Qwen3Config(
        vocab_size=len(tok), hidden_size=32, intermediate_size=64,
        num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2,
        head_dim=8, max_position_embeddings=64, tie_word_embeddings=True,
    )
    model = Qwen3ForCausalLM(config)
    model.eval()
    sd = {k: v.clone().contiguous() for k, v in model.state_dict().items()}

    quantized: dict = {}
    for key, tensor in sd.items():
        if key == "lm_head.weight":
            continue  # tied checkpoint: omit, exactly like a real tied fp8 repack
        if key.endswith(".weight") and tensor.ndim == 2:
            q, scale = _quantize_tensor_fp8(tensor)
            quantized[key] = q
            quantized[f"{key[:-len('.weight')]}.weight_scale"] = scale
        else:
            quantized[key] = tensor
    quantized["scaled_fp8"] = torch.zeros(1, dtype=torch.float32)

    base = tmp_path_factory.mktemp("qwen3_fp8_te_models")
    (base / "clip").mkdir()
    path = base / "clip" / "qwen3-tiny-fp8-te.safetensors"
    save_file(quantized, str(path), metadata={"format": "pt"})
    return str(path)


def test_build_adopted_te_dequantizes_and_loads_fp8_qwen3(tiny_qwen3_fp8_single_file):
    model, tokenizer, model_type = build_adopted_te(tiny_qwen3_fp8_single_file)
    assert model_type == "qwen3"
    prompt = tokenizer.apply_chat_template(
        [{"role": "user", "content": "hi"}], add_generation_prompt=True, tokenize=False
    )
    inputs = tokenizer(prompt, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=2, do_sample=False)
    assert out.shape[-1] >= inputs["input_ids"].shape[-1]
    # Loaded weights are real floats (dequantized), not still fp8.
    assert model.model.layers[0].self_attn.q_proj.weight.dtype != torch.float8_e4m3fn


def test_list_adopted_te_checkpoints_flags_fp8_qwen3_adoptable(tiny_qwen3_fp8_single_file):
    from pathlib import Path

    models_dir = Path(tiny_qwen3_fp8_single_file).parent.parent
    entries = list_adopted_te_checkpoints(models_dir)
    matches = [e for e in entries if e.path == tiny_qwen3_fp8_single_file]
    assert len(matches) == 1
    assert matches[0].fp8 is True
    assert matches[0].adoptable is True


# --- nvfp4 dequant: mixed bf16/nvfp4 repacks -----------------
#
# A live repro used a real mixed bf16/nvfp4 Gemma-3 12B repack
# (`Gemma3-12B-NVFP4-Sikaworld-HF.safetensors`, per-tensor `comfy_quant` blob
# `{"format": "nvfp4", "group_size": 16, ...}`, layers 4-41 nvfp4-quantized,
# layers 0-3/42-47 left bf16). Its nvfp4 weights are `uint8`, packed two
# 4-bit e2m1 codes per byte (half the true in_features width), with a
# per-16-element block scale ALSO stored fp8 (F8_E4M3) plus a per-tensor
# `weight_scale_2` global scale (f32 scalar) -- the double-scale nvfp4
# format, distinct from a plain fp8-scaled weight. Before this fix,
# `_family_from_keys`'s block-scale dtype made the whole file look
# fp8-scaled (`F8_E4M3` in `_FP8_DTYPES`), routing it through
# `_dequantize_fp8_state_dict`, whose `weight.to(bf16) * weight_scale`
# formula multiplies the packed `[out, in // 2]` weight against the
# `[out, in // 16]` block scale -- mismatched on the last dim by exactly 8x
# (16 // 2), raising `generate_with_history`'s live traceback ("The size of
# tensor a (7680) must match the size of tensor b (960) at non-singleton
# dimension 1" for the real file's `down_proj` layers: intermediate_size
# 15360 // 2 = 7680 packed columns vs. 15360 // 16 = 960 block-scale
# columns) deep inside the lazily-triggered adoption load, not inside any
# actual attention/rope code.

def _quantize_tensor_nvfp4(weight: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Produce the on-disk nvfp4 triple (packed uint8 `[out, in//2]`, swizzled
    block-scale `weight_scale`, per-tensor global `weight_scale_2` scalar) for
    a weight. The vendored dynamic quantizer emits the SAME on-disk
    layout/dtypes a real checkpoint's nvfp4 weight uses (see its own
    docstring), so it doubles as a synthetic-fixture builder — the exact
    inverse of `dequantize_nvfp4`."""
    from vendor.gpl.comfyui.ops import _quantize_nvfp4_dynamic

    return _quantize_nvfp4_dynamic(weight.to(torch.float32))


def test_detect_quantization_flags_nvfp4_not_fp8_despite_f8_scale_dtype():
    """The root cause: an nvfp4 repack's per-block scale tensors are
    themselves F8_E4M3 -- dtype-sniffing alone misclassifies the whole file
    as legacy fp8-scaled. The `weight_scale_2` marker (unique to the nvfp4
    double-scale format) must be checked first and win."""
    keys = {
        "model.layers.0.self_attn.q_proj.weight",
        "model.layers.0.self_attn.q_proj.weight_scale",
        "model.layers.0.self_attn.q_proj.weight_scale_2",
        "model.layers.0.self_attn.q_proj.comfy_quant",
    }
    dtypes = {"U8", "F8_E4M3", "F32"}
    fp8, nvfp4 = _detect_quantization(keys, dtypes)
    assert fp8 is False
    assert nvfp4 is True


def test_detect_quantization_still_flags_plain_fp8_without_nvfp4_marker():
    keys = {"model.layers.0.self_attn.q_proj.weight", "model.layers.0.self_attn.q_proj.weight_scale"}
    dtypes = {"F8_E4M3", "BF16"}
    fp8, nvfp4 = _detect_quantization(keys, dtypes)
    assert fp8 is True
    assert nvfp4 is False


def test_dequantize_nvfp4_state_dict_matches_reference_dequant():
    weight = torch.randn(8, 64, dtype=torch.float32)
    packed, block_scale, tensor_scale = _quantize_tensor_nvfp4(weight)
    sd = {
        "model.layers.5.mlp.down_proj.weight": packed,
        "model.layers.5.mlp.down_proj.weight_scale": block_scale,
        "model.layers.5.mlp.down_proj.weight_scale_2": tensor_scale,
        "model.layers.5.mlp.down_proj.comfy_quant": torch.zeros(1, dtype=torch.uint8),
        "model.norm.weight": torch.ones(4, dtype=torch.bfloat16),  # untouched, non-quantized
    }
    out = _dequantize_nvfp4_state_dict(sd)

    assert set(out) == {"model.layers.5.mlp.down_proj.weight", "model.norm.weight"}
    assert out["model.layers.5.mlp.down_proj.weight"].shape == (8, 64)
    assert out["model.layers.5.mlp.down_proj.weight"].dtype == torch.bfloat16

    from vendor.gpl.comfyui.ops import dequantize_nvfp4

    expected = dequantize_nvfp4(packed, block_scale, tensor_scale.reshape(()), 8, 64).to(torch.bfloat16)
    assert torch.equal(out["model.layers.5.mlp.down_proj.weight"], expected)


def test_dequantize_fp8_state_dict_on_nvfp4_tensor_reproduces_cmb42_broadcast_bug():
    """FAILS before the fix / documents the exact historical crash: routing an
    nvfp4-packed tensor through the fp8-scaled dequant path raises the same
    class of broadcasting error as the observed live traceback (packed
    in_features // 2 vs. block-scale in_features // 16 -- an 8x mismatch on
    the last dim). This pins the failure to `_dequantize_fp8_state_dict` so a
    future change can't silently route an nvfp4 file through it again."""
    weight = torch.randn(8, 128, dtype=torch.float32)  # packed cols=64, block-scale cols=8 (128/16)
    packed, block_scale, _tensor_scale = _quantize_tensor_nvfp4(weight)
    sd = {
        "model.layers.5.mlp.down_proj.weight": packed,
        "model.layers.5.mlp.down_proj.weight_scale": block_scale,
    }
    with pytest.raises(RuntimeError, match="must match the size of tensor b"):
        _dequantize_fp8_state_dict(sd)


@pytest.fixture(scope="module")
def tiny_gemma3_nvfp4_single_file(tmp_path_factory, tiny_gemma3_chat_tokenizer_files):
    """A tiny, real, mixed bf16/nvfp4 Gemma3ForCausalLM: layer 0 (which config
    reconstruction reads shapes from — see `_gemma3_config_from_state_dict`)
    stays bf16, exactly like the real Sikaworld-style repack keeps its first
    layers unquantized; layer 1's attention/MLP Linear weights are
    nvfp4-packed, matching the real file's per-tensor `comfy_quant` layout.
    Tied (no `lm_head.weight`), matching a real gemma3 repack (see the module
    docstring: gemma3 is always tied)."""
    from transformers import Gemma3TextConfig, Gemma3ForCausalLM

    tok_dir, vocab = tiny_gemma3_chat_tokenizer_files
    config = Gemma3TextConfig(
        vocab_size=vocab, hidden_size=32, intermediate_size=64, num_hidden_layers=2,
        num_attention_heads=1, num_key_value_heads=1, head_dim=256, query_pre_attn_scalar=256,
        rms_norm_eps=1e-6, sliding_window=1024, tie_word_embeddings=True,
        max_position_embeddings=64,
    )
    model = Gemma3ForCausalLM(config)
    model.eval()
    sd = {
        k: v.clone().contiguous() for k, v in model.state_dict().items() if k != "lm_head.weight"
    }

    nvfp4_prefixes = {
        "model.layers.1.self_attn.q_proj", "model.layers.1.self_attn.k_proj",
        "model.layers.1.self_attn.v_proj", "model.layers.1.self_attn.o_proj",
        "model.layers.1.mlp.gate_proj", "model.layers.1.mlp.up_proj", "model.layers.1.mlp.down_proj",
    }
    quantized: dict = {}
    for key, tensor in sd.items():
        prefix = key[: -len(".weight")] if key.endswith(".weight") else None
        if prefix in nvfp4_prefixes:
            packed, block_scale, tensor_scale = _quantize_tensor_nvfp4(tensor)
            quantized[key] = packed
            quantized[f"{prefix}.weight_scale"] = block_scale
            quantized[f"{prefix}.weight_scale_2"] = tensor_scale
            quantized[f"{prefix}.comfy_quant"] = torch.zeros(1, dtype=torch.uint8)
        else:
            quantized[key] = tensor

    base = tmp_path_factory.mktemp("gemma3_nvfp4_te_models")
    (base / "clip").mkdir()
    path = base / "clip" / "gemma3-tiny-nvfp4-te.safetensors"
    save_file(quantized, str(path), metadata={"format": "pt"})

    chat_dir = base / "clip" / "_chat_tokenizer" / "gemma3"
    chat_dir.mkdir(parents=True)
    for name in ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja"):
        src = tok_dir / name
        if src.is_file():
            (chat_dir / name).write_bytes(src.read_bytes())
    return str(path)


def test_build_adopted_te_dequantizes_and_loads_mixed_nvfp4_gemma3(tiny_gemma3_nvfp4_single_file):
    """Acceptance test: before the fix this file's nvfp4 layers were
    misdetected as fp8-scaled and crashed inside `_dequantize_fp8_state_dict`
    before `generate()` was ever reached. Loading and generating must now
    succeed end to end."""
    model, tokenizer, model_type = build_adopted_te(tiny_gemma3_nvfp4_single_file)
    assert model_type == "gemma3"
    prompt = tokenizer.apply_chat_template(
        [{"role": "user", "content": "hi"}], add_generation_prompt=True, tokenize=False
    )
    inputs = tokenizer(prompt, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=2, do_sample=False)
    assert out.shape[-1] >= inputs["input_ids"].shape[-1]
    # The nvfp4 layer's weight is a real float now (dequantized), not still
    # packed uint8 — same style of check the fp8 dequant test above uses,
    # since `AutoModelForCausalLM.from_config` builds fp32 parameters by
    # default and `load_state_dict` casts the bf16 dequant result to match.
    q_proj = model.model.layers[1].self_attn.q_proj.weight
    assert q_proj.dtype not in (torch.uint8, torch.float8_e4m3fn)
    assert q_proj.shape == (256, 32)


def test_list_adopted_te_checkpoints_flags_nvfp4_gemma3(tiny_gemma3_nvfp4_single_file):
    from pathlib import Path

    models_dir = Path(tiny_gemma3_nvfp4_single_file).parent.parent
    entries = list_adopted_te_checkpoints(models_dir)
    matches = [e for e in entries if e.path == tiny_gemma3_nvfp4_single_file]
    assert len(matches) == 1
    assert matches[0].nvfp4 is True
    assert matches[0].fp8 is False
    assert matches[0].adoptable is True


# --- merged into the native checkpoints listing ------------------

def test_native_checkpoints_listing_includes_adopted_te_flagged(tmp_path):
    from src.features.llm.native_library import list_native_checkpoints

    _write_synthetic(tmp_path / "clip" / "qwen3-te.safetensors", _QWEN3_MIN_KEYS,
                     extra={"lm_head.weight": torch.zeros(64, 32, dtype=torch.bfloat16)})
    entries = list_native_checkpoints(tmp_path)
    te = [e for e in entries if e.shared_te]
    assert len(te) == 1
    assert te[0].name == "clip/qwen3-te.safetensors"
    assert te[0].supported is True
    # An adoptable single-file checkpoint can also be bnb-quantized in
    # place (NativeLLMClient._quantize_adopted_module), same modes as an
    # HF-directory checkpoint.
    from src.features.llm.native_library import NATIVE_LLM_QUANT_MODES

    assert te[0].quant_modes == list(NATIVE_LLM_QUANT_MODES)
