"""Tests for image_encode_cache: memoizing create_base64_image
across the temporary-preview -> final-emission duplicate encode."""

import gc

import pytest
from PIL import Image

from src.features.generation.image_encode_cache import get_or_encode
from src.features.generation.media_utils import create_base64_image


class TestGetOrEncode:
    def test_second_call_reuses_cached_value(self):
        image = Image.new('RGB', (32, 32), color='red')
        calls = []

        def encode_fn(img, max_dimension):
            calls.append((img, max_dimension))
            return f"encoded-{max_dimension}"

        first = get_or_encode(image, 768, encode_fn)
        second = get_or_encode(image, 768, encode_fn)

        assert first == "encoded-768"
        assert second == "encoded-768"
        assert len(calls) == 1

    def test_different_images_are_encoded_independently(self):
        image_a = Image.new('RGB', (32, 32), color='red')
        image_b = Image.new('RGB', (32, 32), color='blue')
        calls = []

        def encode_fn(img, max_dimension):
            calls.append(img)
            return "encoded"

        get_or_encode(image_a, 768, encode_fn)
        get_or_encode(image_b, 768, encode_fn)

        assert len(calls) == 2

    def test_different_max_dimension_is_a_separate_cache_entry(self):
        image = Image.new('RGB', (32, 32), color='red')
        calls = []

        def encode_fn(img, max_dimension):
            calls.append(max_dimension)
            return f"encoded-{max_dimension}"

        result_768 = get_or_encode(image, 768, encode_fn)
        result_256 = get_or_encode(image, 256, encode_fn)
        result_768_again = get_or_encode(image, 768, encode_fn)

        assert result_768 == "encoded-768"
        assert result_256 == "encoded-256"
        assert result_768_again == "encoded-768"
        assert calls == [768, 256]

    def test_entry_evicted_once_image_is_garbage_collected(self):
        """Guards the ABA hazard: a stale cache entry must not survive the
        image it was computed for, since a later unrelated object could
        legally reuse the same freed id()."""
        image = Image.new('RGB', (32, 32), color='red')
        calls = []

        def encode_fn(img, max_dimension):
            calls.append(1)
            return "encoded"

        get_or_encode(image, 768, encode_fn)
        del image
        gc.collect()

        # Internal caches must have been cleaned up by the weakref callback.
        from src.features.generation import image_encode_cache as cache_module
        assert len(cache_module._by_dimension) == 0
        assert len(cache_module._refs) == 0

    def test_encode_fn_result_is_returned_even_when_none(self):
        image = Image.new('RGB', (32, 32), color='red')

        result = get_or_encode(image, 768, lambda img, dim: None)

        assert result is None


class TestCreateBase64ImageMemoization:
    def test_repeat_encode_of_same_image_hits_cache(self, monkeypatch):
        """Simulates the real duplicate: a temporary preview emission followed
        by a final, non-temporary emission of the same PIL Image object."""
        image = Image.new('RGB', (100, 100), color='green')

        from src.features.generation import media_utils

        call_count = {"n": 0}
        original = media_utils._encode_base64_image

        def counting_encode(img, max_dimension):
            call_count["n"] += 1
            return original(img, max_dimension)

        monkeypatch.setattr(media_utils, "_encode_base64_image", counting_encode)

        preview_b64 = create_base64_image(image, max_dimension=768)
        final_b64 = create_base64_image(image, max_dimension=768)

        assert preview_b64 == final_b64
        assert call_count["n"] == 1

    def test_transformed_image_is_not_a_cache_hit(self, monkeypatch):
        """A downstream pipe that produces a genuinely different image object
        must not reuse another image's cached encode."""
        image_a = Image.new('RGB', (100, 100), color='red')
        image_b = Image.new('RGB', (100, 100), color='blue')

        from src.features.generation import media_utils

        call_count = {"n": 0}
        original = media_utils._encode_base64_image

        def counting_encode(img, max_dimension):
            call_count["n"] += 1
            return original(img, max_dimension)

        monkeypatch.setattr(media_utils, "_encode_base64_image", counting_encode)

        create_base64_image(image_a, max_dimension=768)
        create_base64_image(image_b, max_dimension=768)

        assert call_count["n"] == 2
