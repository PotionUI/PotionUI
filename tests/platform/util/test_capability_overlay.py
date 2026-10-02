from src.platform.util.path_resolution import apply_preset_mode_overlay, merge_capability_overlay

BASE = {
    "segment_routing": True,
    "modes": {"t2v": {}, "director": {"keyframes": "first_only", "max_segments": 6}},
    "limits": {"default_duration": 5, "max_duration": 10},
}


def test_a_null_composition_mode_removes_that_mode():
    merged = merge_capability_overlay(BASE, {"modes": {"t2v": None}})

    assert set(merged["modes"]) == {"director"}


def test_a_composition_mode_entry_merges_key_by_key_and_null_values_stay():
    merged = merge_capability_overlay(BASE, {"modes": {"director": {"keyframes": None, "max_frames_per_segment": 96}}})

    assert merged["modes"]["director"] == {"keyframes": None, "max_segments": 6, "max_frames_per_segment": 96}


def test_top_level_keys_replace_outright():
    merged = merge_capability_overlay(BASE, {"limits": {"durations": [4, 8]}})

    assert merged["limits"] == {"durations": [4, 8]} and merged["segment_routing"] is True


def test_an_overlay_that_may_not_add_modes_only_tunes_or_removes_them():
    merged = merge_capability_overlay(BASE, {"modes": {"flf": {}, "t2v": None, "director": {"max_segments": 2}}}, add_modes=False)

    assert merged["modes"] == {"director": {"keyframes": "first_only", "max_segments": 2}}


def test_a_preset_mode_override_can_still_add_and_remove_modes():
    capabilities = {**BASE, "preset_mode_overrides": {"img2video": {"modes": {"t2v": None, "i2v": {}, "flf": {}}}}}

    merged = apply_preset_mode_overlay(capabilities, "img2video")

    assert set(merged["modes"]) == {"director", "i2v", "flf"}
    assert "preset_mode_overrides" not in merged
    assert set(apply_preset_mode_overlay(capabilities, "txt2video")["modes"]) == {"t2v", "director"}


def test_no_overlay_returns_an_unchanged_copy():
    merged = merge_capability_overlay(BASE, None)

    assert merged == BASE and merged is not BASE and merged["modes"] is not BASE["modes"]
