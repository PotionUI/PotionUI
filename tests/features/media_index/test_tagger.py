"""WDTaggerProvider: lazy loading, preprocessing contract, prediction mapping."""

import math
from unittest.mock import MagicMock

import pytest

from src.features.media_index.tagger import (
    RATING_NAMES,
    SystemTagPrediction,
    WDTaggerProvider,
    build_tagger_provider,
)
from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager


def _provider(**overrides):
    kwargs = dict(
        model_name="SmilingWolf/wd-vit-tagger-v3",
        models_dir="/nonexistent",
        auto_download=False,
        tag_threshold=0.35,
        character_threshold=0.75,
    )
    kwargs.update(overrides)
    return WDTaggerProvider(**kwargs)


class TestLazyLoad:
    def test_construction_loads_nothing(self):
        provider = _provider()
        assert provider._tag_names == []
        assert provider._model_config is None

    def test_missing_weights_without_auto_download_raises(self):
        provider = _provider()
        with pytest.raises(RuntimeError, match="auto-download is disabled"):
            provider._ensure_loaded()

    def test_is_available_reflects_weights_or_download_gate(self):
        assert _provider(auto_download=False).is_available() is False
        assert _provider(auto_download=True).is_available() is True

    def test_provenance_is_model_slug(self):
        assert _provider().provenance == "smilingwolf-wd-vit-tagger-v3"


class TestPreprocessing:
    """Pins the torch-path order: white composite -> pad -> normalize -> BGR."""

    def test_transparency_composites_over_white_and_channels_are_bgr(self):
        import torch
        from PIL import Image

        provider = _provider()
        provider._input_size = 2

        image = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
        image.putpixel((1, 1), (0, 0, 0, 0))
        tensor = provider._preprocess(image)

        assert tensor.shape == (3, 2, 2)
        # Red pixel: after normalize R=1, G=B=-1; after RGB->BGR channel 0 is blue.
        assert tensor[0, 0, 0].item() == pytest.approx(-1.0)
        assert tensor[2, 0, 0].item() == pytest.approx(1.0)
        # Fully transparent pixel composites to white: 1.0 in every channel.
        assert torch.allclose(tensor[:, 1, 1], torch.ones(3))

    def test_non_square_input_pads_to_white_square(self):
        from PIL import Image

        provider = _provider()
        provider._input_size = 4

        image = Image.new("RGB", (4, 2), (0, 0, 0))
        tensor = provider._preprocess(image)

        assert tensor.shape == (3, 4, 4)
        # Top and bottom rows are padding: white -> 1.0 after normalize.
        assert tensor[:, 0, :].min().item() == pytest.approx(1.0)
        assert tensor[:, 3, :].min().item() == pytest.approx(1.0)
        # Center rows are the black image: -1.0 after normalize.
        assert tensor[:, 1, :].max().item() == pytest.approx(-1.0)


class TestPredictionMapping:
    def _loaded_provider(self):
        provider = _provider()
        provider._tag_names = [
            "general", "sensitive", "questionable", "explicit",
            "1girl", "outdoors", "hatsune_miku",
        ]
        provider._tag_categories = [9, 9, 9, 9, 0, 0, 4]
        return provider

    def test_ratings_always_stored_and_never_thresholded(self):
        provider = self._loaded_provider()
        result = provider._predictions_to_result([0.9, 0.05, 0.03, 0.02, 0.1, 0.1, 0.1])
        assert set(result.ratings) == set(RATING_NAMES)
        assert result.ratings["general"] == pytest.approx(0.9)
        assert result.ratings["explicit"] == pytest.approx(0.02)
        assert result.tags == []

    def test_general_tags_use_tag_threshold(self):
        provider = self._loaded_provider()
        result = provider._predictions_to_result([0, 0, 0, 0, 0.36, 0.34, 0])
        assert [t.tag for t in result.tags] == ["1girl"]
        assert result.tags[0].category == "general"

    def test_character_tags_use_character_threshold(self):
        provider = self._loaded_provider()
        result = provider._predictions_to_result([0, 0, 0, 0, 0, 0, 0.5])
        assert result.tags == []
        result = provider._predictions_to_result([0, 0, 0, 0, 0, 0, 0.8])
        assert [t.tag for t in result.tags] == ["hatsune_miku"]
        assert result.tags[0].category == "character"

    def test_tags_sorted_by_confidence_desc(self):
        provider = self._loaded_provider()
        result = provider._predictions_to_result([0, 0, 0, 0, 0.4, 0.9, 0])
        assert [t.tag for t in result.tags] == ["outdoors", "1girl"]

    def test_tag_image_applies_sigmoid_to_model_logits(self):
        import torch
        from PIL import Image

        provider = self._loaded_provider()
        provider._input_size = 2
        provider._model_lifecycle_manager = ModelLifecycleManager(gpu_manager=None, settings_manager=None)
        logits = torch.tensor([[0.0, -20.0, -20.0, -20.0, 20.0, -20.0, -20.0]])
        # `_load_model` is the ModelLifecycleManager loader tag_image() acquires
        # through - stubbing it (not `_model` directly) proves the weights are
        # no longer held privately on the provider.
        provider._load_model = MagicMock(return_value=MagicMock(return_value=logits))

        result = provider.tag_image(Image.new("RGB", (2, 2), (0, 0, 0)))

        # sigmoid(0)=0.5, sigmoid(20)~=1: proves sigmoid (softmax would not
        # give 0.5 for the zero logit alongside a 20 logit).
        assert result.ratings["general"] == pytest.approx(0.5, abs=1e-6)
        assert [t.tag for t in result.tags] == ["1girl"]
        assert result.tags[0].confidence == pytest.approx(1.0, abs=1e-6)


class TestModelLifecycleIntegration:
    """The checkpoint is lazy-loaded through ModelLifecycleManager, never
    held privately - lazy load, evictable, idempotent eviction."""

    def _provider(self):
        provider = _provider()
        provider._tag_names = ["general"]
        provider._tag_categories = [9]
        provider._input_size = 2
        provider._model_lifecycle_manager = ModelLifecycleManager(gpu_manager=None, settings_manager=None)
        return provider

    @staticmethod
    def _stub_load_model(provider):
        import torch

        class _FakeCheckpoint:
            """A plain (non-Mock) stand-in for the cached model: a MagicMock's
            own bookkeeping references push `sys.getrefcount` sky-high and its
            auto-attributes defeat `getattr(value, 'estimated_vram_gb', None)`,
            which would fail eviction's `%.2f` formatting on ModelLifecycleManager's
            own log line - unrelated to the eviction contract under test here."""

            def __call__(self, batch):
                return torch.tensor([[0.0]])

        loader = MagicMock(return_value=_FakeCheckpoint())
        provider._load_model = loader
        return loader

    def _tag_a_pixel(self, provider):
        from PIL import Image

        provider.tag_image(Image.new("RGB", (2, 2), (0, 0, 0)))

    def test_not_loaded_until_first_use(self):
        provider = self._provider()
        self._stub_load_model(provider)
        models = provider._model_lifecycle_manager

        assert provider._cache_key() not in models._entries
        provider._load_model.assert_not_called()

    def test_first_tag_loads_and_caches_the_model(self):
        provider = self._provider()
        loader = self._stub_load_model(provider)
        models = provider._model_lifecycle_manager

        self._tag_a_pixel(provider)

        assert loader.call_count == 1
        assert provider._cache_key() in models._entries

        # A second tag reuses the cached entry instead of reloading.
        self._tag_a_pixel(provider)
        assert loader.call_count == 1

    def test_cached_model_is_evictable(self):
        provider = self._provider()
        self._stub_load_model(provider)
        models = provider._model_lifecycle_manager
        self._tag_a_pixel(provider)
        key = provider._cache_key()
        assert key in models._entries

        models.invalidate(key)

        assert key not in models._entries

    def test_eviction_is_idempotent_when_the_key_is_already_gone(self):
        provider = self._provider()
        models = provider._model_lifecycle_manager
        key = provider._cache_key()
        assert key not in models._entries

        models.invalidate(key)  # never loaded - must not raise
        assert key not in models._entries

        self._stub_load_model(provider)
        self._tag_a_pixel(provider)
        models.invalidate(key)
        assert key not in models._entries
        models.invalidate(key)  # already evicted - must not raise
        assert key not in models._entries

    def test_evicted_model_reloads_on_next_use(self):
        provider = self._provider()
        loader = self._stub_load_model(provider)
        models = provider._model_lifecycle_manager
        self._tag_a_pixel(provider)
        assert loader.call_count == 1

        models.invalidate(provider._cache_key())
        self._tag_a_pixel(provider)

        assert loader.call_count == 2

    def test_missing_lifecycle_manager_raises_clean_error(self):
        import src.platform.runtime.model_lifecycle.manager as manager_module

        provider = self._provider()
        provider._model_lifecycle_manager = None
        saved = manager_module._default_manager
        manager_module._default_manager = None
        try:
            with pytest.raises(RuntimeError, match="ModelLifecycleManager"):
                provider._models()
        finally:
            manager_module._default_manager = saved

    def test_is_loaded_reflects_residency_not_disk_presence(self):
        """`is_loaded()` must never be conflated with `is_available()` - a
        status caller distinguishing on-disk weights from an in-memory
        checkpoint needs both signals independently. Weights are absent here
        (auto_download=False, /nonexistent dir), so `is_available()` is
        False throughout while `is_loaded()` still tracks residency on its
        own - proving the two are never derived from each other."""
        provider = self._provider()
        self._stub_load_model(provider)
        assert provider.is_available() is False
        assert provider.is_loaded() is False

        self._tag_a_pixel(provider)
        assert provider.is_available() is False
        assert provider.is_loaded() is True

        provider._model_lifecycle_manager.invalidate(provider._cache_key())
        assert provider.is_loaded() is False

    def test_is_loaded_false_without_a_lifecycle_manager(self):
        provider = self._provider()
        provider._model_lifecycle_manager = None
        import src.platform.runtime.model_lifecycle.manager as manager_module

        saved = manager_module._default_manager
        manager_module._default_manager = None
        try:
            assert provider.is_loaded() is False
        finally:
            manager_module._default_manager = saved


class TestResolveStatus:
    """Presence/path/size the admin settings status endpoint reads."""

    def test_missing_weights_report_absent_with_no_size(self, tmp_path):
        status = WDTaggerProvider.resolve_status("SmilingWolf/wd-vit-tagger-v3", str(tmp_path))

        assert status["present"] is False
        assert status["size"] is None
        assert status["path"] == str(tmp_path / "taggers" / "smilingwolf-wd-vit-tagger-v3")

    def test_partial_files_are_not_present(self, tmp_path):
        target = tmp_path / "taggers" / "smilingwolf-wd-vit-tagger-v3"
        target.mkdir(parents=True)
        (target / "model.safetensors").write_bytes(b"x" * 10)

        status = WDTaggerProvider.resolve_status("SmilingWolf/wd-vit-tagger-v3", str(tmp_path))

        assert status["present"] is False
        assert status["size"] is None

    def test_present_weights_report_total_size(self, tmp_path):
        target = tmp_path / "taggers" / "smilingwolf-wd-vit-tagger-v3"
        target.mkdir(parents=True)
        (target / "model.safetensors").write_bytes(b"x" * 10)
        (target / "selected_tags.csv").write_bytes(b"y" * 5)

        status = WDTaggerProvider.resolve_status("SmilingWolf/wd-vit-tagger-v3", str(tmp_path))

        assert status["present"] is True
        assert status["size"] == 15
        assert status["path"] == str(target)


class TestBuildFromSettings:
    def test_build_defaults_to_no_auto_download(self):
        """Explicit-Fetch policy: silent first-use auto-download is opt-in,
        not the shipped default (see migration 108)."""
        settings = MagicMock()
        settings.get_setting.side_effect = lambda key, default=None: default
        settings.get_models_dir.return_value = "/models"

        provider = build_tagger_provider(settings)

        assert provider.auto_download is False

    def test_build_reads_settings(self):
        values = {
            "media_tagger_model": "SmilingWolf/wd-swinv2-tagger-v3",
            "media_tagger_device": "cpu",
            "media_tagger_auto_download": False,
            "media_tagger_tag_threshold": 0.5,
            "media_tagger_character_threshold": 0.9,
        }
        settings = MagicMock()
        settings.get_setting.side_effect = lambda key, default=None: values.get(key, default)
        settings.get_models_dir.return_value = "/models"

        provider = build_tagger_provider(settings)

        assert provider.model_name == "SmilingWolf/wd-swinv2-tagger-v3"
        assert provider.models_dir == "/models"
        assert provider.auto_download is False
        assert provider.tag_threshold == 0.5
        assert provider.character_threshold == 0.9
        assert provider.provenance == "smilingwolf-wd-swinv2-tagger-v3"
