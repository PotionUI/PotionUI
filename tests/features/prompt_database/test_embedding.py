"""Unit coverage for embedding-provider selection, lazy load, and pooling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import torch

from src.features.prompt_database.embedding import (
    LocalEmbeddingProvider,
    OllamaEmbeddingProvider,
    build_embedding_provider,
)


def make_settings(**overrides):
    settings = MagicMock()

    def get_setting(key, default=None, *args, **kwargs):
        return overrides.get(key, default)

    settings.get_setting.side_effect = get_setting
    settings.get_models_dir.return_value = overrides.get("models_dir", "models")
    return settings


def test_build_embedding_provider_defaults_to_local():
    provider = build_embedding_provider(make_settings())

    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.model_name == LocalEmbeddingProvider.DEFAULT_MODEL
    assert provider.device == "cpu"
    # Explicit-Fetch policy: silent first-use auto-download is opt-in, not
    # the shipped default (see migration 108).
    assert provider.auto_download is False


def test_build_embedding_provider_honors_local_overrides():
    settings = make_settings(
        prompt_embedding_model="intfloat/e5-small",
        prompt_embedding_device="cuda",
        prompt_embedding_auto_download=False,
        models_dir="custom-models",
    )

    provider = build_embedding_provider(settings)

    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.model_name == "intfloat/e5-small"
    assert provider.device == "cuda"
    assert provider.auto_download is False
    assert provider.models_dir == "custom-models"


def test_build_embedding_provider_selects_ollama_when_configured():
    settings = make_settings(
        prompt_embedding_provider="ollama",
        prompt_embedding_ollama_base_url="http://ollama-host:11434",
        prompt_embedding_ollama_model="mxbai-embed-large",
    )

    provider = build_embedding_provider(settings)

    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.base_url == "http://ollama-host:11434"
    assert provider.model == "mxbai-embed-large"


def test_local_embedder_slug_is_stable_and_model_specific():
    provider = LocalEmbeddingProvider(model_name="BAAI/bge-small-en-v1.5")

    assert provider.embedder_slug == "local-baai-bge-small-en-v1-5"


def test_ollama_embedder_slug_is_model_specific():
    provider = OllamaEmbeddingProvider(model="nomic-embed-text")

    assert provider.embedder_slug == "ollama-nomic-embed-text"


def test_local_provider_construction_does_not_load_model():
    provider = LocalEmbeddingProvider(model_name="fake/model")

    assert provider._model is None
    assert provider._tokenizer is None


def test_is_available_true_when_weights_present_even_without_auto_download():
    provider = LocalEmbeddingProvider(model_name="fake/model", auto_download=False)
    provider._weights_present = MagicMock(return_value=True)

    assert _run(provider.is_available()) is True


def test_is_available_true_when_auto_download_allowed_without_weights():
    provider = LocalEmbeddingProvider(model_name="fake/model", auto_download=True)
    provider._weights_present = MagicMock(return_value=False)

    assert _run(provider.is_available()) is True


def test_is_available_false_without_weights_or_auto_download():
    provider = LocalEmbeddingProvider(model_name="fake/model", auto_download=False)
    provider._weights_present = MagicMock(return_value=False)

    assert _run(provider.is_available()) is False


def test_missing_weights_without_auto_download_raises_actionable_error():
    """With auto-download disabled and no weights on disk, `_ensure_loaded`
    must fail loudly with a message pointing at the fix (fetch the weights),
    never fall through to a silent background download."""
    provider = LocalEmbeddingProvider(model_name="fake/model", auto_download=False)
    provider._weights_present = MagicMock(return_value=False)

    with pytest.raises(RuntimeError, match="auto-download is disabled"):
        provider._ensure_loaded()


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _fake_tokenizer_and_model():
    """A fake tokenizer/model pair with a hand-checkable mean-pooling result.

    Two texts, three token slots each; the third slot of the first text is
    masked out so its huge [9, 9] hidden vector must not affect the mean.
    """
    input_ids = torch.tensor([[1, 2, 3], [4, 5, 0]])
    attention_mask = torch.tensor([[1, 1, 0], [1, 1, 1]])
    hidden = torch.tensor(
        [
            [[1.0, 0.0], [0.0, 1.0], [9.0, 9.0]],
            [[2.0, 0.0], [0.0, 2.0], [0.0, 0.0]],
        ]
    )

    tokenizer = MagicMock(return_value={"input_ids": input_ids, "attention_mask": attention_mask})
    model = MagicMock(return_value=SimpleNamespace(last_hidden_state=hidden))
    model.to.return_value = model
    model.eval.return_value = model
    return tokenizer, model


@pytest.mark.asyncio
async def test_embed_mean_pools_masked_tokens_and_l2_normalizes():
    provider = LocalEmbeddingProvider(model_name="fake/model", auto_download=False)
    provider._weights_present = MagicMock(return_value=True)
    tokenizer, model = _fake_tokenizer_and_model()

    with patch("transformers.AutoTokenizer.from_pretrained", return_value=tokenizer), patch(
        "transformers.AutoModel.from_pretrained", return_value=model
    ):
        result = await provider.embed(["a fox", "a forest"])

    # Text 1: mean of [1,0] and [0,1] (third token masked) = [0.5, 0.5], normalized.
    # Text 2: mean of [2,0], [0,2], [0,0] = [0.6667, 0.6667], normalized -> same direction.
    expected = [
        pytest.approx([0.70710677, 0.70710677], abs=1e-5),
        pytest.approx([0.70710677, 0.70710677], abs=1e-5),
    ]
    assert result[0] == expected[0]
    assert result[1] == expected[1]


@pytest.mark.asyncio
async def test_embed_loads_model_lazily_exactly_once():
    provider = LocalEmbeddingProvider(model_name="fake/model", auto_download=False)
    provider._weights_present = MagicMock(return_value=True)
    tokenizer, model = _fake_tokenizer_and_model()

    with patch(
        "transformers.AutoTokenizer.from_pretrained", return_value=tokenizer
    ) as tokenizer_loader, patch(
        "transformers.AutoModel.from_pretrained", return_value=model
    ) as model_loader:
        assert tokenizer_loader.call_count == 0
        assert model_loader.call_count == 0

        await provider.embed(["a fox"])
        assert tokenizer_loader.call_count == 1
        assert model_loader.call_count == 1

        await provider.embed(["a forest"])
        assert tokenizer_loader.call_count == 1
        assert model_loader.call_count == 1


@pytest.mark.asyncio
async def test_embed_empty_input_short_circuits_without_loading():
    provider = LocalEmbeddingProvider(model_name="fake/model")
    provider._ensure_loaded = MagicMock(side_effect=AssertionError("should not load"))

    assert await provider.embed([]) == []


class TestResolveStatus:
    """Presence/path/size the admin settings status endpoint reads."""

    def test_missing_weights_report_absent_with_no_size(self, tmp_path):
        status = LocalEmbeddingProvider.resolve_status("BAAI/bge-small-en-v1.5", str(tmp_path))

        assert status["present"] is False
        assert status["size"] is None
        assert status["path"] == str(tmp_path / "text_embeddings" / "baai-bge-small-en-v1-5")

    def test_present_weights_report_total_size(self, tmp_path):
        target = tmp_path / "text_embeddings" / "baai-bge-small-en-v1-5"
        target.mkdir(parents=True)
        (target / "model.safetensors").write_bytes(b"x" * 10)
        (target / "config.json").write_bytes(b"y" * 5)

        status = LocalEmbeddingProvider.resolve_status("BAAI/bge-small-en-v1.5", str(tmp_path))

        assert status["present"] is True
        assert status["size"] == 15
        assert status["path"] == str(target)

    def test_local_dir_for_matches_instance_local_path(self, tmp_path):
        provider = LocalEmbeddingProvider(model_name="fake/model", models_dir=str(tmp_path))

        assert LocalEmbeddingProvider.local_dir_for("fake/model", str(tmp_path)) == provider._local_path()
