"""Pins the interaction the team lead flagged: a chat turn
(NativeLLMClient) and a native diffusion generation share ONE
ModelLifecycleManager, and each has its own end-of-turn/end-of-generation
sweep (`end_lease` -> `_sweep_unused_owned`, gated on
`model_cache_scope=preset`, the default). Both directions must hold:

1. A chat turn's own `end_lease()` must never run the generation-end sweep at
   all - a chat turn's `_cache_owner` stays at its ContextVar default (None)
   for the whole call (NativeLLMClient._leased() deliberately never calls
   `begin_generation()`), and `_sweep_unused_owned` only fires when
   `owner is not None`. So a chat turn ending must leave OTHER presets'
   entries completely untouched.
2. A generation's own end-of-generation sweep (which DOES run, for the
   generation's real preset owner) must not sweep the native LLM entry away
   via the owner-sweep path — the sweep only matches entries whose owner
   equals the finishing generation's owner, and the LLM entry's owner is
   None, never a preset id. The LLM entry must still be ordinarily evictable
   under RAM-pressure LRU, which is a separate, owner-blind mechanism.

`tests/platform/runtime/model_lifecycle/test_manager.py::TestPresetScopedCache::
test_ownerless_entry_survives_generation_sweep` already pins the generic
manager-level mechanism with a synthetic FakeModel; these tests pin it through
the ACTUAL NativeLLMClient code path with a real tiny checkpoint, so a future
change to `_leased()` (e.g. someone "helpfully" adding a `begin_generation()`
call) gets caught here even if the generic mechanism test still passes.
"""

from __future__ import annotations

import pytest
import torch

from src.features.llm.clients.native import NativeLLMClient
from src.features.llm.repository import LLMConfig
from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager


@pytest.fixture(autouse=True)
def _no_real_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None, raising=False)
    monkeypatch.setattr(torch.cuda, "synchronize", lambda: None, raising=False)


class _Settings:
    """Minimal settings stub — model_cache_scope=preset (the default)."""

    def get_setting(self, key, default=None, user_id=None):
        return "preset" if key == "model_cache_scope" else default


class _FakeDiffusionModel:
    """Stand-in for a diffusion checkpoint entry — same eviction contract
    ModelLifecycleManager's own test suite uses (a `.module` + `.unload()`)."""

    def __init__(self, name):
        self.name = name
        self.module = object()

    def unload(self):
        self.module = None


def _run_generation(models_manager, owner, gen_id, acquisitions):
    """Mirrors GenerationManager's real call order
    (src/features/generation/generation.py): begin_lease -> begin_generation
    -> acquire(s) -> end_lease. `acquisitions`: list of (key, loader)."""
    models_manager.begin_lease(gen_id)
    models_manager.begin_generation(owner)
    for key, loader in acquisitions:
        models_manager.acquire(key, "fp", loader)
    models_manager.end_lease(gen_id)


@pytest.fixture
def models_manager():
    return ModelLifecycleManager(gpu_manager=None, settings_manager=_Settings())


@pytest.fixture
def client(models_manager):
    return NativeLLMClient(models_manager)


@pytest.fixture
def native_checkpoint(tmp_path, monkeypatch):
    """A real tiny Qwen3 checkpoint, HF-layout, under a fake models_dir/llm/."""
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM

    import src.features.llm.native_library as native_library_module

    vocab = {"[UNK]": 0, "[PAD]": 1, "[BOS]": 2, "[EOS]": 3}
    for i, w in enumerate(["hello", "world", "assistant", "user", "system", ":", "\n"], start=4):
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
        vocab_size=len(vocab) + 10, hidden_size=32, intermediate_size=64,
        num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2,
        head_dim=8, max_position_embeddings=64, pad_token_id=1, bos_token_id=2, eos_token_id=3,
    )
    model = Qwen3ForCausalLM(config)
    model.eval()

    models_dir = tmp_path / "models_dir"
    d = models_dir / "llm" / "qwen3-tiny"
    d.mkdir(parents=True)
    model.save_pretrained(d)
    fast_tok.save_pretrained(d)

    monkeypatch.setattr(native_library_module, "_models_dir", lambda: models_dir)
    return "qwen3-tiny", str(d.resolve())


def _chat_config(name: str) -> LLMConfig:
    return LLMConfig(
        id="native-1", name="Native Test", type="native", enabled=True, base_url="",
        model=name, system_message="You are a test assistant.", temperature=0.0,
        max_tokens=4, timeout=30, supports_vision=False,
    )


@pytest.mark.asyncio
async def test_chat_turn_end_lease_never_sweeps_other_presets_entries(client, models_manager, native_checkpoint):
    """Direction 1: a chat turn ending must never trigger the generation-end
    sweep for ANY preset — its own owner is None the whole time."""
    name, _ = native_checkpoint

    # A generation for presets/A already ran and finished, leaving an
    # untouched-by-that-generation entry of its own owned by presets/A.
    _run_generation(models_manager, "presets/A", "gen-1", [
        ("dit", lambda: _FakeDiffusionModel("dit")),
    ])
    assert "dit" in models_manager._entries

    # A chat turn runs on the SAME manager.
    await client.generate_with_history([{"role": "user", "content": "hi"}], _chat_config(name), "sys")

    # If NativeLLMClient ever started calling begin_generation(), this chat
    # turn's end_lease would stamp owner=None and (with owner is not None
    # required for the sweep) still be safe — but if someone "fixed" it to
    # tag chat turns with a real owner, a mismatched sweep could fire and
    # this would catch it either way.
    assert "dit" in models_manager._entries


@pytest.mark.asyncio
async def test_generation_end_sweep_never_evicts_ownerless_llm_entry_but_lru_still_can(client, models_manager, native_checkpoint):
    """Direction 2: a generation's own end-of-generation sweep must not touch
    the native LLM entry (owner=None never matches a preset owner) — but the
    entry must still be ordinarily evictable via RAM-pressure LRU once the
    chat turn's lease has released it."""
    name, path = native_checkpoint
    key = f"native/llm/{path}"

    # A chat turn runs and finishes — the LLM entry is now unleased, owner=None.
    await client.generate_with_history([{"role": "user", "content": "hi"}], _chat_config(name), "sys")
    assert key in models_manager._entries
    assert models_manager._entries[key].owner is None
    assert not models_manager._entries[key].leased_by

    # A generation for presets/A runs and finishes without touching the LLM
    # entry at all — its end-of-generation sweep fires for presets/A only.
    _run_generation(models_manager, "presets/A", "gen-1", [
        ("dit", lambda: _FakeDiffusionModel("dit")),
    ])

    # The owner-sweep must not have removed the (owner=None) LLM entry.
    assert key in models_manager._entries
    # ...but it remains ordinary-LRU evictable (unleased, not mid-acquire) —
    # a real RAM-pressure pass COULD reclaim it, just not via the owner sweep.
    assert key in models_manager._evictable_keys()
