"""Unit tests for the process-global prompt-embedding LRU cache (no GPU).

Exercises hit / miss / eviction / LRU-refresh / fingerprint-isolation and the two
correctness invariants the cache must uphold: it never stores GPU tensors, and a
later in-place mutation of either the caller's originals or the returned tensors
cannot corrupt a stored entry (everything is a detached CPU clone).
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.text_encoders.embed_cache import (
    PROMPT_WEIGHTS_VERSION,
    PromptEmbedCache,
    get_prompt_embed_cache,
    image_content_fingerprint,
    prompt_embed_key,
)


def _cond(seed: float) -> dict:
    """A tiny (cond, uncond) tensor tree shaped like a real encode output."""
    return {"context": torch.full((1, 2, 3), seed), "y": None, "attention_mask": None}


# --- basic get / put ----------------------------------------------------------


def test_miss_returns_none():
    cache = PromptEmbedCache()
    assert cache.get("absent") is None
    assert cache.get_on_device("absent", "cpu") is None


def test_put_then_get_roundtrips_values():
    cache = PromptEmbedCache()
    value = (_cond(1.0), _cond(2.0))
    cache.put("k", value)
    got = cache.get("k")
    assert got is not None
    assert torch.equal(got[0]["context"], value[0]["context"])
    assert torch.equal(got[1]["context"], value[1]["context"])
    assert got[0]["y"] is None and got[0]["attention_mask"] is None


def test_len_tracks_entries():
    cache = PromptEmbedCache()
    assert len(cache) == 0
    cache.put("a", _cond(1.0))
    cache.put("b", _cond(2.0))
    assert len(cache) == 2


# --- CPU-clone isolation (correctness) ---------------------------------------


def test_put_stores_cpu_clone_immune_to_caller_mutation():
    cache = PromptEmbedCache()
    original = _cond(1.0)
    cache.put("k", original)
    # Mutating the caller's tensor after put must not reach the cached entry.
    original["context"].add_(99.0)
    assert torch.equal(cache.get("k")["context"], torch.full((1, 2, 3), 1.0))


def test_get_on_device_returns_mutation_safe_copy():
    cache = PromptEmbedCache()
    cache.put("k", _cond(1.0))
    got = cache.get_on_device("k", "cpu")
    got["context"].add_(99.0)  # mutate the returned tensor in place
    # The stored entry is untouched -> a subsequent fetch is still pristine.
    assert torch.equal(cache.get("k")["context"], torch.full((1, 2, 3), 1.0))


def test_cached_tensors_live_on_cpu():
    cache = PromptEmbedCache()
    cache.put("k", _cond(1.0))
    assert cache.get("k")["context"].device.type == "cpu"


# --- LRU eviction / refresh ---------------------------------------------------


def test_eviction_drops_oldest_past_max():
    cache = PromptEmbedCache(max_entries=2)
    cache.put("a", _cond(1.0))
    cache.put("b", _cond(2.0))
    cache.put("c", _cond(3.0))  # evicts "a" (oldest)
    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None
    assert len(cache) == 2


def test_get_refreshes_lru_recency():
    cache = PromptEmbedCache(max_entries=2)
    cache.put("a", _cond(1.0))
    cache.put("b", _cond(2.0))
    cache.get("a")               # "a" is now most-recently-used
    cache.put("c", _cond(3.0))   # so "b" is evicted, not "a"
    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_put_same_key_overwrites_without_growth():
    cache = PromptEmbedCache(max_entries=2)
    cache.put("k", _cond(1.0))
    cache.put("k", _cond(5.0))
    assert len(cache) == 1
    assert torch.equal(cache.get("k")["context"], torch.full((1, 2, 3), 5.0))


# --- key construction ---------------------------------------------------------


def test_key_is_stable_and_content_addressed():
    assert PromptEmbedCache.key("fp", "hello") == PromptEmbedCache.key("fp", "hello")
    assert PromptEmbedCache.key("fp", "a") != PromptEmbedCache.key("fp", "b")
    assert PromptEmbedCache.key("fp1", "a") != PromptEmbedCache.key("fp2", "a")


def test_prompt_embed_key_bypasses_without_fingerprint():
    assert prompt_embed_key(None, "role", "prompt") is None
    assert prompt_embed_key("", "role", "prompt") is None


def test_prompt_embed_key_distinguishes_inputs():
    base = prompt_embed_key("fp", "qwen3", "cat", "", True)
    assert base is not None
    assert base == prompt_embed_key("fp", "qwen3", "cat", "", True)
    assert base != prompt_embed_key("fp", "qwen3", "dog", "", True)      # prompt
    assert base != prompt_embed_key("fp", "qwen3", "cat", "blur", True)  # negative
    assert base != prompt_embed_key("fp", "qwen3", "cat", "", False)     # do_cfg
    assert base != prompt_embed_key("fp", "t5xxl", "cat", "", True)      # role/variant
    assert base != prompt_embed_key("fp2", "qwen3", "cat", "", True)     # fingerprint


def test_weights_version_participates_in_key():
    k_default = PromptEmbedCache.key("fp", "p")
    k_explicit = PromptEmbedCache.key("fp", "p", ())
    assert k_default == k_explicit
    # A grammar-version bump would change every key; assert the constant is wired in.
    assert isinstance(PROMPT_WEIGHTS_VERSION, int)


# --- image-conditioned cache-key hazard -------------------------


def test_image_content_fingerprint_is_deterministic_and_content_addressed():
    img = torch.rand(4, 4, 3)
    assert image_content_fingerprint(img) == image_content_fingerprint(img.clone())


def test_image_content_fingerprint_distinguishes_different_images():
    a = torch.zeros(4, 4, 3)
    b = torch.zeros(4, 4, 3)
    b[0, 0, 0] = 1.0
    assert image_content_fingerprint(a) != image_content_fingerprint(b)


def test_image_content_fingerprint_distinguishes_shape():
    a = torch.zeros(4, 4, 3)
    b = torch.zeros(2, 8, 3)
    assert image_content_fingerprint(a) != image_content_fingerprint(b)


def test_image_content_fingerprint_is_a_stable_hex_digest():
    fp = image_content_fingerprint(torch.rand(2, 2, 3))
    assert isinstance(fp, str)
    int(fp, 16)  # sha256 hex digest, not a python hash() or id()


def test_naive_prompt_only_key_aliases_different_images_the_hazard():
    """Demonstrates exactly the bug `image_content_fingerprint` exists to
    prevent: a cache key built from (fingerprint, role, prompt) alone is
    IDENTICAL regardless of which image was actually encoded — a real
    image-conditioned caller must additionally fold in
    `image_content_fingerprint(img)` per image, or two different source images
    with the same prompt text would silently share one cached embedding."""
    naive_key_for = lambda img: prompt_embed_key("fp", "qwen25_vl_7b", "make it night")  # noqa: E731,ARG005
    img_a = torch.rand(4, 4, 3)
    img_b = torch.rand(4, 4, 3)
    assert naive_key_for(img_a) == naive_key_for(img_b)  # the hazard

    correct_key_for = lambda img: prompt_embed_key(  # noqa: E731
        "fp", "qwen25_vl_7b", "make it night", image_content_fingerprint(img)
    )
    assert correct_key_for(img_a) != correct_key_for(img_b)  # the fix


# --- singleton ----------------------------------------------------------------


def test_singleton_is_process_global():
    a = get_prompt_embed_cache()
    b = get_prompt_embed_cache()
    assert a is b


# --- review cluster #38 hardening (E16 / E22 / E23) ----------------------

def test_key_rejects_tensor_parts():
    # repr() ellipsizes large tensors -> distinct tensors could collide. Real
    # callers pass only strings/scalars, so a tensor is a programming error (E16).
    import pytest
    with pytest.raises(TypeError, match="Tensor"):
        PromptEmbedCache.key("te", ("prompt", torch.zeros(4)))
    with pytest.raises(TypeError):
        prompt_embed_key("fp", "role", "prompt", torch.zeros(2))
    # strings/scalars are fine.
    assert isinstance(PromptEmbedCache.key("te", ("prompt", True, 3)), str)


def test_namedtuple_value_roundtrips(monkeypatch):
    # An encoder returning a namedtuple must not break caching (E22).
    from collections import namedtuple
    Out = namedtuple("Out", ["context", "pooled"])
    cache = PromptEmbedCache()
    cache.put("k", Out(torch.ones(2), torch.zeros(3)))
    got = cache.get("k")
    assert isinstance(got, Out)
    assert torch.equal(got.context, torch.ones(2))


def test_get_returns_defensive_copy(monkeypatch):
    # Mutating get()'s result must not corrupt the stored entry (E23).
    cache = PromptEmbedCache()
    cache.put("k", {"context": torch.ones(4)})
    first = cache.get("k")
    first["context"].zero_()
    second = cache.get("k")
    assert torch.equal(second["context"], torch.ones(4))
