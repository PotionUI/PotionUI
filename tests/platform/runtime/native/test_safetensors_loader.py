"""Tests for the safetensors loader entry point."""

from __future__ import annotations

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.io.safetensors_loader import (
    load_torch_file,
    load_torch_file_prefixed,
    load_torch_file_stripped,
)


def test_load_safetensors_roundtrip(tmp_path):
    sd = {"a.weight": torch.arange(6, dtype=torch.float32).reshape(2, 3),
          "b.bias": torch.ones(3)}
    path = tmp_path / "m.safetensors"
    save_file(sd, str(path), metadata={"format": "pt", "hello": "world"})

    loaded, meta = load_torch_file(path)
    assert set(loaded) == {"a.weight", "b.bias"}
    assert torch.equal(loaded["a.weight"], sd["a.weight"])
    assert meta["hello"] == "world"


def test_sft_extension_supported(tmp_path):
    path = tmp_path / "m.sft"
    save_file({"w": torch.zeros(2)}, str(path))
    loaded, _ = load_torch_file(path)
    assert "w" in loaded


def test_gguf_rejected(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"\x00")
    with pytest.raises(NativeEngineUnsupportedError, match="GGUF"):
        load_torch_file(path)


def test_pickle_rejected(tmp_path):
    path = tmp_path / "m.ckpt"
    path.write_bytes(b"\x00")
    with pytest.raises(NativeEngineUnsupportedError):
        load_torch_file(path)


def test_missing_file(tmp_path):
    with pytest.raises(NativeEngineUnsupportedError, match="not found"):
        load_torch_file(tmp_path / "nope.safetensors")


# --- load_torch_file_prefixed --------------------------------------------------


def _all_in_one_checkpoint(tmp_path):
    """An LTX-shaped all-in-one file: a big DiT slice plus small VAE/audio_vae/
    vocoder slices, so a prefixed read can be checked against a counting wrapper
    that would fail if the DiT tensors were ever materialized."""
    sd = {
        "model.diffusion_model.blocks.0.weight": torch.zeros(4096, 4096),  # the "big" DiT
        "vae.encoder.conv.weight": torch.ones(3, 3),
        "vae.decoder.conv.weight": torch.ones(3, 3) * 2,
        "audio_vae.encoder.weight": torch.ones(2, 2) * 3,
        "vocoder.head.weight": torch.ones(2, 2) * 4,
    }
    path = tmp_path / "ltx_all_in_one.safetensors"
    save_file(sd, str(path), metadata={"format": "pt", "hello": "world"})
    return path, sd


def test_prefixed_returns_only_matching_keys_with_prefix_intact(tmp_path):
    path, sd = _all_in_one_checkpoint(tmp_path)
    loaded, meta = load_torch_file_prefixed(path, "vae.")
    assert set(loaded) == {"vae.encoder.conv.weight", "vae.decoder.conv.weight"}
    assert torch.equal(loaded["vae.encoder.conv.weight"], sd["vae.encoder.conv.weight"])
    assert meta["hello"] == "world"


def test_prefixed_does_not_materialize_non_matching_keys(tmp_path, monkeypatch):
    path, _sd = _all_in_one_checkpoint(tmp_path)

    from safetensors import safe_open as real_safe_open
    read_keys = []

    class _CountingHandle:
        def __init__(self, handle):
            self._handle = handle

        def keys(self):
            return self._handle.keys()

        def get_tensor(self, key):
            read_keys.append(key)
            return self._handle.get_tensor(key)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self._handle.__exit__(*a)

    def _wrapped_safe_open(path, framework="pt", device="cpu"):
        return _CountingHandle(real_safe_open(path, framework=framework, device=device))

    monkeypatch.setattr(
        "src.platform.runtime.native.io.safetensors_loader.safe_open", _wrapped_safe_open
    )
    load_torch_file_prefixed(path, "audio_vae.")
    assert read_keys == ["audio_vae.encoder.weight"]  # never touched the DiT/vae/vocoder tensors


def test_prefixed_falls_back_to_full_read_when_prefix_absent(tmp_path):
    sd = {"encoder.conv.weight": torch.ones(2, 2), "decoder.conv.weight": torch.ones(2, 2) * 5}
    path = tmp_path / "standalone_vae.safetensors"
    save_file(sd, str(path))
    loaded, _meta = load_torch_file_prefixed(path, "vae.")
    assert set(loaded) == set(sd)
    assert torch.equal(loaded["encoder.conv.weight"], sd["encoder.conv.weight"])


def test_prefixed_gguf_rejected(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"\x00")
    with pytest.raises(NativeEngineUnsupportedError, match="GGUF"):
        load_torch_file_prefixed(path, "vae.")


def test_prefixed_missing_file(tmp_path):
    with pytest.raises(NativeEngineUnsupportedError, match="not found"):
        load_torch_file_prefixed(tmp_path / "nope.safetensors", "vae.")


def _all_in_one_dit(tmp_path, prefix="model.diffusion_model."):
    sd = {
        f"{prefix}blocks.0.weight": torch.full((2, 2), 1.0),
        f"{prefix}final.weight": torch.full((2, 2), 2.0),
        "vae.decoder.conv.weight": torch.full((3, 3), 3.0),
        "text_encoders.t5xxl.weight": torch.full((3, 3), 4.0),
    }
    path = tmp_path / "all_in_one.safetensors"
    save_file(sd, str(path), metadata={"format": "pt", "hello": "world"})
    return path, sd


def _counting_safe_open(monkeypatch):
    from safetensors import safe_open as real_safe_open

    read_keys = []

    class _CountingHandle:
        def __init__(self, handle):
            self._handle = handle

        def keys(self):
            return self._handle.keys()

        def get_tensor(self, key):
            read_keys.append(key)
            return self._handle.get_tensor(key)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self._handle.__exit__(*a)

    monkeypatch.setattr(
        "src.platform.runtime.native.io.safetensors_loader.safe_open",
        lambda path, framework="pt", device="cpu": _CountingHandle(real_safe_open(path, framework=framework, device=device)),
    )
    return read_keys


@pytest.mark.parametrize("prefix", ["model.diffusion_model.", "diffusion_model."])
def test_stripped_keeps_only_the_denoiser_slice_without_its_prefix(tmp_path, prefix):
    path, sd = _all_in_one_dit(tmp_path, prefix)
    loaded, meta = load_torch_file_stripped(path)
    assert set(loaded) == {"blocks.0.weight", "final.weight"}
    assert torch.equal(loaded["final.weight"], sd[f"{prefix}final.weight"])
    assert meta["hello"] == "world"


def test_stripped_strips_net_from_a_file_where_every_key_carries_it(tmp_path):
    sd = {"net.blocks.0.weight": torch.ones(2, 2), "net.final.weight": torch.ones(2, 2)}
    path = tmp_path / "net_only.safetensors"
    save_file(sd, str(path))
    loaded, _meta = load_torch_file_stripped(path)
    assert set(loaded) == {"blocks.0.weight", "final.weight"}


def test_stripped_never_reads_the_vae_or_text_encoder_tensors(tmp_path, monkeypatch):
    path, _sd = _all_in_one_dit(tmp_path)
    read_keys = _counting_safe_open(monkeypatch)
    load_torch_file_stripped(path)
    assert sorted(read_keys) == ["model.diffusion_model.blocks.0.weight", "model.diffusion_model.final.weight"]


def test_stripped_reads_a_bare_keyed_file_whole(tmp_path):
    sd = {"blocks.0.weight": torch.ones(2, 2), "final.weight": torch.ones(2, 2) * 5}
    path = tmp_path / "bare.safetensors"
    save_file(sd, str(path))
    loaded, _meta = load_torch_file_stripped(path)
    assert set(loaded) == set(sd)


def test_stripped_picks_the_dominant_prefix(tmp_path):
    sd = {
        "model.diffusion_model.a": torch.ones(1),
        "model.diffusion_model.b": torch.ones(1),
        "net.stray": torch.ones(1),
    }
    path = tmp_path / "mixed.safetensors"
    save_file(sd, str(path))
    loaded, _meta = load_torch_file_stripped(path)
    assert set(loaded) == {"a", "b"}


def test_stripped_accepts_net_only_when_every_key_carries_it(tmp_path):
    sd = {"blocks.0.w": torch.ones(1), "blocks.1.w": torch.ones(1), "net.stray": torch.ones(1)}
    path = tmp_path / "few_net.safetensors"
    save_file(sd, str(path))
    loaded, _meta = load_torch_file_stripped(path)
    assert set(loaded) == set(sd)


def test_stripped_gguf_rejected(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"\x00")
    with pytest.raises(NativeEngineUnsupportedError, match="GGUF"):
        load_torch_file_stripped(path)


def test_stripped_missing_file(tmp_path):
    with pytest.raises(NativeEngineUnsupportedError, match="not found"):
        load_torch_file_stripped(tmp_path / "nope.safetensors")
