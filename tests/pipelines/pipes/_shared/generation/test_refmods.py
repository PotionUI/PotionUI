"""Tests for RefMod bundle loading (`_shared.generation.refmods`), ported
from the community ComfyUI-MiniMaxH3Mod project's file format: v5 `members`
metadata, the v4 single-member fallback, per-entry strength (dropping
`w <= 0`, mixing otherwise), and the blur-based strength mix itself.
CPU-only, no weights -- every bundle is a tiny tensor built and saved with
`safetensors.torch.save_file` in `tmp_path`."""

from __future__ import annotations

import json

import pytest
import torch
from safetensors.torch import save_file

from src.pipelines.pipes._shared.generation.refmods import (
    AUDIO_CHANNELS,
    VIDEO_LATENT_CHANNELS,
    RefMod,
    apply_strength,
    load_refmods,
)


def _visual_tensor(t: int = 1, h: int = 4, w: int = 4) -> torch.Tensor:
    return torch.arange(1 * VIDEO_LATENT_CHANNELS * t * h * w, dtype=torch.float32).reshape(
        1, VIDEO_LATENT_CHANNELS, t, h, w
    )


def _audio_tensor(latent_channels: int = 32, t: int = 6) -> torch.Tensor:
    return torch.arange(1 * latent_channels * AUDIO_CHANNELS * t, dtype=torch.float32).reshape(
        1, latent_channels, AUDIO_CHANNELS, t
    )


def _write_bundle(path, tensors: dict, metadata: dict) -> str:
    file_path = str(path)
    save_file(tensors, file_path, metadata={"refmod_meta": json.dumps(metadata)})
    return file_path


# -- apply_strength -----------------------------------------------------------


def test_apply_strength_w1_is_bit_exact_identity():
    z = _visual_tensor()
    got = apply_strength(z, 1.0)
    torch.testing.assert_close(got, z, rtol=0, atol=0)
    assert got is z


def test_apply_strength_w_above_1_is_also_identity():
    # A caller passing a stray >1 strength must not amplify past the source.
    z = _visual_tensor()
    assert apply_strength(z, 1.5) is z


def test_apply_strength_half_differs_from_the_source():
    torch.manual_seed(0)
    z = torch.rand(1, VIDEO_LATENT_CHANNELS, 1, 8, 8)
    got = apply_strength(z, 0.5)
    assert not torch.allclose(got, z)
    assert got.shape == z.shape


def test_apply_strength_visual_blurs_spatially_not_temporally():
    # Two different frames, each spatially UNIFORM: pooling+upsampling a
    # spatially-uniform frame is a no-op on IT, so the blend must reproduce
    # each frame's own value exactly, even though the two frames differ.
    z = torch.zeros(1, VIDEO_LATENT_CHANNELS, 2, 4, 4)
    z[:, :, 0] = 1.0
    z[:, :, 1] = 5.0
    got = apply_strength(z, 0.0)  # pure blur -- exposes the blur term alone
    torch.testing.assert_close(got[:, :, 0], torch.full((1, VIDEO_LATENT_CHANNELS, 4, 4), 1.0))
    torch.testing.assert_close(got[:, :, 1], torch.full((1, VIDEO_LATENT_CHANNELS, 4, 4), 5.0))


def test_apply_strength_audio_shape_preserved_and_differs():
    torch.manual_seed(1)
    z = torch.rand(AUDIO_CHANNELS, 32, 10)
    got = apply_strength(z, 0.5)
    assert got.shape == z.shape
    assert not torch.allclose(got, z)


def test_apply_strength_rejects_an_unsupported_shape():
    with pytest.raises(ValueError, match="unsupported latent shape"):
        apply_strength(torch.zeros(4, 4), 0.5)


# -- load_refmods: v5 (members) -----------------------------------------------


def test_v5_bundle_loads_one_refmod_per_member(tmp_path):
    image_z = _visual_tensor()
    audio_z = _audio_tensor()
    members = [{"kind": "image", "name": "face"}, {"kind": "audio", "name": "voice"}]
    path = _write_bundle(
        tmp_path / "bundle.safetensors",
        {"ref_0": image_z, "ref_1": audio_z},
        {"_format_version": "5", "kind": "bundle", "name": "character", "members": members},
    )
    mods = load_refmods([{"file_path": path, "strength": 1.0}])
    assert [m.kind for m in mods] == ["image", "audio"]
    assert [m.name for m in mods] == ["face", "voice"]
    assert all(m.source == path for m in mods)
    torch.testing.assert_close(mods[0].latent, image_z, rtol=0, atol=0)
    # audio reshaped (1, latent_channels, channels, T) -> (channels, latent_channels, T)
    assert mods[1].latent.shape == (AUDIO_CHANNELS, 32, 6)
    torch.testing.assert_close(mods[1].latent, audio_z.squeeze(0).permute(1, 0, 2))


def test_v5_bundle_video_member_keeps_its_own_t_h_w(tmp_path):
    video_z = _visual_tensor(t=3, h=8, w=6)
    path = _write_bundle(
        tmp_path / "video.safetensors",
        {"ref_0": video_z},
        {"_format_version": "5", "kind": "bundle", "name": "clip", "members": [{"kind": "video", "name": "walk"}]},
    )
    mods = load_refmods([{"file_path": path, "strength": 1.0}])
    assert len(mods) == 1
    assert mods[0].kind == "video"
    assert mods[0].latent.shape == video_z.shape


# -- load_refmods: v4 (single member, no 'members' key) -----------------------


def test_v4_bundle_falls_back_to_top_level_kind_and_ref_0(tmp_path):
    z = _visual_tensor()
    path = _write_bundle(
        tmp_path / "legacy.safetensors", {"latent": z}, {"kind": "image", "name": "legacy face"},
    )
    mods = load_refmods([{"file_path": path, "strength": 1.0}])
    assert len(mods) == 1
    assert mods[0].kind == "image"
    assert mods[0].name == "legacy face"
    torch.testing.assert_close(mods[0].latent, z, rtol=0, atol=0)


def test_v4_bundle_with_a_non_standard_tensor_name_uses_the_only_tensor(tmp_path):
    z = _visual_tensor()
    path = _write_bundle(tmp_path / "legacy2.safetensors", {"latent": z}, {"kind": "video", "name": "old"})
    mods = load_refmods([{"file_path": path, "strength": 1.0}])
    assert len(mods) == 1
    torch.testing.assert_close(mods[0].latent, z, rtol=0, atol=0)


def test_file_without_refmod_metadata_is_a_clear_error(tmp_path):
    path = str(tmp_path / "bad.safetensors")
    save_file({"ref_0": _visual_tensor()}, path, metadata={"format": "pt"})
    with pytest.raises(ValueError, match="not a RefMod file"):
        load_refmods([{"file_path": path, "strength": 1.0}])


# -- strength: drop / mix / passthrough ---------------------------------------


def test_strength_zero_or_negative_drops_the_entry(tmp_path):
    path = _write_bundle(
        tmp_path / "bundle.safetensors", {"ref_0": _visual_tensor()},
        {"kind": "bundle", "name": "b", "members": [{"kind": "image", "name": "x"}]},
    )
    assert load_refmods([{"file_path": path, "strength": 0.0}]) == []
    assert load_refmods([{"file_path": path, "strength": -0.5}]) == []


def test_strength_is_applied_to_every_member(tmp_path):
    z = _visual_tensor()
    path = _write_bundle(
        tmp_path / "bundle.safetensors", {"ref_0": z},
        {"kind": "bundle", "name": "b", "members": [{"kind": "image", "name": "x"}]},
    )
    mods = load_refmods([{"file_path": path, "strength": 0.4}])
    torch.testing.assert_close(mods[0].latent, apply_strength(z, 0.4), rtol=0, atol=0)
    assert mods[0].strength == 0.4


def test_multiple_entries_load_in_file_then_member_order(tmp_path):
    z1 = _visual_tensor()
    z2 = _audio_tensor()
    path_a = _write_bundle(
        tmp_path / "a.safetensors", {"ref_0": z1},
        {"kind": "bundle", "name": "a", "members": [{"kind": "image", "name": "a-img"}]},
    )
    path_b = _write_bundle(
        tmp_path / "b.safetensors", {"ref_0": z1, "ref_1": z2},
        {
            "kind": "bundle", "name": "b",
            "members": [{"kind": "video", "name": "b-vid"}, {"kind": "audio", "name": "b-aud"}],
        },
    )
    mods = load_refmods([
        {"file_path": path_a, "strength": 1.0}, {"file_path": path_b, "strength": 1.0},
    ])
    assert [m.name for m in mods] == ["a-img", "b-vid", "b-aud"]
    assert [m.kind for m in mods] == ["image", "video", "audio"]


def test_load_refmods_of_an_empty_list_is_empty():
    assert load_refmods([]) == []
    assert load_refmods(None) == []


# -- shape validation ----------------------------------------------------------


def test_visual_member_with_wrong_channel_count_is_a_clear_error(tmp_path):
    bad = torch.zeros(1, 4, 1, 4, 4)  # not VIDEO_LATENT_CHANNELS=24
    path = _write_bundle(
        tmp_path / "bad.safetensors", {"ref_0": bad},
        {"kind": "bundle", "name": "b", "members": [{"kind": "image", "name": "x"}]},
    )
    with pytest.raises(ValueError, match="24"):
        load_refmods([{"file_path": path, "strength": 1.0}])


def test_audio_member_with_wrong_channel_axis_is_a_clear_error(tmp_path):
    bad = torch.zeros(1, 32, 3, 6)  # axis 2 must be AUDIO_CHANNELS=2
    path = _write_bundle(
        tmp_path / "bad.safetensors", {"ref_0": bad},
        {"kind": "bundle", "name": "b", "members": [{"kind": "audio", "name": "x"}]},
    )
    with pytest.raises(ValueError, match="latent_channels"):
        load_refmods([{"file_path": path, "strength": 1.0}])


def test_unknown_member_kind_is_a_clear_error(tmp_path):
    path = _write_bundle(
        tmp_path / "bad.safetensors", {"ref_0": _visual_tensor()},
        {"kind": "bundle", "name": "b", "members": [{"kind": "sculpture", "name": "x"}]},
    )
    with pytest.raises(ValueError, match="sculpture"):
        load_refmods([{"file_path": path, "strength": 1.0}])


def test_missing_tensor_for_a_declared_member_is_a_clear_error(tmp_path):
    path = _write_bundle(
        tmp_path / "bad.safetensors", {"ref_0": _visual_tensor()},
        {
            "kind": "bundle", "name": "b",
            "members": [{"kind": "image", "name": "a"}, {"kind": "image", "name": "b-missing"}],
        },
    )
    with pytest.raises(ValueError, match="ref_1"):
        load_refmods([{"file_path": path, "strength": 1.0}])
