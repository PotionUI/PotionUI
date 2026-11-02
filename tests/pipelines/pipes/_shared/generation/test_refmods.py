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
from src.pipelines.pipes._shared.generation.refmod_header import RefModMember, read_refmod_members


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


def _single_image_bundle(tmp_path, name: str = "b") -> str:
    return _write_bundle(
        tmp_path / f"{name}.safetensors", {"ref_0": _visual_tensor(), "ref_1": _audio_tensor()},
        {
            "kind": "bundle", "name": name,
            "members": [{"kind": "image", "name": f"{name}-img"}, {"kind": "audio", "name": f"{name}-aud"}],
        },
    )


def test_copies_repeat_the_whole_entry_in_member_order(tmp_path):
    path = _single_image_bundle(tmp_path)
    mods = load_refmods([{"file_path": path, "strength": 1.0, "copies": 3}])
    assert [m.name for m in mods] == ["b-img", "b-aud"] * 3
    torch.testing.assert_close(mods[4].latent, mods[0].latent, rtol=0, atol=0)


@pytest.mark.parametrize("copies", [None, "", 1, "1", 1.0])
def test_missing_or_single_copies_load_the_entry_once(tmp_path, copies):
    path = _single_image_bundle(tmp_path)
    assert len(load_refmods([{"file_path": path, "strength": 1.0, "copies": copies}])) == 2


def test_copies_given_as_text_from_a_template_are_read_as_a_count(tmp_path):
    path = _single_image_bundle(tmp_path)
    assert len(load_refmods([{"file_path": path, "strength": 1.0, "copies": "2"}])) == 4


def test_copies_stay_in_their_entry_position(tmp_path):
    first = _single_image_bundle(tmp_path, "a")
    second = _single_image_bundle(tmp_path, "c")
    mods = load_refmods([
        {"file_path": first, "strength": 1.0, "copies": 2},
        {"file_path": second, "strength": 1.0},
    ])
    assert [m.name for m in mods] == ["a-img", "a-aud", "a-img", "a-aud", "c-img", "c-aud"]


def test_copies_carry_the_entry_strength(tmp_path):
    path = _single_image_bundle(tmp_path)
    mods = load_refmods([{"file_path": path, "strength": 0.5, "copies": 2}])
    torch.testing.assert_close(mods[2].latent, apply_strength(_visual_tensor(), 0.5), rtol=0, atol=0)
    assert {m.strength for m in mods} == {0.5}


def test_a_dropped_entry_contributes_no_copies(tmp_path):
    path = _single_image_bundle(tmp_path)
    assert load_refmods([{"file_path": path, "strength": 0.0, "copies": 4}]) == []


@pytest.mark.parametrize("copies", [0, -1, 1.5, "two", 13, 1e9])
def test_invalid_copies_are_a_clear_error(tmp_path, copies):
    path = _single_image_bundle(tmp_path)
    with pytest.raises(ValueError, match="copies"):
        load_refmods([{"file_path": path, "strength": 1.0, "copies": copies}])


def test_copies_count_toward_the_per_kind_reference_limit(tmp_path):
    from src.pipelines.pipes._shared.generation.reference_order import pack_references
    from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
        MAX_IMAGE_REFERENCES,
        ReferenceMedia,
        validate_references,
    )

    path = _write_bundle(
        tmp_path / "img.safetensors", {"ref_0": _visual_tensor()},
        {"kind": "bundle", "name": "img", "members": [{"kind": "image", "name": "img"}]},
    )
    mods = load_refmods([{"file_path": path, "strength": 1.0, "copies": MAX_IMAGE_REFERENCES + 1}])
    packed = pack_references([], [], [], mods=mods)
    assert len(packed) == MAX_IMAGE_REFERENCES + 1
    references = [ReferenceMedia(kind="image", latent=mod.latent) for mod in mods]
    with pytest.raises(ValueError, match=f"at most {MAX_IMAGE_REFERENCES} image references"):
        validate_references(references)


def test_the_copies_cap_is_the_h3_total_reference_limit():
    from src.pipelines.pipes._shared.generation.refmods import MAX_COPIES, MAX_PACKED_REFERENCES
    from src.pipelines.pipes.generator.video_minimax_h3.conditioning import MAX_REFERENCES

    assert MAX_COPIES == MAX_PACKED_REFERENCES == MAX_REFERENCES


def test_copies_past_the_remaining_budget_are_refused_before_they_are_expanded(tmp_path, monkeypatch):
    from src.pipelines.pipes._shared.generation import refmods

    path = _single_image_bundle(tmp_path)
    expanded = []
    real_list = list

    class Members(real_list):
        def __mul__(self, count):
            expanded.append(count)
            return real_list.__mul__(self, count)

    real_load = refmods._load_bundle
    monkeypatch.setattr(refmods, "_load_bundle", lambda *a: Members(real_load(*a)))
    with pytest.raises(ValueError, match=r"2 member\(s\) x 7 copies is more than the 12 reference\(s\) left"):
        load_refmods([{"file_path": path, "strength": 1.0, "copies": 7}])
    assert expanded == []
    with pytest.raises(ValueError, match="left of the 12 allowed"):
        load_refmods([
            {"file_path": path, "strength": 1.0, "copies": 5},
            {"file_path": path, "strength": 1.0, "copies": 2},
        ])
    assert len(load_refmods([{"file_path": path, "strength": 1.0, "copies": 6}])) == 12


@pytest.mark.parametrize("copies", [13, 1e9, "1000000000"])
def test_copies_above_the_cap_are_refused_before_the_file_is_read(tmp_path, monkeypatch, copies):
    from src.pipelines.pipes._shared.generation import refmods

    def never(*args):
        raise AssertionError("the file was read")

    monkeypatch.setattr(refmods, "_load_bundle", never)
    with pytest.raises(ValueError, match="copies must be a whole number from 1 to 12"):
        load_refmods([{"file_path": str(tmp_path / "x.safetensors"), "strength": 1.0, "copies": copies}])


def test_read_members_lists_every_member_kind_and_name_from_the_header(tmp_path):
    path = _single_image_bundle(tmp_path, "hdr")
    assert read_refmod_members(path) == (
        RefModMember(kind="image", name="hdr-img"), RefModMember(kind="audio", name="hdr-aud"),
    )


def test_read_members_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    path = _single_image_bundle(tmp_path, "cached")
    import src.pipelines.pipes._shared.generation.refmod_header as refmod_header

    opened = []
    real_reader = refmod_header.read_safetensors_header
    monkeypatch.setattr(refmod_header, "read_safetensors_header", lambda p: opened.append(p) or real_reader(p))
    read_refmod_members(path)
    read_refmod_members(path)
    assert len(opened) == 1
    _write_bundle(tmp_path / "cached.safetensors", {"latent": _visual_tensor(), "pad": _visual_tensor(h=2)}, {"kind": "video"})
    assert read_refmod_members(path) == (RefModMember(kind="video", name=None),)
    assert len(opened) == 2


def test_read_members_of_a_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="cannot be read"):
        read_refmod_members(str(tmp_path / "gone.safetensors"))


def test_read_members_of_a_file_that_is_not_safetensors_is_a_clear_error(tmp_path):
    path = tmp_path / "junk.safetensors"
    path.write_bytes(b"not a safetensors file")
    with pytest.raises(ValueError, match="not a readable safetensors file"):
        read_refmod_members(str(path))


def test_read_members_of_a_non_refmod_file_is_a_clear_error(tmp_path):
    path = str(tmp_path / "plain.safetensors")
    save_file({"w": torch.zeros(2)}, path)
    with pytest.raises(ValueError, match="not a RefMod file"):
        read_refmod_members(path)


def test_read_members_rejects_an_unknown_member_kind(tmp_path):
    path = _write_bundle(tmp_path / "odd.safetensors", {"latent": _visual_tensor()}, {"kind": "depth"})
    with pytest.raises(ValueError, match="'depth'"):
        read_refmod_members(path)


@pytest.mark.parametrize("meta, tensors, kinds", [
    (
        {"kind": "image", "name": "face", "latent_t": 1, "source": "image", "mode": "encode", "pool": "full-res 64x64px"},
        {"latent": (1, 24, 1, 4, 4)}, ["image"],
    ),
    (
        {"kind": "video", "name": "stack", "latent_t": 3, "source": "stack", "tags": "3 img, 0 vid", "mode": "encode"},
        {"latent": (1, 24, 3, 4, 4)}, ["video"],
    ),
    (
        {"_format_version": 5, "kind": "bundle", "name": "cast", "members": [
            {"_format_version": 4, "kind": "image", "name": "hero", "source": "image"},
            {"_format_version": 4, "kind": "image", "name": "villain", "source": "image"},
        ]},
        {"ref_0": (1, 24, 1, 4, 4), "ref_1": (1, 24, 1, 2, 4)}, ["image", "image"],
    ),
], ids=["one-picture", "stacked-pictures", "picture-bundle"])
def test_upstream_picture_mods_read_with_the_kind_their_header_declares(tmp_path, meta, tensors, kinds):
    path = _write_bundle(tmp_path / f"{meta['name']}.safetensors", {k: torch.zeros(v) for k, v in tensors.items()}, meta)
    assert [member.kind for member in read_refmod_members(path)] == kinds
    assert [mod.kind for mod in load_refmods([{"file_path": path, "strength": 1.0}])] == kinds
