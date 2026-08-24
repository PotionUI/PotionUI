"""SiglipVisionEmbedder: lazy load, query prep, and the fixed-padding contract."""

from unittest.mock import MagicMock

import pytest
import torch

from src.features.media_index.vision_embedder import (
    SiglipVisionEmbedder,
    build_vision_embedder,
)
from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager


def make_settings(**overrides):
    settings = MagicMock()

    def get_setting(key, default=None, *args, **kwargs):
        return overrides.get(key, default)

    settings.get_setting.side_effect = get_setting
    settings.get_models_dir.return_value = overrides.get("models_dir", "models")
    return settings


def make_loaded_embedder(dim=4):
    """An embedder with a mocked processor (bypassing `_ensure_processor`)
    and a mocked model wired in as the `ModelLifecycleManager` loader's
    return value (bypassing `_load_model`) - the model is never held
    privately on the instance, so stubbing `_load_model` is how a test
    supplies one."""
    embedder = SiglipVisionEmbedder(
        model_name="fake/siglip",
        model_lifecycle_manager=ModelLifecycleManager(gpu_manager=None, settings_manager=None),
    )
    processor = MagicMock()
    processor.return_value = {"input_ids": torch.ones(1, 64, dtype=torch.long)}
    model = MagicMock()
    model.dtype = torch.float32
    model.get_text_features.return_value = torch.tensor([[3.0, 4.0, 0.0, 0.0]])
    model.get_image_features.return_value = torch.tensor([[0.0, 6.0, 8.0, 0.0]])
    embedder._processor = processor
    embedder._load_model = MagicMock(return_value=model)
    return embedder, processor, model


# ---------------------------------------------------------------------------
# Construction & settings
# ---------------------------------------------------------------------------

def test_construction_does_not_load_model():
    embedder = SiglipVisionEmbedder(model_name="fake/siglip")
    assert embedder._processor is None
    assert embedder._source_path is None


def test_build_vision_embedder_defaults():
    embedder = build_vision_embedder(make_settings())

    assert embedder.model_name == "google/siglip-base-patch16-224"
    assert embedder.device == "cpu"
    # Explicit-Fetch policy: silent first-use auto-download is opt-in, not
    # the shipped default (see migration 108).
    assert embedder.auto_download is False


def test_missing_weights_without_auto_download_raises_actionable_error():
    embedder = SiglipVisionEmbedder(
        model_name="fake/siglip", models_dir="/nonexistent", auto_download=False
    )

    with pytest.raises(RuntimeError, match="auto-download is disabled"):
        embedder._ensure_processor()


def test_build_vision_embedder_honors_overrides():
    embedder = build_vision_embedder(make_settings(
        media_vision_model="google/siglip-so400m-patch14-384",
        media_vision_device="cuda:0",
        media_vision_auto_download=False,
        models_dir="custom-models",
    ))

    assert embedder.model_name == "google/siglip-so400m-patch14-384"
    assert embedder.device == "cuda:0"
    assert embedder.auto_download is False
    assert embedder.models_dir == "custom-models"


def test_embedder_slug_is_stable_and_model_specific():
    embedder = SiglipVisionEmbedder(model_name="google/siglip-base-patch16-224")
    assert embedder.embedder_slug == "local-google-siglip-base-patch16-224"


# ---------------------------------------------------------------------------
# Query preparation
# ---------------------------------------------------------------------------

def test_prepare_query_lowercases():
    assert SiglipVisionEmbedder.prepare_query("A Castle On A Hill At Dusk") == (
        "a castle on a hill at dusk"
    )


def test_prepare_query_wraps_short_queries_as_a_photo_of():
    assert SiglipVisionEmbedder.prepare_query("Fox") == "a photo of fox"
    assert SiglipVisionEmbedder.prepare_query("Red Fox") == "a photo of red fox"


def test_prepare_query_leaves_longer_queries_unwrapped():
    assert SiglipVisionEmbedder.prepare_query("red fox in snow") == "red fox in snow"


def test_prepare_query_collapses_whitespace():
    assert SiglipVisionEmbedder.prepare_query("  red   fox  in   snow ") == "red fox in snow"


# ---------------------------------------------------------------------------
# Text tower: the fixed-64-token padding contract (do not regress)
# ---------------------------------------------------------------------------

def test_embed_texts_uses_max_length_padding():
    embedder, processor, _ = make_loaded_embedder()

    embedder.embed_texts(["Red Fox"])

    kwargs = processor.call_args.kwargs
    assert kwargs["padding"] == "max_length"
    assert kwargs["max_length"] == 64
    assert kwargs["truncation"] is True
    assert kwargs["return_tensors"] == "pt"
    assert kwargs["text"] == ["a photo of red fox"]


def test_embed_texts_returns_l2_normalized_vectors():
    embedder, _, _ = make_loaded_embedder()

    [vector] = embedder.embed_texts(["fox"])

    assert vector == pytest.approx([0.6, 0.8, 0.0, 0.0])


def test_features_extracted_from_transformers5_output_objects():
    class FakeOutput:
        pooler_output = torch.tensor([[3.0, 4.0, 0.0, 0.0]])

    embedder, _, model = make_loaded_embedder()
    model.get_text_features.return_value = FakeOutput()

    [vector] = embedder.embed_texts(["fox"])

    assert vector == pytest.approx([0.6, 0.8, 0.0, 0.0])


def test_embed_texts_empty_input_never_touches_model():
    embedder = SiglipVisionEmbedder(model_name="fake/siglip", auto_download=False)
    assert embedder.embed_texts([]) == []
    assert embedder._processor is None


# ---------------------------------------------------------------------------
# Image tower
# ---------------------------------------------------------------------------

def test_embed_images_uses_image_features_and_normalizes():
    from PIL import Image

    embedder, processor, model = make_loaded_embedder()
    processor.return_value = {"pixel_values": torch.zeros(1, 3, 224, 224)}

    [vector] = embedder.embed_images([Image.new("RGB", (8, 8))])

    assert model.get_image_features.called
    assert vector == pytest.approx([0.0, 0.6, 0.8, 0.0])


def test_embed_images_empty_input_never_touches_model():
    embedder = SiglipVisionEmbedder(model_name="fake/siglip", auto_download=False)
    assert embedder.embed_images([]) == []
    assert embedder._processor is None


# ---------------------------------------------------------------------------
# ModelLifecycleManager integration: lazy load, evictable, idempotent
# eviction - the model is never held privately on the instance.
# ---------------------------------------------------------------------------

class _FakeSiglipModel:
    """A plain (non-Mock) stand-in for the cached model. A MagicMock's own
    bookkeeping references push `sys.getrefcount` far above the "sole owner"
    threshold, and its auto-attributes defeat
    `getattr(value, 'estimated_vram_gb', None)` - returning a Mock instead of
    None - which crashes ModelLifecycleManager's `%.2f` eviction log line.
    Unrelated to the eviction contract under test here."""

    dtype = torch.float32

    def get_image_features(self, pixel_values):
        return torch.tensor([[0.0, 6.0, 8.0, 0.0]])


def _embedder_for_lifecycle_tests():
    embedder = SiglipVisionEmbedder(
        model_name="fake/siglip",
        model_lifecycle_manager=ModelLifecycleManager(gpu_manager=None, settings_manager=None),
    )
    processor = MagicMock()
    processor.return_value = {"pixel_values": torch.zeros(1, 3, 224, 224)}
    embedder._processor = processor
    embedder._load_model = MagicMock(return_value=_FakeSiglipModel())
    return embedder


def test_not_loaded_until_first_embed():
    embedder = _embedder_for_lifecycle_tests()
    models = embedder._model_lifecycle_manager

    assert embedder._cache_key() not in models._entries
    embedder._load_model.assert_not_called()


def test_first_embed_loads_and_caches_the_model():
    from PIL import Image

    embedder = _embedder_for_lifecycle_tests()
    models = embedder._model_lifecycle_manager

    embedder.embed_images([Image.new("RGB", (8, 8))])

    assert embedder._load_model.call_count == 1
    assert embedder._cache_key() in models._entries

    embedder.embed_images([Image.new("RGB", (8, 8))])
    assert embedder._load_model.call_count == 1


def test_cached_model_is_evictable():
    from PIL import Image

    embedder = _embedder_for_lifecycle_tests()
    models = embedder._model_lifecycle_manager
    embedder.embed_images([Image.new("RGB", (8, 8))])
    key = embedder._cache_key()
    assert key in models._entries

    models.invalidate(key)

    assert key not in models._entries


def test_eviction_is_idempotent_when_the_key_is_already_gone():
    from PIL import Image

    embedder = _embedder_for_lifecycle_tests()
    models = embedder._model_lifecycle_manager
    key = embedder._cache_key()

    models.invalidate(key)  # never loaded - must not raise
    assert key not in models._entries

    embedder.embed_images([Image.new("RGB", (8, 8))])
    models.invalidate(key)
    assert key not in models._entries
    models.invalidate(key)  # already evicted - must not raise
    assert key not in models._entries


def test_evicted_model_reloads_on_next_use():
    from PIL import Image

    embedder = _embedder_for_lifecycle_tests()
    models = embedder._model_lifecycle_manager
    embedder.embed_images([Image.new("RGB", (8, 8))])
    assert embedder._load_model.call_count == 1

    models.invalidate(embedder._cache_key())
    embedder.embed_images([Image.new("RGB", (8, 8))])

    assert embedder._load_model.call_count == 2


def test_missing_lifecycle_manager_raises_clean_error():
    import src.platform.runtime.model_lifecycle.manager as manager_module

    embedder = SiglipVisionEmbedder(model_name="fake/siglip")
    saved = manager_module._default_manager
    manager_module._default_manager = None
    try:
        with pytest.raises(RuntimeError, match="ModelLifecycleManager"):
            embedder._models()
    finally:
        manager_module._default_manager = saved


def test_is_loaded_reflects_residency_not_disk_presence():
    """`is_loaded()` must never be conflated with `is_available()` - a
    status caller distinguishing on-disk weights from an in-memory
    checkpoint needs both signals independently. Weights are absent here
    (auto_download=False default, nonexistent model dir), so
    `is_available()` is False throughout while `is_loaded()` still tracks
    residency on its own - proving the two are never derived from each
    other."""
    from PIL import Image

    embedder = _embedder_for_lifecycle_tests()
    embedder.auto_download = False
    assert embedder.is_available() is False
    assert embedder.is_loaded() is False

    embedder.embed_images([Image.new("RGB", (8, 8))])
    assert embedder.is_available() is False
    assert embedder.is_loaded() is True

    embedder._model_lifecycle_manager.invalidate(embedder._cache_key())
    assert embedder.is_loaded() is False


def test_is_loaded_false_without_a_lifecycle_manager():
    import src.platform.runtime.model_lifecycle.manager as manager_module

    embedder = SiglipVisionEmbedder(model_name="fake/siglip")
    saved = manager_module._default_manager
    manager_module._default_manager = None
    try:
        assert embedder.is_loaded() is False
    finally:
        manager_module._default_manager = saved


# ---------------------------------------------------------------------------
# resolve_status (the admin settings status endpoint)
# ---------------------------------------------------------------------------

def test_resolve_status_missing_weights_report_absent_with_no_size(tmp_path):
    status = SiglipVisionEmbedder.resolve_status("google/siglip-base-patch16-224", str(tmp_path))

    assert status["present"] is False
    assert status["size"] is None
    assert status["path"] == str(tmp_path / "vision_embeddings" / "google-siglip-base-patch16-224")


def test_resolve_status_present_weights_report_total_size(tmp_path):
    target = tmp_path / "vision_embeddings" / "google-siglip-base-patch16-224"
    target.mkdir(parents=True)
    (target / "model.safetensors").write_bytes(b"x" * 20)

    status = SiglipVisionEmbedder.resolve_status("google/siglip-base-patch16-224", str(tmp_path))

    assert status["present"] is True
    assert status["size"] == 20
    assert status["path"] == str(target)
