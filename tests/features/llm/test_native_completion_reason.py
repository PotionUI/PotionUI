"""Completion-reason detection for the native (in-process transformers)
provider — see LLM-08.

The native engine has no wire-level `finish_reason` to read: it has to infer
the boundary itself from the effective generation config. These tests are
SKIPPED until the locked patch at
`scratchpad/llm08-locked/native.patch` (see the LLM-08 report) lands on
`src/features/llm/clients/native.py` — that patch adds
`NativeLLMClient._eos_token_ids`/`_completion_outcome` and wires them into
both `generate_with_history` and `stream_with_history`. This file is
independent of `test_native_client.py` (owned by another lane) by design —
it duplicates the small checkpoint-building fixtures it needs rather than
importing that file's private helpers, so it never conflicts with concurrent
edits there.

CPU-only, no downloads — same tiny real Qwen3 checkpoint approach as
test_native_client.py.
"""

from __future__ import annotations

import numpy._core.multiarray  # noqa: F401

from types import SimpleNamespace

import pytest
import torch

pytest.importorskip("transformers")

from src.features.llm.repository import LLMConfig  # noqa: E402
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle  # noqa: E402

try:
    from src.features.llm.clients.native import NativeLLMClient
except ImportError:
    NativeLLMClient = None  # patch not applied yet


pytestmark = pytest.mark.skip(reason="LLM-08 native patch pending (scratchpad/llm08-locked/native.patch)")


@pytest.fixture(autouse=True)
def _no_real_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None, raising=False)
    monkeypatch.setattr(torch.cuda, "synchronize", lambda: None, raising=False)


@pytest.fixture(scope="session")
def tiny_qwen3_checkpoint_dir(tmp_path_factory):
    """A real, tiny, randomly-initialized Qwen3 checkpoint on disk — see
    test_native_client.py's fixture of the same shape/purpose (duplicated
    here rather than imported, so this file has no dependency on that one)."""
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM

    vocab = {"[UNK]": 0, "[PAD]": 1, "[BOS]": 2, "[EOS]": 3}
    for i, w in enumerate(["hello", "world", "the", "cat", "sat", "on", "mat"], start=4):
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
        num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2, head_dim=8,
        max_position_embeddings=64, pad_token_id=1, bos_token_id=2, eos_token_id=3,
    )
    model = Qwen3ForCausalLM(config)
    model.eval()

    models_dir = tmp_path_factory.mktemp("native_llm_completion_models_dir")
    d = models_dir / "llm" / "qwen3-tiny"
    d.mkdir(parents=True)
    model.save_pretrained(d)
    fast_tok.save_pretrained(d)
    return models_dir


@pytest.fixture
def native_checkpoint(tiny_qwen3_checkpoint_dir, monkeypatch):
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
        id="native-1", name="Native Test", type="native", enabled=True, base_url="",
        model=model_name, system_message="You are a test assistant.",
        temperature=0.0, max_tokens=4, timeout=30, supports_vision=False,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


def _fixed_generate(token_ids):
    """A `checkpoint.model.generate` replacement for the buffered
    (non-streaming) path: returns the full prompt+completion sequence, the
    real contract `generate_with_history` decodes against."""
    def _generate(input_ids, **_gen_kwargs):
        seq = input_ids
        for tid in token_ids:
            seq = torch.cat([seq, torch.tensor([[tid]])], dim=-1)
        return seq
    return _generate


def _scripted_streaming_generate(token_ids):
    """A `checkpoint.model.generate` replacement for the streaming path:
    drives the real `TextIteratorStreamer` with one `put()` per token (the
    prompt `put()` first, discarded by `skip_prompt=True`, exactly like real
    `generate()`), then returns the full sequence."""
    def _generate(*, input_ids, streamer, stopping_criteria=None, **_gen_kwargs):
        streamer.put(input_ids)
        seq = input_ids
        for tid in token_ids:
            t = torch.tensor([[tid]])
            streamer.put(t)
            seq = torch.cat([seq, t], dim=-1)
        return seq
    return _generate


# ---------------------------------------------------------------------------
# Pure unit tests for the two helpers the patch adds
# ---------------------------------------------------------------------------

class TestEosTokenIds:
    def test_none_normalizes_to_empty_list(self):
        checkpoint = SimpleNamespace(model=SimpleNamespace(generation_config=SimpleNamespace(eos_token_id=None)))
        assert NativeLLMClient._eos_token_ids(checkpoint) == []

    def test_int_normalizes_to_a_singleton_list(self):
        checkpoint = SimpleNamespace(model=SimpleNamespace(generation_config=SimpleNamespace(eos_token_id=3)))
        assert NativeLLMClient._eos_token_ids(checkpoint) == [3]

    def test_list_passes_through(self):
        checkpoint = SimpleNamespace(model=SimpleNamespace(generation_config=SimpleNamespace(eos_token_id=[3, 7])))
        assert NativeLLMClient._eos_token_ids(checkpoint) == [3, 7]


class TestCompletionOutcome:
    def test_eos_as_last_token_is_stop(self):
        outcome = NativeLLMClient._completion_outcome(
            last_token_id=3, completion_tokens=2, max_new_tokens=10, eos_ids=[3],
        )
        assert outcome == {"reason": "stop", "raw": None}

    def test_eos_as_last_token_wins_even_exactly_at_the_limit(self):
        """The card's explicit edge case: hitting max_new_tokens must never
        shadow a real EOS that happened to land exactly there."""
        outcome = NativeLLMClient._completion_outcome(
            last_token_id=3, completion_tokens=10, max_new_tokens=10, eos_ids=[3],
        )
        assert outcome == {"reason": "stop", "raw": None}

    def test_limit_reached_without_eos_is_length(self):
        outcome = NativeLLMClient._completion_outcome(
            last_token_id=5, completion_tokens=10, max_new_tokens=10, eos_ids=[3],
        )
        assert outcome == {"reason": "length", "raw": None}

    def test_neither_eos_nor_limit_is_unknown_not_guessed(self):
        outcome = NativeLLMClient._completion_outcome(
            last_token_id=5, completion_tokens=2, max_new_tokens=10, eos_ids=[3],
        )
        assert outcome == {"reason": "unknown", "raw": None}

    def test_no_generated_tokens_is_unknown(self):
        outcome = NativeLLMClient._completion_outcome(
            last_token_id=None, completion_tokens=0, max_new_tokens=10, eos_ids=[3],
        )
        assert outcome == {"reason": "unknown", "raw": None}

    def test_no_eos_ids_configured_still_reports_length_at_the_limit(self):
        outcome = NativeLLMClient._completion_outcome(
            last_token_id=5, completion_tokens=10, max_new_tokens=10, eos_ids=[],
        )
        assert outcome == {"reason": "length", "raw": None}


# ---------------------------------------------------------------------------
# End to end through the real generate_with_history entry point
# ---------------------------------------------------------------------------

class TestGenerateWithHistoryCompletionReason:
    @pytest.mark.asyncio
    async def test_eos_mid_generation_reports_stop(self, client, native_checkpoint, monkeypatch):
        name, path = native_checkpoint
        config = _config(name, max_tokens=10)
        checkpoint = client._acquire(path, config)
        eos_id = 3
        monkeypatch.setattr(checkpoint.model, "generate", _fixed_generate([5, 5, eos_id]))
        monkeypatch.setattr(checkpoint.model, "generation_config", SimpleNamespace(eos_token_id=eos_id))

        response = await client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        )

        assert response.completion == {"reason": "stop", "raw": None}
        assert response.finish_reason is None

    @pytest.mark.asyncio
    async def test_max_new_tokens_reached_without_eos_reports_length(self, client, native_checkpoint, monkeypatch):
        name, path = native_checkpoint
        config = _config(name, max_tokens=4)
        checkpoint = client._acquire(path, config)
        monkeypatch.setattr(checkpoint.model, "generate", _fixed_generate([5, 5, 5, 5]))
        monkeypatch.setattr(checkpoint.model, "generation_config", SimpleNamespace(eos_token_id=3))

        response = await client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        )

        assert response.completion == {"reason": "length", "raw": None}

    @pytest.mark.asyncio
    async def test_eos_exactly_at_the_limit_still_reports_stop(self, client, native_checkpoint, monkeypatch):
        name, path = native_checkpoint
        config = _config(name, max_tokens=4)
        checkpoint = client._acquire(path, config)
        eos_id = 3
        monkeypatch.setattr(checkpoint.model, "generate", _fixed_generate([5, 5, 5, eos_id]))
        monkeypatch.setattr(checkpoint.model, "generation_config", SimpleNamespace(eos_token_id=eos_id))

        response = await client.generate_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        )

        assert response.completion == {"reason": "stop", "raw": None}


# ---------------------------------------------------------------------------
# End to end through the real stream_with_history entry point
# ---------------------------------------------------------------------------

class TestStreamWithHistoryCompletionReason:
    @pytest.mark.asyncio
    async def test_eos_mid_generation_reports_stop_on_the_usage_event(self, client, native_checkpoint, monkeypatch):
        name, path = native_checkpoint
        config = _config(name, max_tokens=10)
        checkpoint = client._acquire(path, config)
        eos_id = 3
        monkeypatch.setattr(checkpoint.model, "generate", _scripted_streaming_generate([5, 5, eos_id]))
        monkeypatch.setattr(checkpoint.model, "generation_config", SimpleNamespace(eos_token_id=eos_id))

        events = []
        async for event in client.stream_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ):
            events.append(event)

        usage = events[-1]
        assert usage["type"] == "usage"
        assert usage["completion"] == {"reason": "stop", "raw": None}

    @pytest.mark.asyncio
    async def test_max_new_tokens_reached_without_eos_reports_length_on_the_usage_event(
        self, client, native_checkpoint, monkeypatch
    ):
        name, path = native_checkpoint
        config = _config(name, max_tokens=4)
        checkpoint = client._acquire(path, config)
        monkeypatch.setattr(checkpoint.model, "generate", _scripted_streaming_generate([5, 5, 5, 5]))
        monkeypatch.setattr(checkpoint.model, "generation_config", SimpleNamespace(eos_token_id=3))

        events = []
        async for event in client.stream_with_history(
            [{"role": "user", "content": "hi"}], config, config.system_message,
        ):
            events.append(event)

        usage = events[-1]
        assert usage["type"] == "usage"
        assert usage["completion"] == {"reason": "length", "raw": None}
