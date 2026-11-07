import contextvars
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import torch
from PIL import Image

import src.plugin_api as root
from src.features.media_index.vision_embedder import SiglipVisionEmbedder
from src.platform.runtime.model_lifecycle import lifecycle as lifecycle_module
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from src.plugin_api import media
from tests.fixtures.model_roots import make_roots

MODEL = "google/siglip-base-patch16-224"


class Downloads:
    def __init__(self):
        self.calls = []

    def ensure_local_hf_repo(self, repo, target):
        self.calls.append((repo, target))
        raise RuntimeError("a download was asked for")


class Siglip:
    dtype = torch.float32

    def get_image_features(self, pixel_values):
        return torch.tensor([[0.0, 6.0, 8.0, 0.0]] * pixel_values.shape[0])


class Weights:
    pass


@pytest.fixture
def lifecycle(monkeypatch):
    monkeypatch.setattr(lifecycle_module, "_default_lifecycle", None)
    return ModelLifecycle(gpu_monitor=None, settings=None)


def _embedder(tmp_path, lifecycle, downloads=None, auto_download=True):
    return SiglipVisionEmbedder(
        resolver=make_roots(tmp_path, types=[]),
        model_name=MODEL,
        device="cpu",
        auto_download=auto_download,
        download_queue=downloads,
        model_lifecycle=lifecycle,
    )


def _loaded(embedder):
    processor = MagicMock(side_effect=lambda images, return_tensors: {"pixel_values": torch.zeros(len(images), 3, 4, 4)})
    embedder._processor = processor
    embedder._load_model = MagicMock(return_value=Siglip())
    return processor


def _put_weights(embedder):
    folder = embedder.local_dir_for(embedder.model_name, embedder.resolver)
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{}")
    return folder


def _world(embedder):
    return SimpleNamespace(media_indexer=SimpleNamespace(vision_embedder=embedder))


def test_the_image_embedder_says_which_model_it_is_whether_its_weights_are_here_and_whether_it_is_loaded(tmp_path, lifecycle):
    embedder = _embedder(tmp_path, lifecycle)
    _loaded(embedder)
    with patch("src.plugin_api.media.get_container", return_value=_world(embedder)):
        before = media.image_embedder()
        _put_weights(embedder)
        present = media.image_embedder()
        media.embed_images([Image.new("RGB", (8, 8))])
        loaded = media.image_embedder()

    assert before == {"model": MODEL, "slug": "local-google-siglip-base-patch16-224", "device": "cpu", "available": False, "loaded": False}
    assert present == {**before, "available": True}
    assert loaded == {**before, "available": True, "loaded": True}


def test_embedding_images_goes_through_the_application_embedder_and_its_model_lifecycle(tmp_path, lifecycle):
    embedder = _embedder(tmp_path, lifecycle)
    processor = _loaded(embedder)
    _put_weights(embedder)
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(first)
    Image.new("RGB", (8, 8), (30, 20, 10)).save(second)

    with patch("src.plugin_api.media.get_container", return_value=_world(embedder)):
        vectors = media.embed_images((str(first), second))

    assert vectors == [pytest.approx([0.0, 0.6, 0.8, 0.0])] * 2
    assert [image.size for image in processor.call_args.kwargs["images"]] == [(8, 8), (8, 8)]
    assert lifecycle.is_cached(embedder._cache_key())
    assert lifecycle.leased_values() == []


def test_embedding_never_downloads_weights_that_are_not_here_even_when_the_gallery_may(tmp_path, lifecycle):
    downloads = Downloads()
    embedder = _embedder(tmp_path, lifecycle, downloads=downloads, auto_download=True)

    with patch("src.plugin_api.media.get_container", return_value=_world(embedder)):
        with pytest.raises(FileNotFoundError) as raised:
            media.embed_images([Image.new("RGB", (8, 8))])

    assert downloads.calls == []
    assert not embedder.local_dir_for(MODEL, embedder.resolver).exists()
    assert str(tmp_path) not in str(raised.value)
    assert not lifecycle.is_cached(embedder._cache_key())


def test_embedding_from_inside_a_generation_leaves_that_generations_lease_and_preset_alone(tmp_path, lifecycle):
    embedder = _embedder(tmp_path, lifecycle)
    _loaded(embedder)
    _put_weights(embedder)
    weights = Weights()

    def generation():
        lifecycle.begin_generation("preset-a")
        lifecycle.begin_lease("generation-1")
        try:
            with patch("src.plugin_api.media.get_container", return_value=_world(embedder)):
                media.embed_images([Image.new("RGB", (8, 8))])
            lease = lifecycle_module._active_lease_id.get()
            owner = lifecycle_module._cache_owner.get()
            lifecycle.acquire("native/dit/a", "a", lambda: weights)
            held = weights in lifecycle.leased_values()
        finally:
            lifecycle.end_lease("generation-1")
        return lease, owner, held

    assert contextvars.copy_context().run(generation) == ("generation-1", "preset-a", True)


def test_no_images_need_no_weights_loaded(tmp_path, lifecycle):
    embedder = _embedder(tmp_path, lifecycle)
    _loaded(embedder)
    _put_weights(embedder)

    with patch("src.plugin_api.media.get_container", return_value=_world(embedder)):
        assert media.embed_images([]) == []

    embedder._load_model.assert_not_called()


def test_the_names_are_listed_by_the_media_module_and_the_root():
    assert {"embed_images", "image_embedder"} <= set(media.__all__)
    assert root.embed_images is media.embed_images
    assert root.image_embedder is media.image_embedder
