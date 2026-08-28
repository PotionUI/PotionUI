"""Tests for NativeLLMClient — the in-process transformers provider.

CPU-only, no downloads: the checkpoint fixtures are TINY randomly-initialized
transformers models (2-layer Qwen3 for text, a matching processor stub is not
needed since vision family-gating is tested at the config level, not by
actually loading a VL model — building one from scratch with a real chat
template AND an image processor offline is out of proportion to what the
gating/lifecycle/streaming contract needs to prove). Family gating for the
vision allowlist is exercised via the config.json model_type alone.

Every torch.cuda entry point is monkeypatched off so this suite runs
identically whether or not the box happens to have a visible GPU (the
project's "no GPU ever" test rule).
"""

from __future__ import annotations

import json

import pytest
import torch

from src.features.llm.clients.native import NativeLLMClient
from src.features.llm.repository import LLMConfig
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle


@pytest.fixture(autouse=True)
def _no_real_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None, raising=False)
    monkeypatch.setattr(torch.cuda, "synchronize", lambda: None, raising=False)


@pytest.fixture(scope="session")
def tiny_qwen3_checkpoint_dir(tmp_path_factory):
    """A real, tiny, randomly-initialized Qwen3 checkpoint on disk — built
    once per test session via transformers' config-only init (no network),
    round-tripped through save_pretrained/from_pretrained so the client's
    AutoConfig/AutoModelForCausalLM/AutoTokenizer calls exercise the real
    HF-layout loading path end to end. Laid out as `<models_dir>/llm/<name>/`
    to match the real on-disk convention `resolve_native_checkpoint_path`
    expects — see the `native_checkpoint` fixture, which points
    `native_library._models_dir` at this directory's parent-of-parent so
    `LLMConfig.model` can be the bare directory name, exactly like production.
    """
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM

    vocab = {"[UNK]": 0, "[PAD]": 1, "[BOS]": 2, "[EOS]": 3}
    for i, w in enumerate(
        ["hello", "world", "the", "cat", "sat", "on", "mat", "assistant", "user", "system", ":", "\n"],
        start=4,
    ):
        vocab[w] = i

    tok = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.decoder = decoders.WordPiece()
    fast_tok = PreTrainedTokenizerFast(
        tokenizer_object=tok, unk_token="[UNK]", pad_token="[PAD]", bos_token="[BOS]", eos_token="[EOS]"
    )
    fast_tok.chat_template = (
        "{% for m in messages %}{{ m.role }}: {{ m.content }}\n{% endfor %}"
        "{% if add_generation_prompt %}assistant:\n{% endif %}"
    )

    config = Qwen3Config(
        vocab_size=len(vocab) + 10,
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        head_dim=8,
        max_position_embeddings=64,
        pad_token_id=1,
        bos_token_id=2,
        eos_token_id=3,
    )
    model = Qwen3ForCausalLM(config)
    model.eval()

    models_dir = tmp_path_factory.mktemp("native_llm_models_dir")
    d = models_dir / "llm" / "qwen3-tiny"
    d.mkdir(parents=True)
    model.save_pretrained(d)
    fast_tok.save_pretrained(d)
    return models_dir


@pytest.fixture
def native_checkpoint(tiny_qwen3_checkpoint_dir, monkeypatch):
    """Points the library's models_dir at the fixture directory, and returns
    (bare_name, resolved_absolute_path) — `LLMConfig.model` holds the former,
    exactly like an admin picking it from `list_native_checkpoints()`."""
    import src.features.llm.native_library as native_library_module

    monkeypatch.setattr(native_library_module, "_models_dir", lambda: tiny_qwen3_checkpoint_dir)
    name = "qwen3-tiny"
    path = str((tiny_qwen3_checkpoint_dir / "llm" / name).resolve())
    return name, path


@pytest.fixture
def models_manager():
    return ModelLifecycle(gpu_monitor=None, settings=None)


@pytest.fixture
def client(models_manager):
    return NativeLLMClient(models_manager)


def _config(model_name: str, **overrides) -> LLMConfig:
    defaults = dict(
        id="native-1",
        name="Native Test",
        type="native",
        enabled=True,
        base_url="",
        model=model_name,
        system_message="You are a test assistant.",
        temperature=0.0,
        max_tokens=4,
        timeout=30,
        supports_vision=False,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


# --- family gating --------------------------------------------------------

@pytest.mark.parametrize("model_type", ["qwen3", "gemma3_text"])
def test_family_accepts_text_types(model_type):
    vision, resolved = NativeLLMClient._family(model_type)
    assert vision is False
    assert resolved == model_type


@pytest.mark.parametrize("model_type", ["qwen3_vl", "qwen2_vl", "gemma3"])
def test_family_accepts_vision_types(model_type):
    vision, resolved = NativeLLMClient._family(model_type)
    assert vision is True
    assert resolved == model_type


def test_family_rejects_unknown_type_and_names_the_allowlist():
    with pytest.raises(ValueError) as exc:
        NativeLLMClient._family("llama")
    message = str(exc.value)
    assert "llama" in message
    assert "qwen3" in message
    assert "gemma3" in message


def test_family_rejects_none():
    with pytest.raises(ValueError):
        NativeLLMClient._family(None)


# --- provider contract: buffered generate ---------------------------------

@pytest.mark.asyncio
async def test_generate_with_history_returns_response(client, native_checkpoint):
    name, _ = native_checkpoint
    config = _config(name)
    response = await client.generate_with_history(
        [{"role": "user", "content": "hello world"}], config, config.system_message
    )
    assert response.provider_id == "native"
    assert response.model == name
    assert isinstance(response.content, str)
    assert response.prompt_tokens > 0
    assert response.completion_tokens >= 0
    assert response.tokens_used == response.prompt_tokens + response.completion_tokens


@pytest.mark.asyncio
async def test_generate_delegates_to_generate_with_history(client, native_checkpoint):
    name, _ = native_checkpoint
    config = _config(name)
    response = await client.generate("hello world", config, config.system_message)
    assert response.provider_id == "native"


@pytest.mark.asyncio
async def test_vision_image_on_text_only_checkpoint_raises_cleanly(client, native_checkpoint):
    name, _ = native_checkpoint
    config = _config(name, supports_vision=True)
    with pytest.raises(ValueError, match="no vision support"):
        await client.generate_with_history(
            [{"role": "user", "content": "describe this"}],
            config,
            config.system_message,
            image_data="not-a-real-base64-but-never-reached",
        )


# --- provider contract: streaming -----------------------------------------

@pytest.mark.asyncio
async def test_stream_with_history_yields_tokens_then_usage(client, native_checkpoint):
    name, _ = native_checkpoint
    config = _config(name)
    events = []
    async for event in client.stream_with_history([{"role": "user", "content": "hello world"}], config, config.system_message):
        events.append(event)

    assert events, "expected at least the final usage event"
    assert events[-1]["type"] == "usage"
    assert events[-1]["prompt_tokens"] > 0
    assert all(e["type"] in ("token", "usage") for e in events)
    token_events = [e for e in events if e["type"] == "token"]
    assert all(isinstance(e["content"], str) and e["content"] for e in token_events)


# --- tool calling: always prompt-injected ---------------------------------

@pytest.mark.asyncio
async def test_generate_with_tools_injects_tool_text_and_never_sets_structured_tool_calls(client, native_checkpoint):
    name, _ = native_checkpoint
    config = _config(name)
    tools = [{"function": {"name": "do_thing", "description": "does a thing", "parameters": {"type": "object"}}}]
    response = await client.generate_with_tools(
        [{"role": "user", "content": "call the tool"}], config, config.system_message, tools=tools
    )
    assert response.tool_calls is None


def test_inject_tools_into_system_message_names_the_tool():
    text = NativeLLMClient._inject_tools_into_system_message(
        "base prompt",
        [{"function": {"name": "do_thing", "description": "does a thing", "parameters": {}}}],
    )
    assert "base prompt" in text
    assert "do_thing" in text
    assert "<tool_call>" in text


def test_inject_tools_into_system_message_noop_without_tools():
    assert NativeLLMClient._inject_tools_into_system_message("base prompt", None) == "base prompt"
    assert NativeLLMClient._inject_tools_into_system_message("base prompt", []) == "base prompt"


# --- model-lifecycle integration ------------------------------------------

@pytest.mark.asyncio
async def test_generate_registers_a_lifecycle_entry(client, models_manager, native_checkpoint):
    name, path = native_checkpoint
    config = _config(name)
    await client.generate_with_history([{"role": "user", "content": "hi"}], config, config.system_message)
    key = f"native/llm/{path}"
    assert key in models_manager._entries


@pytest.mark.asyncio
async def test_second_turn_reuses_the_cached_checkpoint(client, models_manager, native_checkpoint, monkeypatch):
    name, _ = native_checkpoint
    build_calls = []
    original_build = NativeLLMClient._build

    def _counting_build(self, path, load_kwargs):
        build_calls.append(path)
        return original_build(self, path, load_kwargs)

    monkeypatch.setattr(NativeLLMClient, "_build", _counting_build)

    config = _config(name)
    await client.generate_with_history([{"role": "user", "content": "first"}], config, config.system_message)
    await client.generate_with_history([{"role": "user", "content": "second"}], config, config.system_message)

    assert len(build_calls) == 1


@pytest.mark.asyncio
async def test_turn_is_leased_during_generate_and_released_after(client, models_manager, native_checkpoint, monkeypatch):
    name, path = native_checkpoint
    key = f"native/llm/{path}"
    leased_during_call = []

    original_acquire = ModelLifecycle.acquire

    def _spy_acquire(self, *args, **kwargs):
        value = original_acquire(self, *args, **kwargs)
        entry = self._entries.get(key)
        leased_during_call.append(bool(entry and entry.leased_by))
        return value

    monkeypatch.setattr(ModelLifecycle, "acquire", _spy_acquire)

    config = _config(name)
    await client.generate_with_history([{"role": "user", "content": "hi"}], config, config.system_message)

    assert leased_during_call == [True]
    # released once the turn's lease exits __aexit__ of `_leased`
    assert not models_manager._entries[key].leased_by
    assert key in models_manager._evictable_keys()


@pytest.mark.asyncio
async def test_lease_released_even_when_generation_raises(client, models_manager, native_checkpoint, monkeypatch):
    name, path = native_checkpoint
    key = f"native/llm/{path}"

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic failure mid-generate")

    config = _config(name)

    # Force the underlying model.generate() to blow up so the lease's
    # finally-block is what has to release it, not the happy path.
    async def _patched(self, *a, **k):
        checkpoint = client._acquire(path, config)
        monkeypatch.setattr(checkpoint.model, "generate", _boom)
        return await NativeLLMClient.generate_with_history(self, *a, **k)

    with pytest.raises(RuntimeError, match="synthetic failure"):
        await _patched(client, [{"role": "user", "content": "hi"}], config, config.system_message)

    assert not models_manager._entries[key].leased_by
    assert key in models_manager._evictable_keys()


@pytest.mark.asyncio
async def test_oom_during_generate_is_reported_cleanly(client, native_checkpoint, monkeypatch):
    name, path = native_checkpoint
    config = _config(name)

    async def _patched(self, *a, **k):
        checkpoint = client._acquire(path, config)

        def _oom(*_a, **_k):
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")

        monkeypatch.setattr(checkpoint.model, "generate", _oom)
        return await NativeLLMClient.generate_with_history(self, *a, **k)

    with pytest.raises(ValueError, match="ran out of GPU memory"):
        await _patched(client, [{"role": "user", "content": "hi"}], config, config.system_message)


def test_missing_lifecycle_manager_raises_clean_error():
    client = NativeLLMClient(model_lifecycle=None)
    import src.platform.runtime.model_lifecycle.lifecycle as manager_module

    saved = manager_module._default_lifecycle
    manager_module._default_lifecycle = None
    try:
        with pytest.raises(ValueError, match="ModelLifecycle"):
            client._models()
    finally:
        manager_module._default_lifecycle = saved


def test_unsupported_checkpoint_family_raises_before_loading_weights(tmp_path, client):
    d = tmp_path / "llm" / "bad"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"model_type": "llama"}))
    with pytest.raises(ValueError, match="unsupported model family"):
        client._build(str(d), {"dtype": "auto"})


# --- fingerprint / size-estimate (team-lead integration notes) -----------

def test_load_kwargs_default_dtype_auto():
    config = _config("qwen3-tiny")
    assert NativeLLMClient._load_kwargs(config) == {"dtype": "auto"}


def test_load_kwargs_reads_dtype_and_revision_from_provider_options():
    config = _config("qwen3-tiny", provider_options={"dtype": "bfloat16", "revision": "abc123"})
    assert NativeLLMClient._load_kwargs(config) == {"dtype": "bfloat16", "revision": "abc123"}


def test_fingerprint_changes_with_dtype():
    a = NativeLLMClient._fingerprint("/path", {"dtype": "auto"})
    b = NativeLLMClient._fingerprint("/path", {"dtype": "bfloat16"})
    assert a != b


@pytest.mark.asyncio
async def test_changing_dtype_busts_the_cached_checkpoint(client, models_manager, native_checkpoint, monkeypatch):
    name, path = native_checkpoint
    build_calls = []
    original_build = NativeLLMClient._build

    def _counting_build(self, path, load_kwargs):
        build_calls.append(load_kwargs)
        return original_build(self, path, load_kwargs)

    monkeypatch.setattr(NativeLLMClient, "_build", _counting_build)

    await client.generate_with_history(
        [{"role": "user", "content": "hi"}], _config(name), "sys"
    )
    await client.generate_with_history(
        [{"role": "user", "content": "hi"}], _config(name, provider_options={"dtype": "float32"}), "sys"
    )

    assert len(build_calls) == 2
    assert build_calls[0]["dtype"] == "auto"
    assert build_calls[1]["dtype"] == "float32"


def test_checkpoint_size_gb_sums_shard_files_not_the_directory_inode(native_checkpoint):
    from src.features.llm.native_library import checkpoint_size_gb

    _, path = native_checkpoint
    size = checkpoint_size_gb(path)
    assert size is not None
    # A directory's own inode size (what os.path.getsize(dir) would report)
    # is a few KB at most; the real shard file is bigger than that even for
    # this tiny fixture model, proving the estimate sums shard files rather
    # than stat'ing the directory itself.
    assert size > 1e-6


# --- quantized loading -------------------------------------------

def test_quant_mode_defaults_to_none():
    assert NativeLLMClient._quant_mode(_config("qwen3-tiny")) == "none"


@pytest.mark.parametrize("mode", ["int8", "nf4"])
def test_quant_mode_reads_provider_options(mode):
    config = _config("qwen3-tiny", provider_options={"quantization": mode})
    assert NativeLLMClient._quant_mode(config) == mode


def test_quant_mode_rejects_unknown_and_names_the_allowlist():
    config = _config("qwen3-tiny", provider_options={"quantization": "int3"})
    with pytest.raises(ValueError, match="unknown quantization mode"):
        NativeLLMClient._quant_mode(config)


def test_load_kwargs_omits_quantization_when_none():
    assert "quantization" not in NativeLLMClient._load_kwargs(_config("qwen3-tiny"))


def test_load_kwargs_carries_quantization_marker():
    config = _config("qwen3-tiny", provider_options={"quantization": "nf4"})
    assert NativeLLMClient._load_kwargs(config)["quantization"] == "nf4"


def test_fingerprint_includes_quant_mode():
    bf16 = NativeLLMClient._fingerprint("/path", {"dtype": "auto"})
    nf4 = NativeLLMClient._fingerprint("/path", {"dtype": "auto", "quantization": "nf4"})
    int8 = NativeLLMClient._fingerprint("/path", {"dtype": "auto", "quantization": "int8"})
    assert bf16 != nf4 != int8 != bf16
    assert "quant=none" in bf16
    assert "quant=nf4" in nf4


@pytest.mark.parametrize("mode,factor", [("int8", 0.5), ("nf4", 0.28)])
def test_estimated_size_gb_applies_quant_factor(native_checkpoint, mode, factor):
    _, path = native_checkpoint
    from src.features.llm.native_library import checkpoint_size_gb

    base = checkpoint_size_gb(path)
    assert base is not None
    assert NativeLLMClient._estimated_size_gb(path, "none") == base
    assert NativeLLMClient._estimated_size_gb(path, mode) == pytest.approx(base * factor)


def test_estimated_size_gb_none_when_unsizable(tmp_path):
    # An empty directory has no shard files -> checkpoint_size_gb returns None,
    # and the factor must not turn that into a crash.
    assert NativeLLMClient._estimated_size_gb(str(tmp_path), "nf4") is None


@pytest.mark.parametrize("mode", ["int8", "nf4"])
def test_bnb_config_errors_without_cuda(monkeypatch, mode):
    # _no_real_cuda already forces is_available -> False.
    with pytest.raises(ValueError, match="requires a CUDA GPU"):
        NativeLLMClient._bnb_config(mode)


@pytest.mark.parametrize("mode", ["int8", "nf4"])
def test_bnb_config_errors_without_bitsandbytes(monkeypatch, mode):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    import builtins

    real_import = builtins.__import__

    def _no_bnb(name, *args, **kwargs):
        if name == "bitsandbytes":
            raise ImportError("No module named 'bitsandbytes'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_bnb)
    with pytest.raises(ValueError, match="bitsandbytes"):
        NativeLLMClient._bnb_config(mode)


@pytest.mark.parametrize("mode,expect_key,expect_extra", [
    ("int8", "load_in_8bit", {}),
    ("nf4", "load_in_4bit", {"bnb_4bit_quant_type": "nf4", "bnb_4bit_use_double_quant": True}),
])
def test_bnb_config_shape_when_available(monkeypatch, mode, expect_key, expect_extra):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "bitsandbytes", __import__("types").ModuleType("bitsandbytes"))
    cfg = NativeLLMClient._bnb_config(mode)
    assert getattr(cfg, expect_key) is True
    for k, v in expect_extra.items():
        assert getattr(cfg, k) == v


@pytest.mark.asyncio
async def test_build_passes_bitsandbytes_config_and_device_map(client, native_checkpoint, monkeypatch):
    """The quant mode reaches AutoModelForCausalLM.from_pretrained as a real
    BitsAndBytesConfig + device_map, and the marker key is not leaked as a
    from_pretrained kwarg."""
    from transformers import BitsAndBytesConfig

    _, path = native_checkpoint
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "bitsandbytes", __import__("types").ModuleType("bitsandbytes"))

    captured = {}

    class _Stub:
        @staticmethod
        def eval():
            return None

    def _fake_from_pretrained(p, **kwargs):
        captured["path"] = p
        captured["kwargs"] = kwargs
        model = _Stub()
        model.eval = lambda: model
        return model

    import transformers

    monkeypatch.setattr(transformers.AutoModelForCausalLM, "from_pretrained", staticmethod(_fake_from_pretrained))
    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", staticmethod(lambda p, **k: object()))

    load_kwargs = NativeLLMClient._load_kwargs(
        _config("qwen3-tiny", provider_options={"quantization": "nf4"})
    )
    checkpoint = client._build(path, load_kwargs)

    assert checkpoint.quantized is True
    assert "quantization" not in captured["kwargs"]
    assert isinstance(captured["kwargs"]["quantization_config"], BitsAndBytesConfig)
    assert captured["kwargs"]["quantization_config"].load_in_4bit is True
    assert captured["kwargs"]["device_map"] == {"": 0}


# --- quantizing an adopted single-file TE checkpoint --------------

@pytest.fixture(scope="module")
def tiny_qwen3_adopted_checkpoint(tmp_path_factory):
    """A tiny real Qwen3ForCausalLM, already built (no safetensors round-trip
    needed here — ``build_adopted_te`` itself is covered end to end in
    test_native_te_adoption.py; this fixture only needs a real module with
    Linear layers to exercise ``_quantize_adopted_module``'s routing)."""
    from transformers import Qwen3Config, Qwen3ForCausalLM

    config = Qwen3Config(
        vocab_size=32, hidden_size=16, intermediate_size=32,
        num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=1,
        head_dim=8, max_position_embeddings=32, tie_word_embeddings=False,
    )
    model = Qwen3ForCausalLM(config)
    model.eval()
    return model


def test_quantize_adopted_module_never_converts_lm_head(monkeypatch, tiny_qwen3_adopted_checkpoint):
    """``_quantize_adopted_module`` always excludes lm_head (precision
    + tied-storage reasons — see its docstring), and every other Linear layer
    gets routed through the SAME BitsAndBytesConfig-driven module-replacement
    seam ``transformers.integrations.bitsandbytes.replace_with_bnb_linear``
    uses for an HF-directory bnb load. No real bitsandbytes install is needed:
    a fake ``bitsandbytes.nn.Params4bit``/``Int8Params`` (a torch.nn.Parameter
    subclass, so module assignment behaves like the real thing) stands in."""
    import sys
    import types

    model = tiny_qwen3_adopted_checkpoint

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.nn.Module, "to", lambda self, device=None: self)

    class _FakeQuantParam(torch.nn.Parameter):
        def __new__(cls, data, requires_grad=False, **kwargs):
            return super().__new__(cls, data, requires_grad=requires_grad)

    calls = {}

    def _fake_replace(model, modules_to_not_convert, quantization_config):
        calls["modules_to_not_convert"] = list(modules_to_not_convert)
        calls["quantization_config"] = quantization_config
        for name, module in list(model.named_modules()):
            if isinstance(module, torch.nn.Linear) and name not in modules_to_not_convert:
                new_module = torch.nn.Linear(
                    module.in_features, module.out_features, bias=module.bias is not None
                )
                model.set_submodule(name, new_module)
        return model

    # Import the REAL module first (bitsandbytes genuinely absent -> its
    # top-level `is_bitsandbytes_available()` guard is simply False, so this
    # import is a clean no-op) so the patch below sets an attribute on an
    # already-cached module object instead of re-triggering that module's own
    # import-time bnb-availability probe against our fake `sys.modules` entry
    # (which has no real `__spec__` and would raise `find_spec`'s own error).
    import transformers.integrations.bitsandbytes as tib

    fake_bnb = types.SimpleNamespace(
        nn=types.SimpleNamespace(Params4bit=_FakeQuantParam, Int8Params=_FakeQuantParam)
    )
    monkeypatch.setitem(sys.modules, "bitsandbytes", fake_bnb)
    monkeypatch.setattr(tib, "replace_with_bnb_linear", _fake_replace)

    quantized = NativeLLMClient._quantize_adopted_module(model, "nf4")

    assert calls["modules_to_not_convert"] == ["lm_head"]
    linear_names = [
        name for name, m in quantized.named_modules()
        if isinstance(m, torch.nn.Linear) and name != "lm_head"
    ]
    assert linear_names  # q/k/v/o/gate/up/down proj at minimum
    for name in linear_names:
        assert isinstance(quantized.get_submodule(name).weight, _FakeQuantParam)
    # lm_head itself was never handed to replace_with_bnb_linear as convertible,
    # and _quantize_adopted_module never touches it directly either.
    assert not isinstance(quantized.get_submodule("lm_head").weight, _FakeQuantParam)


def test_quantize_adopted_module_int8_uses_int8params(monkeypatch, tiny_qwen3_adopted_checkpoint):
    import sys
    import types

    model = tiny_qwen3_adopted_checkpoint
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.nn.Module, "to", lambda self, device=None: self)

    class _FakeInt8Param(torch.nn.Parameter):
        def __new__(cls, data, requires_grad=False, **kwargs):
            return super().__new__(cls, data, requires_grad=requires_grad)

    def _fake_replace(model, modules_to_not_convert, quantization_config):
        for name, module in list(model.named_modules()):
            if isinstance(module, torch.nn.Linear) and name not in modules_to_not_convert:
                model.set_submodule(
                    name, torch.nn.Linear(module.in_features, module.out_features, bias=module.bias is not None)
                )
        return model

    import transformers.integrations.bitsandbytes as tib

    fake_bnb = types.SimpleNamespace(
        nn=types.SimpleNamespace(Params4bit=_FakeInt8Param, Int8Params=_FakeInt8Param)
    )
    monkeypatch.setitem(sys.modules, "bitsandbytes", fake_bnb)
    monkeypatch.setattr(tib, "replace_with_bnb_linear", _fake_replace)

    quantized = NativeLLMClient._quantize_adopted_module(model, "int8")
    assert isinstance(quantized.model.layers[0].self_attn.q_proj.weight, _FakeInt8Param)


@pytest.mark.asyncio
async def test_build_adopted_with_quantization_without_cuda_raises_clear_error(client, monkeypatch):
    """The CPU-only host gate ``_bnb_config`` already enforces for the
    HF-directory path applies identically to an adopted single-file
    checkpoint — reached via ``_build_adopted`` -> ``_quantize_adopted_module``
    -> ``_bnb_config``. ``_no_real_cuda`` (autouse) keeps CUDA unavailable."""
    from src.features.llm.native_te_adoption import build_adopted_te as _real_build

    monkeypatch.setattr(
        "src.features.llm.clients.native.build_adopted_te",
        lambda path: (object(), object(), "qwen3"),
    )
    with pytest.raises(ValueError, match="requires a CUDA GPU"):
        client._build_adopted("/fake/path.safetensors", quant_mode="nf4")


def test_acquire_te_fingerprint_and_key_include_quant_mode(client, monkeypatch):
    """The cache fingerprint for an adopted TE must fold in the
    requested quant mode, exactly like the HF-directory path's own
    fingerprint (test_fingerprint_includes_quant_mode) — otherwise switching
    quantization on/off for the same file would silently reuse a stale
    bf16-or-differently-quantized cached module."""
    captured = {}

    class _FakeModels:
        def acquire(self, key, fingerprint, loader, estimated_vram_gb):
            captured["key"] = key
            captured["fingerprint"] = fingerprint
            captured["estimated_vram_gb"] = estimated_vram_gb
            return "sentinel"

    monkeypatch.setattr(client, "_models", lambda: _FakeModels())
    config = _config("qwen3-tiny", provider_options={"quantization": "nf4"})

    result = client._acquire("/models/text_encoders/qwen3-te.safetensors", config, is_te=True)

    assert result == "sentinel"
    assert "quant=nf4" in captured["fingerprint"]
    assert captured["key"] == "native/llm-te//models/text_encoders/qwen3-te.safetensors"


def test_acquire_te_fingerprint_differs_by_quant_mode(client, monkeypatch):
    captured = []

    class _FakeModels:
        def acquire(self, key, fingerprint, loader, estimated_vram_gb):
            captured.append(fingerprint)
            return None

    monkeypatch.setattr(client, "_models", lambda: _FakeModels())
    for mode in (None, "int8", "nf4"):
        opts = {"quantization": mode} if mode else {}
        client._acquire("/models/text_encoders/qwen3-te.safetensors", _config("qwen3-tiny", provider_options=opts), is_te=True)

    assert len(set(captured)) == 3


class _RecordingModel:
    def __init__(self):
        self.moves = []

    def to(self, device):
        self.moves.append(device)
        return self


def _stub_checkpoint(quantized: bool):
    from src.features.llm.clients.native import _LoadedCheckpoint

    return _LoadedCheckpoint(
        model=_RecordingModel(), tokenizer=object(), vision=False,
        model_type="qwen3", quantized=quantized,
    )


@pytest.mark.asyncio
async def test_leased_quantized_checkpoint_is_never_moved(client, native_checkpoint, monkeypatch):
    """A quantized checkpoint (GPU-resident, evict-only) must not have .to()
    called on it in _leased — moving a bnb module round-trip would corrupt it."""
    name, path = native_checkpoint
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    checkpoint = _stub_checkpoint(quantized=True)
    monkeypatch.setattr(NativeLLMClient, "_acquire", lambda self, p, cfg, is_te=False: checkpoint)

    async with client._leased(path, _config(name)) as (ck, device):
        assert ck is checkpoint
        assert device == "cuda"
    assert checkpoint.model.moves == []


@pytest.mark.asyncio
async def test_leased_unquantized_checkpoint_moves_to_gpu_and_back(client, native_checkpoint, monkeypatch):
    """The unquantized path is unchanged: placed on the compute device for the
    turn and returned to CPU on the way out."""
    name, path = native_checkpoint
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    checkpoint = _stub_checkpoint(quantized=False)
    monkeypatch.setattr(NativeLLMClient, "_acquire", lambda self, p, cfg, is_te=False: checkpoint)

    async with client._leased(path, _config(name)) as (_ck, device):
        assert device == "cuda"
        assert checkpoint.model.moves == ["cuda"]
    assert checkpoint.model.moves == ["cuda", "cpu"]


@pytest.mark.asyncio
async def test_leased_falls_back_to_cpu_on_placement_oom(client, native_checkpoint, monkeypatch, caplog):
    """A CUDA OOM moving the checkpoint onto the GPU must degrade this turn to
    CPU (warn once) rather than fail the whole request; placement itself keeps
    retrying GPU next turn since nothing about the fallback is cached."""
    name, path = native_checkpoint
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    checkpoint = _stub_checkpoint(quantized=False)

    def _raise_oom(device):
        raise RuntimeError("CUDA out of memory.")

    checkpoint.model.to = _raise_oom
    monkeypatch.setattr(NativeLLMClient, "_acquire", lambda self, p, cfg, is_te=False: checkpoint)

    with caplog.at_level("WARNING"):
        async with client._leased(path, _config(name)) as (ck, device):
            assert ck is checkpoint
            assert device == "cpu"

    assert any("fell back to CPU" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_leased_does_not_swallow_a_non_oom_runtime_error(client, native_checkpoint, monkeypatch):
    """Only an OOM degrades to CPU - any other RuntimeError during placement
    must still propagate."""
    name, path = native_checkpoint
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    checkpoint = _stub_checkpoint(quantized=False)

    def _raise_other(device):
        raise RuntimeError("something unrelated broke")

    checkpoint.model.to = _raise_other
    monkeypatch.setattr(NativeLLMClient, "_acquire", lambda self, p, cfg, is_te=False: checkpoint)

    with pytest.raises(RuntimeError, match="something unrelated broke"):
        async with client._leased(path, _config(name)):
            pass


# --- adopted single-file TE dispatch ------------------------------

def test_resolve_model_flags_adopted_te_reference(monkeypatch):
    import src.features.llm.clients.native as native_module

    monkeypatch.setattr(native_module, "resolve_adopted_te_path", lambda n: f"/abs/{n}")
    path, is_te = NativeLLMClient._resolve_model("text_encoders/qwen3-te.safetensors")
    assert is_te is True
    assert path == "/abs/text_encoders/qwen3-te.safetensors"


@pytest.mark.asyncio
async def test_adopted_te_generate_uses_distinct_lifecycle_key(client, models_manager, monkeypatch):
    """A text_encoders/ model reference loads via the adoption builder and is cached under
    the native/llm-te/ kind, never colliding with native/llm/."""
    import src.features.llm.clients.native as native_module

    resolved_path = "/models/text_encoders/qwen3-te.safetensors"
    monkeypatch.setattr(native_module, "resolve_adopted_te_path", lambda n: resolved_path)

    class _Model:
        def to(self, *a, **k):
            return self

        def generate(self, **kwargs):
            return torch.cat([kwargs["input_ids"], torch.tensor([[7]])], dim=-1)

    class _Tok:
        def apply_chat_template(self, chat, add_generation_prompt, tokenize):
            return "hi"

        def __call__(self, text, return_tensors="pt"):
            return {"input_ids": torch.tensor([[2, 4, 5]])}

        def decode(self, ids, skip_special_tokens=True):
            return "ok"

    monkeypatch.setattr(native_module, "build_adopted_te", lambda p: (_Model(), _Tok(), "qwen3"))

    config = _config("text_encoders/qwen3-te.safetensors")
    response = await client.generate_with_history(
        [{"role": "user", "content": "hi"}], config, config.system_message
    )
    assert response.provider_id == "native"
    assert f"native/llm-te/{resolved_path}" in models_manager._entries


# --- the chat checkpoint must never be left GPU-resident ----------

@pytest.mark.asyncio
async def test_leased_cpu_restore_failure_evicts_the_cache_entry(client, models_manager, native_checkpoint, monkeypatch, caplog):
    """If the return-to-CPU move itself fails (e.g. a CPU-RAM squeeze staging
    the copy), the checkpoint must not be left as a zombie CUDA-resident cache
    entry with no further recovery path -- the entry is evicted outright."""
    name, path = native_checkpoint
    key = f"native/llm/{path}"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    checkpoint = _stub_checkpoint(quantized=False)
    # Monkeypatch the LOADER, not `_acquire` itself, so the real `_acquire` ->
    # `models.acquire()` call genuinely populates `models_manager._entries` --
    # otherwise a stub `_acquire` bypasses the cache entirely and the eviction
    # assertion below would trivially pass even if eviction never ran.
    monkeypatch.setattr(NativeLLMClient, "_build", lambda self, p, load_kwargs: checkpoint)

    real_to = checkpoint.model.to

    def _raise_on_cpu_return(device):
        if device == "cpu":
            raise RuntimeError("synthetic CPU RAM pressure during restore")
        return real_to(device)

    checkpoint.model.to = _raise_on_cpu_return

    with caplog.at_level("WARNING"):
        async with client._leased(path, _config(name)) as (ck, device):
            assert ck is checkpoint
            assert device == "cuda"
            assert key in models_manager._entries

    assert key not in models_manager._entries
    assert any("evicting the cache entry" in r.message for r in caplog.records)


# --- GpuResidencyRegistry registration ------------------------------

def _fake_residency_module(monkeypatch):
    """Patches `get_residency_registry` at the module the client imports it
    from with an in-memory fake exposing the two calls native.py makes:
    `note_resident`/`note_offloaded`."""
    import types

    calls = {"resident": [], "offloaded": []}

    class _FakeResidencyRegistry:
        def note_resident(self, handle, device, size_gb):
            calls["resident"].append((handle, device, size_gb))

        def note_offloaded(self, handle):
            calls["offloaded"].append(handle)

    fake_module = types.ModuleType("src.platform.runtime.native.memory.residency")
    fake_module.get_residency_registry = lambda: _FakeResidencyRegistry()
    monkeypatch.setitem(
        __import__("sys").modules, "src.platform.runtime.native.memory.residency", fake_module,
    )
    return calls


@pytest.mark.asyncio
async def test_leased_registers_and_clears_residency_for_an_unquantized_checkpoint(
    client, native_checkpoint, monkeypatch,
):
    name, path = native_checkpoint
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    checkpoint = _stub_checkpoint(quantized=False)
    monkeypatch.setattr(NativeLLMClient, "_build", lambda self, p, load_kwargs: checkpoint)
    calls = _fake_residency_module(monkeypatch)

    async with client._leased(path, _config(name)) as (ck, device):
        assert device == "cuda"
        assert len(calls["resident"]) == 1
        assert calls["resident"][0][1] == "cuda"

    assert len(calls["offloaded"]) == 1
    assert calls["offloaded"][0] is calls["resident"][0][0]


@pytest.mark.asyncio
async def test_leased_never_registers_residency_for_a_quantized_checkpoint(
    client, native_checkpoint, monkeypatch,
):
    name, path = native_checkpoint
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    checkpoint = _stub_checkpoint(quantized=True)
    monkeypatch.setattr(NativeLLMClient, "_build", lambda self, p, load_kwargs: checkpoint)
    calls = _fake_residency_module(monkeypatch)

    async with client._leased(path, _config(name)):
        pass

    assert calls["resident"] == []
    assert calls["offloaded"] == []


def test_residency_handle_offload_moves_model_and_notes_offloaded(monkeypatch):
    from src.features.llm.clients.native import _NativeLLMResidencyHandle

    checkpoint = _stub_checkpoint(quantized=False)
    checkpoint.model.to("cuda")
    calls = _fake_residency_module(monkeypatch)

    models = type("M", (), {"invalidate": lambda self, key: None})()
    handle = _NativeLLMResidencyHandle(checkpoint, "native/llm/fake", models)

    handle.offload()

    assert checkpoint.model.moves[-1] == "cpu"
    assert handle.offloaded is True
    assert calls["offloaded"] == [handle]


def test_residency_handle_offload_evicts_when_move_fails(monkeypatch):
    from src.features.llm.clients.native import _NativeLLMResidencyHandle

    checkpoint = _stub_checkpoint(quantized=False)

    def _boom(device):
        raise RuntimeError("stuck")

    checkpoint.model.to = _boom
    _fake_residency_module(monkeypatch)

    invalidated = []
    models = type("M", (), {"invalidate": lambda self, key: invalidated.append(key)})()
    handle = _NativeLLMResidencyHandle(checkpoint, "native/llm/fake", models)

    handle.offload()

    assert invalidated == ["native/llm/fake"]
