"""Tests for `model_loader/ltx`'s projection.py: 19b vs 2.3 key-set branch selection.

The fixtures use TINY tensor shapes on purpose — the loader's logic (key-set
branch selection, bias handling, dtype/device cast) is shape-agnostic, and the
real projections are ~3GB each (3840x188160 fp32), far too big for test files.
"""

from __future__ import annotations

import pytest
import safetensors.torch as st
import torch

from src.pipelines.pipes.model_loader.ltx.projection import load_projection


def _write(tmp_path, tensors, name="dit.safetensors"):
    path = tmp_path / name
    st.save_file(tensors, str(path))
    return str(path)


def test_19b_shared_bias_less_projection(tmp_path):
    path = _write(tmp_path, {
        "text_embedding_projection.aggregate_embed.weight": torch.zeros(8, 24),
        "model.diffusion_model.some_other_weight": torch.zeros(4),
    })
    out = load_projection(path, "cpu", torch.float32)
    assert set(out.keys()) == {"video_projection_weight"}
    assert out["video_projection_weight"].shape == (8, 24)


def test_23_dual_biased_projection(tmp_path):
    path = _write(tmp_path, {
        "text_embedding_projection.video_aggregate_embed.weight": torch.zeros(8, 24),
        "text_embedding_projection.video_aggregate_embed.bias": torch.zeros(8),
        "text_embedding_projection.audio_aggregate_embed.weight": torch.zeros(4, 24),
        "text_embedding_projection.audio_aggregate_embed.bias": torch.zeros(4),
    })
    out = load_projection(path, "cpu", torch.float32)
    assert set(out.keys()) == {
        "video_projection_weight", "video_projection_bias",
        "audio_projection_weight", "audio_projection_bias",
    }
    assert out["video_projection_weight"].shape == (8, 24)
    assert out["audio_projection_weight"].shape == (4, 24)


def test_missing_projection_raises_keyerror(tmp_path):
    path = _write(tmp_path, {"model.diffusion_model.some_weight": torch.zeros(4)})
    with pytest.raises(KeyError):
        load_projection(path, "cpu", torch.float32)


def test_dtype_and_device_cast(tmp_path):
    path = _write(tmp_path, {
        "text_embedding_projection.aggregate_embed.weight": torch.zeros(8, 24, dtype=torch.float32),
    })
    out = load_projection(path, "cpu", torch.bfloat16)
    assert out["video_projection_weight"].dtype == torch.bfloat16


def test_25_falls_back_to_te_file_when_dit_has_no_projection(tmp_path):
    # LTX-2.5: the projection moved off the DiT and onto the Gemma4 TE file.
    dit_path = _write(tmp_path, {"model.diffusion_model.some_weight": torch.zeros(4)}, name="dit.safetensors")
    te_path = _write(tmp_path, {
        "model.embed_tokens.weight": torch.zeros(4, 4),
        "text_embedding_projection.video_aggregate_embed.weight": torch.zeros(8, 24),
        "text_embedding_projection.video_aggregate_embed.bias": torch.zeros(8),
        "text_embedding_projection.audio_aggregate_embed.weight": torch.zeros(4, 24),
        "text_embedding_projection.audio_aggregate_embed.bias": torch.zeros(4),
    }, name="te.safetensors")

    out = load_projection(dit_path, "cpu", torch.float32, te_path=te_path)
    assert set(out.keys()) == {
        "video_projection_weight", "video_projection_bias",
        "audio_projection_weight", "audio_projection_bias",
    }
    assert out["video_projection_weight"].shape == (8, 24)


def test_dit_projection_preferred_over_te_when_both_present(tmp_path):
    dit_path = _write(tmp_path, {
        "text_embedding_projection.aggregate_embed.weight": torch.ones(8, 24),
    }, name="dit.safetensors")
    te_path = _write(tmp_path, {
        "text_embedding_projection.video_aggregate_embed.weight": torch.zeros(8, 24),
        "text_embedding_projection.audio_aggregate_embed.weight": torch.zeros(4, 24),
    }, name="te.safetensors")

    out = load_projection(dit_path, "cpu", torch.float32, te_path=te_path)
    assert set(out.keys()) == {"video_projection_weight"}
    assert torch.equal(out["video_projection_weight"], torch.ones(8, 24))


def test_missing_projection_in_both_dit_and_te_raises_keyerror(tmp_path):
    dit_path = _write(tmp_path, {"model.diffusion_model.some_weight": torch.zeros(4)}, name="dit.safetensors")
    te_path = _write(tmp_path, {"model.embed_tokens.weight": torch.zeros(4, 4)}, name="te.safetensors")
    with pytest.raises(KeyError):
        load_projection(dit_path, "cpu", torch.float32, te_path=te_path)
