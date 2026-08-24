import copy

import pytest

from src.features.video_director.normalize import (
    VideoDirectorValidationError,
    apply_preset_mode_overlay,
    derive_ltx_media_fields,
    normalize_video_director,
)

_LIMITS = {"default_duration": 5, "default_fps": 24, "max_duration": 30}

# The `director` mode is capability-shaped: LTX runs it as a single keyframe/
# audio timeline (no segment_routing); Wan runs it as a routed multi-segment
# chain (segment_routing: true). The two shapes never coexist in one preset, so
# the fixtures mirror the two real presets rather than one conflated cap dict.
LTX_CAPS = {
    "preset_modes": ["video"],
    "modes": {
        "t2v": {},
        "i2v": {},
        "flf": {},
        "director": {"audio": True, "ic_lora": True, "max_keyframes": 8},
    },
    "limits": _LIMITS,
}

WAN_CAPS = {
    "preset_modes": ["video"],
    "segment_routing": True,
    "modes": {
        "t2v": {},
        "i2v": {},
        "flf": {},
        "director": {
            "per_segment_loras": True,
            "keyframes": "first_only",
            "max_segments": 8,
            "max_frames_per_segment": 81,
        },
    },
    "limits": _LIMITS,
}

# A hypothetical chain-style preset that declares NEITHER a single-shot i2v/flf
# mode NOR any keyframe placement capability -- none of leadingEdgeAllowed's/
# trailingEdgeAllowed's grants apply (docs/video-director.md "media"), so
# 'first'/'last' edge-role media should be rejected outright rather than
# riding through ungated.
MINIMAL_CHAIN_CAPS = {
    "preset_modes": ["video"],
    "segment_routing": True,
    "modes": {
        "director": {"max_segments": 8, "max_frames_per_segment": 81},
    },
    "limits": _LIMITS,
}


@pytest.fixture
def storage_dir(tmp_path):
    (tmp_path / "image.png").write_bytes(b"fake-image")
    (tmp_path / "audio.wav").write_bytes(b"fake-audio")
    (tmp_path / "clip.mp4").write_bytes(b"fake-video")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "ref.png").write_bytes(b"fake-ref")
    return tmp_path


def _base_doc(mode, **overrides):
    doc = {
        "schema_version": 1,
        "mode": mode,
        "settings": {"fps": 24, "duration": 5.0, "resolution": "", "seed": -1},
        "segments": [{"id": "seg-1", "prompt": "a cat", "negative_prompt": ""}],
        "media": [],
        "audio": [],
        "ic_lora": [],
    }
    doc.update(overrides)
    return doc


def test_t2v_happy_path(storage_dir):
    doc = _base_doc("t2v")
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert out["mode"] == "t2v"
    assert out["media"] == []
    assert isinstance(out["settings"]["seed"], int)
    assert out["settings"]["seed"] >= 0


def test_i2v_happy_path(storage_dir):
    doc = _base_doc(
        "i2v",
        media=[{
            "id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0,
            "strength": 1.0, "media": {"relative_path": "image.png"},
        }],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert len(out["media"]) == 1
    assert out["media"][0]["media"]["path"] == str(storage_dir / "image.png")


def test_i2v_upload_shaped_path_falls_back_to_relative_path(storage_dir):
    # An upload's raw `path` is rooted at the process CWD (e.g.
    # "storage/uploads/x.png"), so joined onto the storage root it
    # double-prefixes and misses; `relative_path` is the resolvable key.
    doc = _base_doc(
        "i2v",
        media=[{
            "id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0,
            "strength": 1.0,
            "media": {"path": "storage/uploads/image.png", "relative_path": "image.png"},
        }],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert out["media"][0]["media"]["path"] == str(storage_dir / "image.png")


def test_i2v_wrong_role_rejected(storage_dir):
    doc = _base_doc(
        "i2v",
        media=[{"id": "m-1", "role": "last", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "i2v" in str(excinfo.value)
    assert "'first'" in str(excinfo.value)


def test_flf_happy_path(storage_dir):
    doc = _base_doc(
        "flf",
        media=[
            {"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
             "media": {"relative_path": "image.png"}},
            {"id": "m-2", "role": "last", "segment_id": "seg-1", "at": 5.0, "strength": 1.0,
             "media": {"relative_path": "image.png"}},
        ],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert len(out["media"]) == 2


def test_director_timeline_happy_path(storage_dir):
    # LTX's timeline director: start/end segments, keyframes-at-positions, audio,
    # ic_lora -- driven by the absence of segment_routing.
    doc = _base_doc(
        "director",
        segments=[
            {"id": "seg-1", "prompt": "a", "start": 0.0, "end": 2.0},
            {"id": "seg-2", "prompt": "b", "start": 2.0, "end": 5.0},
        ],
        media=[{"id": "kf-1", "role": "keyframe", "segment_id": None, "at": 1.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
        audio=[{"id": "a-1", "start": 0.0, "trim_start": 0.0, "length": 5.0,
                "media": {"relative_path": "audio.wav"}}],
        ic_lora=[{"id": "ic-1", "lora": {"model": "some-lora", "strength": 1.0},
                  "reference": {"relative_path": "nested/ref.png"}, "strength": 1.0}],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert [s["id"] for s in out["segments"]] == ["seg-1", "seg-2"]
    assert len(out["audio"]) == 1
    assert len(out["ic_lora"]) == 1
    assert out["ic_lora"][0]["reference"]["path"] == str(storage_dir / "nested" / "ref.png")


def test_director_chain_happy_path(storage_dir):
    # Wan's routed chain director: per-segment frames/loras, continuation, a
    # first-only leading keyframe -- driven by segment_routing.
    doc = _base_doc(
        "director",
        settings={"fps": 24, "duration": None, "resolution": "", "seed": -1,
                   "continuation": {"source": "tail_frames", "overlap_frames": 4, "stitch": True}},
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 81, "seed": None, "steps": 30, "cfg": 5.0,
             "loras": {"high": [{"model": "lora-a", "strength": 1.0}], "low": []}},
            {"id": "seg-2", "prompt": "b", "frames": 49, "seed": None, "steps": None, "cfg": None,
             "loras": None},
        ],
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert out["mode"] == "director"
    assert out["segments"][0]["frames"] == 81
    assert out["segments"][0]["loras"]["high"][0]["model"] == "lora-a"
    assert out["settings"]["continuation"]["overlap_frames"] == 4


def test_single_segment_director_chain_is_a_degenerate_chain(storage_dir):
    # "Long videos right away" still starts at one shot: a single-segment routed
    # director doc validates and routes as a t2v opener (chain of one).
    doc = _base_doc(
        "director",
        settings={"fps": 24, "duration": None, "resolution": "", "seed": -1, "continuation": None},
        segments=[{"id": "seg-1", "prompt": "a", "frames": 81}],
        media=[],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert [s["sub_type"] for s in out["segments"]] == ["t2v"]
    assert (out["needs_t2v_set"], out["needs_i2v_set"]) == (True, False)


def test_all_errors_collected(storage_dir):
    doc = _base_doc("t2v", segments=[])
    doc["schema_version"] = 2
    doc["settings"]["fps"] = 999
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    errors = excinfo.value.errors
    assert len(errors) >= 3
    joined = str(excinfo.value)
    assert "schema_version" in joined
    assert "fps" in joined
    assert "segments" in joined


def test_schema_version_2_rejected(storage_dir):
    doc = _base_doc("t2v")
    doc["schema_version"] = 2
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "schema_version" in str(excinfo.value)


def test_mode_rejected_when_not_capable(storage_dir):
    # A preset that doesn't declare `director` at all rejects a director doc --
    # and, having no routed director mode, doesn't leniently absorb it.
    caps = {"preset_modes": ["video"], "modes": {"t2v": {}}, "limits": _LIMITS}
    doc = _base_doc("director", segments=[{"id": "seg-1", "prompt": "a"}])
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, caps, str(storage_dir))
    assert "unsupported mode" in str(excinfo.value)
    assert "director" in str(excinfo.value)


def test_lenient_reads_legacy_chain_mode_as_director(storage_dir):
    # A stored pre-director document (the retired Wan `chain` mode) re-submitted
    # against a routed director preset is read as the director mode and emits the
    # new mode string -- old generations re-run without a client migration.
    doc = _base_doc(
        "chain",
        settings={"fps": 24, "duration": None, "resolution": "", "seed": -1, "continuation": None},
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 40},
            {"id": "seg-2", "prompt": "b", "frames": 40},
        ],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert out["mode"] == "director"
    assert [s["sub_type"] for s in out["segments"]] == ["t2v", "chain"]


def test_legacy_chain_mode_not_remapped_without_routing(storage_dir):
    # Without a routed director mode there is nothing to leniently absorb into --
    # a legacy `chain` doc is simply an unsupported mode.
    doc = _base_doc("chain", segments=[{"id": "seg-1", "prompt": "a"}])
    with pytest.raises(VideoDirectorValidationError):
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))


def test_audio_without_capability_rejected(storage_dir):
    caps = copy.deepcopy(LTX_CAPS)
    caps["modes"]["director"]["audio"] = False
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        audio=[{"id": "a-1", "start": 0.0, "trim_start": 0.0, "length": 5.0,
                "media": {"relative_path": "audio.wav"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, caps, str(storage_dir))
    assert "audio" in str(excinfo.value)


def test_ic_lora_without_capability_rejected(storage_dir):
    caps = copy.deepcopy(LTX_CAPS)
    caps["modes"]["director"]["ic_lora"] = False
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        ic_lora=[{"id": "ic-1", "lora": {"model": "m", "strength": 1.0}, "reference": None, "strength": 1.0}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, caps, str(storage_dir))
    assert "ic_lora" in str(excinfo.value)


def test_per_segment_loras_without_capability_rejected(storage_dir):
    caps = copy.deepcopy(WAN_CAPS)
    caps["modes"]["director"]["per_segment_loras"] = False
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "frames": 40,
                   "loras": {"high": [{"model": "m", "strength": 1.0}]}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, caps, str(storage_dir))
    assert "loras" in str(excinfo.value)


def test_director_overlap_detected_and_sorted(storage_dir):
    doc = _base_doc(
        "director",
        segments=[
            {"id": "seg-2", "prompt": "b", "start": 1.5, "end": 5.0},
            {"id": "seg-1", "prompt": "a", "start": 0.0, "end": 2.0},
        ],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "overlaps" in str(excinfo.value)


def test_director_touching_edges_ok(storage_dir):
    doc = _base_doc(
        "director",
        segments=[
            {"id": "seg-1", "prompt": "a", "start": 0.0, "end": 2.0},
            {"id": "seg-2", "prompt": "b", "start": 2.0, "end": 5.0},
        ],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert [s["id"] for s in out["segments"]] == ["seg-1", "seg-2"]


def test_flf_wrong_media_count_rejected(storage_dir):
    doc = _base_doc(
        "flf",
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "flf" in str(excinfo.value)


def test_flf_wrong_roles_rejected(storage_dir):
    doc = _base_doc(
        "flf",
        media=[
            {"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
             "media": {"relative_path": "image.png"}},
            {"id": "m-2", "role": "keyframe", "segment_id": "seg-1", "at": 1.0, "strength": 1.0,
             "media": {"relative_path": "image.png"}},
        ],
    )
    with pytest.raises(VideoDirectorValidationError):
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))


def test_director_chain_first_only_keyframe_rule(storage_dir):
    # Join-aware, not index-pinned: 'first_only' means "no free-floating
    # keyframe timeline" (that's the 'anywhere' capability), never "segment 0
    # only" -- a non-first segment carrying its own start image resolves to a
    # fresh 'i2v' opener (derive_segment_sub_type), so it's legal here. A
    # post-cut shot IS a first frame in its own generation.
    doc = _base_doc(
        "director",
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 40},
            {"id": "seg-2", "prompt": "b", "frames": 40},
        ],
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-2", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert [s["sub_type"] for s in out["segments"]] == ["t2v", "i2v"]


def test_director_chain_first_role_rejected_when_segment_explicitly_continues(storage_dir):
    # The one way 'first' media on a non-first segment IS a dead knob: an
    # EXPLICIT `sub_type: "chain"` override forces continuation regardless of
    # attached media (derive_segment_sub_type: override always wins), so the
    # start image would never be read by the generator. Rejected outright
    # rather than silently ignored.
    doc = _base_doc(
        "director",
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 40},
            {"id": "seg-2", "prompt": "b", "frames": 40, "sub_type": "chain"},
        ],
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-2", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert "seg-2" in str(excinfo.value)
    assert "'first'" in str(excinfo.value)


def test_director_chain_last_role_rejected_without_a_paired_first_on_the_same_segment(storage_dir):
    # A trailing-only edge is a dead knob: the generator only ever reads
    # `end_frames` on the 'flf' sub-type, which requires a leading image on
    # the SAME segment (generator/chain_video_wan22/main.py). Wan declares
    # `flf` at the document level (trailing_edge_allowed), but that alone
    # doesn't make an unpaired 'last' meaningful -- rejected rather than
    # silently dropped.
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "frames": 40}],
        media=[{"id": "m-1", "role": "last", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert "seg-1" in str(excinfo.value)
    assert "'flf'" in str(excinfo.value)


def test_director_chain_last_role_allowed_when_paired_with_first_on_the_same_segment(storage_dir):
    # Paired first+last on one segment resolves to 'flf' -- honoured by the
    # generator regardless of the segment's position in the chain (a mid-chain
    # flf shot works the same as segment 0; see generator/chain_video_wan22).
    doc = _base_doc(
        "director",
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 40},
            {"id": "seg-2", "prompt": "b", "frames": 40},
        ],
        media=[
            {"id": "m-1", "role": "first", "segment_id": "seg-2", "at": 0.0, "strength": 1.0,
             "media": {"relative_path": "image.png"}},
            {"id": "m-2", "role": "last", "segment_id": "seg-2", "at": 1.0, "strength": 1.0,
             "media": {"relative_path": "image.png"}},
        ],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert [s["sub_type"] for s in out["segments"]] == ["t2v", "flf"]
    assert {m["role"] for m in out["media"]} == {"first", "last"}


def test_director_chain_edge_roles_rejected_without_any_capability_grant(storage_dir):
    # No i2v/flf single-shot mode and no keyframe placement capability at all --
    # neither leadingEdgeAllowed nor trailingEdgeAllowed has a grant, so both
    # edge roles are illegal, not silently accepted.
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "frames": 40}],
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, MINIMAL_CHAIN_CAPS, str(storage_dir))
    assert "'first' media" in str(excinfo.value)

    doc["media"][0]["role"] = "last"
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, MINIMAL_CHAIN_CAPS, str(storage_dir))
    assert "'last' media" in str(excinfo.value)


def test_director_timeline_edge_roles_always_allowed(storage_dir):
    # Timeline style is director-mode-by-definition (mode == "director" and no
    # segment_routing), which makes freePlacementAllowed unconditionally true --
    # so 'first'/'last' placements on a timeline director are never gated by
    # i2v/flf declaration, unlike chain style.
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        media=[
            {"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
             "media": {"relative_path": "image.png"}},
            {"id": "m-2", "role": "last", "segment_id": "seg-1", "at": 5.0, "strength": 1.0,
             "media": {"relative_path": "image.png"}},
        ],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert {m["role"] for m in out["media"]} == {"first", "last"}


def test_fps_bounds(storage_dir):
    doc = _base_doc("t2v")
    doc["settings"]["fps"] = 200
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "fps" in str(excinfo.value)


def test_fps_bound_matches_generator_cap_of_60(storage_dir):
    # generator/video_ltx + generator/txt2vid_ltx (and every Wan video
    # generator) cap PipeConfigSpec("fps", ...) at 60 -- 90 used to pass the
    # old (1, 120) Director range and only fail once it reached the pipe.
    doc = _base_doc("t2v")
    doc["settings"]["fps"] = 90
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "fps" in str(excinfo.value)


def test_duration_fps_combination_exceeding_generator_frame_cap_rejected(storage_dir):
    # generator/video_ltx + generator/txt2vid_ltx cap `frames` at 1001; LTX's
    # own preset declares limits.max_frames=1001 to opt into this check.
    caps = copy.deepcopy(LTX_CAPS)
    caps["limits"]["max_frames"] = 1001
    doc = _base_doc("t2v", settings={"fps": 60, "duration": 30, "resolution": "", "seed": -1})
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, caps, str(storage_dir))
    assert "1001" in str(excinfo.value)
    assert "frames" in str(excinfo.value)


def test_duration_fps_within_frame_cap_reports_snapped_frame_count(storage_dir):
    caps = copy.deepcopy(LTX_CAPS)
    caps["limits"]["max_frames"] = 1001
    doc = _base_doc("t2v", settings={"fps": 24, "duration": 5.0, "resolution": "", "seed": -1})
    out = normalize_video_director(doc, caps, str(storage_dir))
    # round(5 * 24) == 120, snapped to the nearest 1 + k*8 -> 121.
    assert out["settings"]["frame_count"] == 121
    assert out["settings"]["effective_duration"] == pytest.approx(121 / 24)


def test_frame_cap_not_enforced_without_capability(storage_dir):
    # A preset that doesn't declare limits.max_frames (e.g. Wan today) gets no
    # frame-count check and no frame_count/effective_duration fields -- this
    # is what keeps the change a no-op for every preset besides LTX.
    doc = _base_doc("t2v", settings={"fps": 60, "duration": 30, "resolution": "", "seed": -1})
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "frame_count" not in out["settings"]
    assert "effective_duration" not in out["settings"]


def test_video_typed_keyframe_rejected(storage_dir):
    (storage_dir / "clip.mp4").write_bytes(b"fake-video")
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        media=[{"id": "kf-1", "role": "keyframe", "segment_id": None, "at": 1.0, "strength": 1.0,
                "media": {"relative_path": "clip.mp4", "type": "video"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "keyframe" in str(excinfo.value)
    assert "video" in str(excinfo.value)


def test_image_typed_keyframe_still_accepted(storage_dir):
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        media=[{"id": "kf-1", "role": "keyframe", "segment_id": None, "at": 1.0, "strength": 1.0,
                "media": {"relative_path": "image.png", "type": "image"}}],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert len(out["media"]) == 1


def test_video_typed_first_rejected(storage_dir):
    (storage_dir / "clip.mp4").write_bytes(b"fake-video")
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": None, "strength": 1.0,
                "media": {"relative_path": "clip.mp4", "type": "video"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "first" in str(excinfo.value)
    assert "video" in str(excinfo.value)


def test_video_typed_last_rejected(storage_dir):
    (storage_dir / "clip.mp4").write_bytes(b"fake-video")
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        media=[{"id": "m-1", "role": "last", "segment_id": "seg-1", "at": None, "strength": 1.0,
                "media": {"relative_path": "clip.mp4", "type": "video"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "last" in str(excinfo.value)
    assert "video" in str(excinfo.value)


def test_duration_exceeds_max(storage_dir):
    doc = _base_doc("t2v")
    doc["settings"]["duration"] = 60
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "duration" in str(excinfo.value)


def test_director_chain_frames_exceeds_max_frames_per_segment(storage_dir):
    doc = _base_doc("director", segments=[{"id": "seg-1", "prompt": "a", "frames": 200}])
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert "between 1 and 81" in str(excinfo.value)


def test_director_chain_declared_cap_overrides_the_wan_hard_cap(storage_dir):
    caps = {**WAN_CAPS, "modes": {"director": {**WAN_CAPS["modes"]["director"], "max_frames_per_segment": 345}}}
    doc = _base_doc("director", segments=[{"id": "seg-1", "prompt": "a", "frames": 345}])
    document = normalize_video_director(doc, caps, str(storage_dir))
    assert document["segments"][0]["frames"] == 345


def test_director_chain_steps_cfg_bounds(storage_dir):
    doc = _base_doc("director", segments=[{"id": "seg-1", "prompt": "a", "frames": 40, "steps": 999, "cfg": 999}])
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    errors = excinfo.value.errors
    assert any("steps" in e for e in errors)
    assert any("cfg" in e for e in errors)


def test_path_traversal_rejected(storage_dir):
    doc = _base_doc(
        "i2v",
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "../../etc/passwd"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "escapes" in str(excinfo.value)


# -- segment routing (Wan `segment_routing` capability, director mode) -------


def test_routing_disabled_leaves_document_unchanged(storage_dir):
    # LTX_CAPS has no segment_routing -> no sub_type / needs_* fields added.
    out = normalize_video_director(_base_doc("t2v"), LTX_CAPS, str(storage_dir))
    assert "sub_type" not in out["segments"][0]
    assert "needs_t2v_set" not in out
    assert "needs_i2v_set" not in out


def test_routing_t2v_single_segment(storage_dir):
    out = normalize_video_director(_base_doc("t2v"), WAN_CAPS, str(storage_dir))
    assert out["segments"][0]["sub_type"] == "t2v"
    assert out["needs_t2v_set"] is True
    assert out["needs_i2v_set"] is False


def test_routing_i2v_single_segment(storage_dir):
    doc = _base_doc(
        "i2v",
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert out["segments"][0]["sub_type"] == "i2v"
    assert (out["needs_t2v_set"], out["needs_i2v_set"]) == (False, True)


def test_routing_director_t2v_opener_then_continuation_needs_both_sets(storage_dir):
    doc = _base_doc(
        "director",
        segments=[
            {"id": "seg-1", "prompt": "opener", "frames": 40},
            {"id": "seg-2", "prompt": "more", "frames": 40},
            {"id": "seg-3", "prompt": "end", "frames": 40},
        ],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert [s["sub_type"] for s in out["segments"]] == ["t2v", "chain", "chain"]
    assert (out["needs_t2v_set"], out["needs_i2v_set"]) == (True, True)


def test_routing_director_from_start_image_needs_only_i2v_set(storage_dir):
    doc = _base_doc(
        "director",
        segments=[
            {"id": "seg-1", "prompt": "opener", "frames": 40},
            {"id": "seg-2", "prompt": "more", "frames": 40},
        ],
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert [s["sub_type"] for s in out["segments"]] == ["i2v", "chain"]
    assert (out["needs_t2v_set"], out["needs_i2v_set"]) == (False, True)


def test_routing_per_segment_override_forces_fresh_cut(storage_dir):
    doc = _base_doc(
        "director",
        segments=[
            {"id": "seg-1", "prompt": "opener", "frames": 40},
            {"id": "seg-2", "prompt": "hard cut", "frames": 40, "sub_type": "t2v"},
        ],
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "image.png"}}],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert [s["sub_type"] for s in out["segments"]] == ["i2v", "t2v"]
    assert (out["needs_t2v_set"], out["needs_i2v_set"]) == (True, True)


def test_routing_invalid_override_rejected(storage_dir):
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "frames": 40, "sub_type": "bogus"}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert "sub_type" in str(excinfo.value)


def test_missing_file_rejected(storage_dir):
    doc = _base_doc(
        "i2v",
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"relative_path": "does-not-exist.png"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "does not exist" in str(excinfo.value)


def test_absolute_existing_path_kept(storage_dir):
    absolute = str(storage_dir / "image.png")
    doc = _base_doc(
        "i2v",
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                "media": {"path": absolute}}],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert out["media"][0]["media"]["path"] == absolute


def test_seed_minus_one_replaced_and_input_not_mutated(storage_dir):
    doc = _base_doc("t2v")
    original = copy.deepcopy(doc)
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert isinstance(out["settings"]["seed"], int)
    assert out["settings"]["seed"] >= 0
    assert doc == original


def test_unknown_top_level_key_round_trips(storage_dir):
    doc = _base_doc("t2v")
    doc["client_meta"] = {"editor_version": "1.2.3"}
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert out["client_meta"] == {"editor_version": "1.2.3"}


# -- keyframes "anywhere": a chain-style director placing keyframes ----------


def _wan_anywhere_caps(**director_overrides):
    caps = copy.deepcopy(WAN_CAPS)
    caps["modes"]["director"]["keyframes"] = "anywhere"
    caps["modes"]["director"].update(director_overrides)
    return caps


def test_chain_keyframes_anywhere_admits_keyframe_media(storage_dir):
    # 81 + 49 frames at 16 fps == 8.125s of chain, so a keyframe at 6.0s lands
    # inside a window settings.duration (absent in chain style) can't describe.
    doc = _base_doc(
        "director",
        settings={"fps": 16, "duration": None, "resolution": "", "seed": 7},
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 81},
            {"id": "seg-2", "prompt": "b", "frames": 49},
        ],
        media=[{"id": "kf-1", "role": "keyframe", "segment_id": None, "at": 6.0, "strength": 0.8,
                "media": {"relative_path": "image.png", "type": "image"}}],
    )
    out = normalize_video_director(doc, _wan_anywhere_caps(), str(storage_dir))
    assert [m["role"] for m in out["media"]] == ["keyframe"]
    # derive_ltx_media_fields runs for every mode, so the chain keyframe is
    # already placed on a frame index for the family pipes to consume.
    assert out["media_placements"] == [
        {"source": "image", "index": 0, "frame": 96, "strength": 0.8, "role": "keyframe"},
    ]


def test_chain_keyframes_anywhere_at_beyond_chain_total_rejected(storage_dir):
    doc = _base_doc(
        "director",
        settings={"fps": 16, "duration": None, "resolution": "", "seed": 7},
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 81},
            {"id": "seg-2", "prompt": "b", "frames": 49},
        ],
        media=[{"id": "kf-1", "role": "keyframe", "segment_id": None, "at": 9.0, "strength": 1.0,
                "media": {"relative_path": "image.png", "type": "image"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, _wan_anywhere_caps(), str(storage_dir))
    assert "at must be within [0, the chain's total duration]" in str(excinfo.value)


def test_chain_keyframes_anywhere_respects_max_keyframes(storage_dir):
    doc = _base_doc(
        "director",
        settings={"fps": 16, "duration": None, "resolution": "", "seed": 7},
        segments=[{"id": "seg-1", "prompt": "a", "frames": 81}],
        media=[
            {"id": f"kf-{i}", "role": "keyframe", "segment_id": None, "at": 0.1 * i, "strength": 1.0,
             "media": {"relative_path": "image.png", "type": "image"}}
            for i in range(3)
        ],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, _wan_anywhere_caps(max_keyframes=2), str(storage_dir))
    assert "at most 2 keyframes are allowed, got 3" in str(excinfo.value)


def test_chain_keyframes_rejected_without_the_anywhere_capability(storage_dir):
    # WAN_CAPS declares keyframes: "first_only" -- the pre-existing rejection,
    # message unchanged, is what a preset that never opts in still gets.
    doc = _base_doc(
        "director",
        settings={"fps": 16, "duration": None, "resolution": "", "seed": 7},
        segments=[{"id": "seg-1", "prompt": "a", "frames": 81}],
        media=[{"id": "kf-1", "role": "keyframe", "segment_id": None, "at": 1.0, "strength": 1.0,
                "media": {"relative_path": "image.png", "type": "image"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert "media[0]: keyframe media is only valid in a timeline director mode" in str(excinfo.value)


def test_timeline_keyframe_window_message_unchanged(storage_dir):
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        media=[{"id": "kf-1", "role": "keyframe", "segment_id": None, "at": 9.0, "strength": 1.0,
                "media": {"relative_path": "image.png", "type": "image"}}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "at must be within [0, settings.duration], got 9.0" in str(excinfo.value)


# -- audio: capability-gated in either director style ------------------------


def _audio_entry(**overrides):
    entry = {"id": "a-1", "start": 0.0, "trim_start": 0.0, "length": 5.0,
             "media": {"relative_path": "audio.wav"}}
    entry.update(overrides)
    return entry


def _wan_audio_doc(**audio_overrides):
    return _base_doc(
        "director",
        settings={"fps": 16, "duration": None, "resolution": "", "seed": 7},
        segments=[{"id": "seg-1", "prompt": "a", "frames": 81}],
        audio=[_audio_entry(**audio_overrides)],
    )


def test_chain_style_audio_admitted_with_the_audio_capability(storage_dir):
    caps = copy.deepcopy(WAN_CAPS)
    caps["modes"]["director"]["audio"] = True
    out = normalize_video_director(_wan_audio_doc(), caps, str(storage_dir))
    assert len(out["audio"]) == 1
    assert out["audio"][0]["media"]["path"] == str(storage_dir / "audio.wav")


def test_chain_style_audio_rejected_without_the_audio_capability(storage_dir):
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(_wan_audio_doc(), WAN_CAPS, str(storage_dir))
    assert "audio tracks are only supported in a mode declaring the 'audio' capability" in str(excinfo.value)


def test_audio_role_defaults_to_condition(storage_dir):
    caps = copy.deepcopy(WAN_CAPS)
    caps["modes"]["director"]["audio"] = True
    out = normalize_video_director(_wan_audio_doc(), caps, str(storage_dir))
    assert out["audio"][0]["role"] == "condition"


def test_audio_role_mux_passes_through(storage_dir):
    caps = copy.deepcopy(WAN_CAPS)
    caps["modes"]["director"]["audio"] = True
    out = normalize_video_director(_wan_audio_doc(role="mux"), caps, str(storage_dir))
    assert out["audio"][0]["role"] == "mux"


def test_audio_role_rejects_unknown_value(storage_dir):
    caps = copy.deepcopy(WAN_CAPS)
    caps["modes"]["director"]["audio"] = True
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(_wan_audio_doc(role="soundtrack"), caps, str(storage_dir))
    assert "audio[0]: role must be one of ['condition', 'mux'], got 'soundtrack'" in str(excinfo.value)


def test_ic_lora_stays_timeline_only(storage_dir):
    # Only the audio gate lost its timeline half; a chain-style preset that
    # declares ic_lora still can't submit one.
    caps = copy.deepcopy(WAN_CAPS)
    caps["modes"]["director"]["ic_lora"] = True
    doc = _base_doc(
        "director",
        settings={"fps": 16, "duration": None, "resolution": "", "seed": 7},
        segments=[{"id": "seg-1", "prompt": "a", "frames": 81}],
        ic_lora=[{"id": "ic-1", "lora": {"model": "m", "strength": 1.0}, "reference": None, "strength": 1.0}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, caps, str(storage_dir))
    assert "ic_lora is only supported in a timeline director mode" in str(excinfo.value)


# -- continuation.overlap_frames bound (max_overlap_frames capability) -------


def _continuation_doc(overlap_frames):
    return _base_doc(
        "director",
        settings={"fps": 16, "duration": None, "resolution": "", "seed": 7,
                  "continuation": {"source": "tail_frames", "overlap_frames": overlap_frames, "stitch": True}},
        segments=[{"id": "seg-1", "prompt": "a", "frames": 81}],
    )


def test_overlap_frames_within_declared_maximum_accepted(storage_dir):
    caps = copy.deepcopy(WAN_CAPS)
    caps["modes"]["director"]["max_overlap_frames"] = 8
    out = normalize_video_director(_continuation_doc(8), caps, str(storage_dir))
    assert out["settings"]["continuation"]["overlap_frames"] == 8


def test_overlap_frames_exceeding_declared_maximum_rejected(storage_dir):
    caps = copy.deepcopy(WAN_CAPS)
    caps["modes"]["director"]["max_overlap_frames"] = 8
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(_continuation_doc(12), caps, str(storage_dir))
    message = str(excinfo.value)
    assert "overlap_frames 12" in message
    assert "max_overlap_frames 8" in message


def test_overlap_frames_unbounded_without_the_capability(storage_dir):
    # No preset declares max_overlap_frames today, so an unbounded overlap must
    # keep validating exactly as it did.
    out = normalize_video_director(_continuation_doc(999), WAN_CAPS, str(storage_dir))
    assert out["settings"]["continuation"]["overlap_frames"] == 999


# -- regression: pinned canonical output for the two shipping presets --------
# Both expectations were captured by running the normalizer BEFORE the
# keyframe/audio/overlap capability split, so any drift in an untouched code
# path shows up here rather than in a preset render.


def test_wan_chain_document_output_pinned(storage_dir):
    doc = {
        "schema_version": 1, "mode": "director",
        "settings": {"fps": 16, "duration": None, "resolution": "832x480", "seed": 1234,
                     "continuation": {"source": "tail_frames", "overlap_frames": 4, "stitch": True}},
        "segments": [
            {"id": "seg-1", "prompt": "a", "negative_prompt": "n", "frames": 81, "seed": None,
             "steps": 30, "cfg": 5.0,
             "loras": {"high": [{"model": "lora-a", "strength": 1.0}], "low": []}},
            {"id": "seg-2", "prompt": "b", "negative_prompt": "", "frames": 49},
        ],
        "media": [{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                   "media": {"relative_path": "image.png", "type": "image"}}],
        "audio": [], "ic_lora": [], "client_meta": {"editor_version": "1.2.3"},
    }
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert out == {
        "client_meta": {"editor_version": "1.2.3"},
        "schema_version": 1,
        "mode": "director",
        "settings": {
            "fps": 16, "duration": None, "resolution": "832x480", "seed": 1234,
            "continuation": {"source": "tail_frames", "overlap_frames": 4, "stitch": True},
        },
        "segments": [
            {"id": "seg-1", "prompt": "a", "negative_prompt": "n", "start": None, "end": None,
             "frames": 81, "seed": None, "steps": 30, "cfg": 5.0,
             "loras": {"high": [{"model": "lora-a", "strength": 1.0}], "low": []},
             "sub_type": "i2v", "references": None, "reference_indices": None},
            {"id": "seg-2", "prompt": "b", "negative_prompt": "", "start": None, "end": None,
             "frames": 49, "seed": None, "steps": None, "cfg": None, "loras": None,
             "sub_type": "chain", "references": None, "reference_indices": None},
        ],
        "media": [{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0.0, "strength": 1.0,
                   "media": {"relative_path": "image.png", "type": "image",
                             "path": str(storage_dir / "image.png")}}],
        "audio": [],
        "ic_lora": [],
        "needs_t2v_set": False,
        "needs_i2v_set": True,
        "media_images": [str(storage_dir / "image.png")],
        "media_videos": [],
        "media_placements": [
            {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe"},
        ],
    }


def test_ltx_timeline_document_output_pinned(storage_dir):
    # The ONLY difference from the pre-split output is the additive
    # `audio[].role` default -- every other key is byte-identical.
    doc = {
        "schema_version": 1, "mode": "director",
        "settings": {"fps": 25, "duration": 5.0, "resolution": "1216x704", "seed": 4321},
        "segments": [
            {"id": "seg-1", "prompt": "a", "negative_prompt": "", "start": 0.0, "end": 2.0},
            {"id": "seg-2", "prompt": "b", "negative_prompt": "", "start": 2.0, "end": 5.0},
        ],
        "media": [{"id": "kf-1", "role": "keyframe", "segment_id": None, "at": 1.0, "strength": 0.8,
                   "media": {"relative_path": "image.png", "type": "image"}}],
        "audio": [{"id": "a-1", "start": 0.0, "trim_start": 0.5, "length": 5.0,
                   "media": {"relative_path": "audio.wav", "type": "audio"}}],
        "ic_lora": [{"id": "ic-1", "lora": {"model": "some-lora", "strength": 0.9},
                     "reference": {"relative_path": "nested/ref.png", "type": "image"},
                     "strength": 0.7}],
    }
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert out == {
        "schema_version": 1,
        "mode": "director",
        "settings": {"fps": 25, "duration": 5.0, "resolution": "1216x704", "seed": 4321,
                     "continuation": None},
        "segments": [
            {"id": "seg-1", "prompt": "a", "negative_prompt": "", "start": 0.0, "end": 2.0,
             "frames": None, "seed": None, "steps": None, "cfg": None, "loras": None,
             "references": None, "reference_indices": None},
            {"id": "seg-2", "prompt": "b", "negative_prompt": "", "start": 2.0, "end": 5.0,
             "frames": None, "seed": None, "steps": None, "cfg": None, "loras": None,
             "references": None, "reference_indices": None},
        ],
        "media": [{"id": "kf-1", "role": "keyframe", "segment_id": None, "at": 1.0, "strength": 0.8,
                   "media": {"relative_path": "image.png", "type": "image",
                             "path": str(storage_dir / "image.png")}}],
        "audio": [{"id": "a-1", "role": "condition", "start": 0.0, "trim_start": 0.5, "length": 5.0,
                   "media": {"relative_path": "audio.wav", "type": "audio",
                             "path": str(storage_dir / "audio.wav")}}],
        "ic_lora": [{"id": "ic-1", "lora": {"model": "some-lora", "strength": 0.9},
                     "reference": {"relative_path": "nested/ref.png", "type": "image",
                                   "path": str(storage_dir / "nested" / "ref.png")},
                     "strength": 0.7}],
        "media_images": [str(storage_dir / "image.png"), str(storage_dir / "nested" / "ref.png")],
        "media_videos": [],
        "media_placements": [
            {"source": "image", "index": 0, "frame": 25, "strength": 0.8, "role": "keyframe"},
            {"source": "image", "index": 1, "frame": "first", "strength": 0.7, "role": "reference"},
        ],
    }


# -- derive_ltx_media_fields() -- precomputed media_images/media_placements --
# (feeds content/presets/marketplace/LTX-2/modes/video/pipeline.yml -- see that
# module's docstring for the exact ordering contract.)

def _media_entry(role, path, media_type="image", at=None, strength=1.0):
    return {"id": f"m-{role}-{path}", "role": role, "segment_id": "seg-1", "at": at,
            "strength": strength, "media": {"path": path, "type": media_type}}


def test_derive_media_images_order_first_last_keyframes_sorted_by_at():
    media = [
        _media_entry("first", "/first.png"),
        _media_entry("last", "/last.png", strength=0.9),
        _media_entry("keyframe", "/kf_late.png", at=6.0, strength=0.8),
        _media_entry("keyframe", "/kf_early.png", at=2.0, strength=0.7),
        _media_entry("keyframe", "/kf_video.mp4", media_type="video", at=4.0, strength=0.5),
    ]
    derived = derive_ltx_media_fields(media, [], fps=25)
    assert derived["media_images"] == ["/first.png", "/last.png", "/kf_early.png", "/kf_late.png"]


def test_derive_media_placements_index_alignment_and_frame_rounding():
    media = [
        _media_entry("first", "/first.png"),
        _media_entry("last", "/last.png", strength=0.9),
        _media_entry("keyframe", "/kf_late.png", at=6.0, strength=0.8),
        _media_entry("keyframe", "/kf_early.png", at=2.0, strength=0.7),
        _media_entry("keyframe", "/kf_video.mp4", media_type="video", at=4.0, strength=0.5),
    ]
    ic_lora = [{"id": "ic1", "lora": {"model": "/lora.safetensors", "strength": 0.6},
                "reference": {"path": "/ref.mp4", "type": "video"}, "strength": 0.75}]
    derived = derive_ltx_media_fields(media, ic_lora, fps=25)

    assert derived["media_placements"] == [
        {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe"},
        {"source": "image", "index": 1, "frame": "last", "strength": 0.9, "role": "keyframe"},
        {"source": "image", "index": 2, "frame": 50, "strength": 0.7, "role": "keyframe"},
        {"source": "image", "index": 3, "frame": 150, "strength": 0.8, "role": "keyframe"},
        {"source": "video", "index": 0, "frame": "first", "strength": 0.75, "role": "reference"},
    ]
    assert derived["media_videos"] == ["/ref.mp4"]


def test_derive_media_fields_empty_for_t2v_shape():
    assert derive_ltx_media_fields([], [], fps=24) == {
        "media_images": [], "media_videos": [], "media_placements": [],
    }


def test_derive_media_ic_lora_image_reference_routes_to_media_images():
    # An image-typed ic_lora reference must NOT go through the video loader
    # (cv2-backed `_load_video_frames`, which cannot read a still image) --
    # it routes into media_images/source="image" like any other still.
    ic_lora = [{"id": "ic1", "lora": {"model": "/lora.safetensors", "strength": 0.6},
                "reference": {"path": "/ref.png", "type": "image"}, "strength": 0.8}]
    derived = derive_ltx_media_fields([], ic_lora, fps=25)

    assert derived["media_images"] == ["/ref.png"]
    assert derived["media_videos"] == []
    assert derived["media_placements"] == [
        {"source": "image", "index": 0, "frame": "first", "strength": 0.8, "role": "reference"},
    ]


def test_derive_media_ic_lora_reference_defaults_to_image_when_type_absent():
    # Mirrors _normalize_media's own first/last/keyframe default: a MediaRef
    # with no `type` is image-typed, not video-typed -- MediaLoaderField
    # always stamps an explicit `type` from the detected mime, so an absent
    # `type` here means "not a video", never "assume video".
    ic_lora = [{"id": "ic1", "lora": {"model": "/lora.safetensors", "strength": 0.6},
                "reference": {"path": "/ref.png"}, "strength": 1.0}]
    derived = derive_ltx_media_fields([], ic_lora, fps=25)

    assert derived["media_images"] == ["/ref.png"]
    assert derived["media_videos"] == []


def test_derive_media_mixed_first_frame_image_and_both_reference_types():
    # first-frame image (keyframe) + an image ic_lora reference + a video
    # ic_lora reference, in ic_lora document order video-then-image, to prove
    # index alignment doesn't depend on ic_lora list order: the image
    # reference's media_images index must land AFTER the first-frame image
    # (keyframe images always precede ic_lora image references), and the
    # video reference's media_videos index is independent (its own list).
    media = [_media_entry("first", "/first.png")]
    ic_lora = [
        {"id": "ic-video", "lora": {"model": "/lora1.safetensors", "strength": 1.0},
         "reference": {"path": "/clip.mp4", "type": "video"}, "strength": 0.6},
        {"id": "ic-image", "lora": {"model": "/lora2.safetensors", "strength": 1.0},
         "reference": {"path": "/still.png", "type": "image"}, "strength": 0.9},
    ]
    derived = derive_ltx_media_fields(media, ic_lora, fps=25)

    assert derived["media_images"] == ["/first.png", "/still.png"]
    assert derived["media_videos"] == ["/clip.mp4"]
    assert derived["media_placements"] == [
        {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe"},
        {"source": "video", "index": 0, "frame": "first", "strength": 0.6, "role": "reference"},
        {"source": "image", "index": 1, "frame": "first", "strength": 0.9, "role": "reference"},
    ]


def test_normalize_video_director_attaches_derived_ltx_fields(storage_dir):
    doc = _base_doc(
        "i2v",
        media=[{"id": "m-1", "role": "first", "segment_id": "seg-1", "at": None, "strength": 1.0,
                "media": {"path": str(storage_dir / "image.png")}}],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert out["media_images"] == [str(storage_dir / "image.png")]
    assert out["media_placements"] == [
        {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe"},
    ]


def test_normalize_routes_video_typed_ic_lora_reference_through_media_videos(storage_dir):
    # End-to-end through _resolve_media_ref, which rebuilds the MediaRef as
    # `dict(media_ref)` -- the `type` the frontend stamped has to survive that
    # copy, because _media_ref_is_image() treats a missing `type` as an image
    # and would hand a video clip to the still-image loader.
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        ic_lora=[{"id": "ic-1", "lora": {"model": "some-lora", "strength": 1.0},
                  "reference": {"relative_path": "clip.mp4", "type": "video"}, "strength": 0.7}],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))

    assert out["ic_lora"][0]["reference"]["type"] == "video"
    assert out["media_videos"] == [str(storage_dir / "clip.mp4")]
    assert out["media_images"] == []
    assert out["media_placements"] == [
        {"source": "video", "index": 0, "frame": "first", "strength": 0.7, "role": "reference"},
    ]


def test_normalize_routes_image_typed_ic_lora_reference_through_media_images(storage_dir):
    doc = _base_doc(
        "director",
        segments=[{"id": "seg-1", "prompt": "a", "start": 0.0, "end": 5.0}],
        ic_lora=[{"id": "ic-1", "lora": {"model": "some-lora", "strength": 1.0},
                  "reference": {"relative_path": "nested/ref.png", "type": "image"}, "strength": 0.7}],
    )
    out = normalize_video_director(doc, LTX_CAPS, str(storage_dir))

    assert out["media_videos"] == []
    assert out["media_images"] == [str(storage_dir / "nested" / "ref.png")]
    assert out["media_placements"] == [
        {"source": "image", "index": 0, "frame": "first", "strength": 0.7, "role": "reference"},
    ]


# ---------------------------------------------------------------------------
# apply_preset_mode_overlay -- MiniMax-H3's `video`/`refs` capability split.
# ---------------------------------------------------------------------------

H3_VIDEO_CAPS = {
    "preset_modes": ["video", "refs"],
    "segment_routing": True,
    "modes": {
        "t2v": {},
        "i2v": {},
        "flf": {},
        "director": {
            "keyframes": "anywhere", "audio": True, "max_keyframes": 8,
            "max_segments": 6, "max_frames_per_segment": 345, "max_overlap_frames": 34,
        },
    },
    "limits": {"default_duration": 5, "default_fps": 24, "max_duration": 15},
    "preset_mode_overrides": {
        "refs": {
            "references": "per_shot",
            "reference_fields": ["references", "reference_videos", "reference_audios"],
            "modes": {"director": {"keyframes": None, "audio": False}},
        },
    },
}


def test_overlay_with_no_matching_preset_mode_returns_base_capabilities():
    for preset_mode in (None, "video", "unknown-mode"):
        merged = apply_preset_mode_overlay(H3_VIDEO_CAPS, preset_mode)
        assert merged["modes"]["director"]["keyframes"] == "anywhere"
        assert merged["modes"]["director"]["audio"] is True
        assert "references" not in merged
        assert "preset_mode_overrides" not in merged


def test_overlay_scalar_override_wins():
    merged = apply_preset_mode_overlay(H3_VIDEO_CAPS, "refs")
    assert merged["references"] == "per_shot"
    assert merged["reference_fields"] == ["references", "reference_videos", "reference_audios"]
    # Untouched top-level keys pass through from the base.
    assert merged["segment_routing"] is True
    assert merged["limits"] == H3_VIDEO_CAPS["limits"]


def test_overlay_modes_merge_per_composition_mode_not_whole_dict():
    merged = apply_preset_mode_overlay(H3_VIDEO_CAPS, "refs")
    director = merged["modes"]["director"]
    # The two keys the override touches change...
    assert director["keyframes"] is None
    assert director["audio"] is False
    # ...but everything else on `director` the override didn't mention survives
    # from the base -- this is NOT a whole-dict replace.
    assert director["max_keyframes"] == 8
    assert director["max_segments"] == 6
    assert director["max_frames_per_segment"] == 345
    assert director["max_overlap_frames"] == 34
    # A composition mode the override's `modes` dict never mentions at all is
    # completely untouched.
    assert merged["modes"]["t2v"] == {}
    assert merged["modes"]["i2v"] == {}
    assert merged["modes"]["flf"] == {}


def test_overlay_strips_preset_mode_overrides_key():
    assert "preset_mode_overrides" not in apply_preset_mode_overlay(H3_VIDEO_CAPS, "refs")


def test_overlay_on_capabilities_with_no_overrides_declared_at_all():
    merged = apply_preset_mode_overlay(WAN_CAPS, "director")
    assert merged == WAN_CAPS


# ---------------------------------------------------------------------------
# `references` capability -- a per-segment SELECTION from a whole-film pool
# held on named form fields (see apply_preset_mode_overlay/H3_VIDEO_CAPS above).
# ---------------------------------------------------------------------------

H3_REFS_CAPS = {
    "modes": {
        "t2v": {}, "i2v": {}, "flf": {},
        "director": {"max_segments": 6, "max_frames_per_segment": 345},
    },
    "limits": _LIMITS,
    "segment_routing": True,
    "references": "per_shot",
    "reference_fields": ["references", "reference_videos", "reference_audios"],
}

H3_WHOLE_CAPS = {**H3_REFS_CAPS, "references": "whole"}


def test_references_capability_off_rejects_a_segment_selection(storage_dir):
    doc = _base_doc("t2v", segments=[{"id": "seg-1", "prompt": "a", "references": [{"path": "image.png"}]}])
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, LTX_CAPS, str(storage_dir))
    assert "references are not supported" in str(excinfo.value)


def test_references_capability_whole_rejects_a_segment_selection(storage_dir):
    doc = _base_doc("t2v", segments=[{"id": "seg-1", "prompt": "a", "references": [{"path": "image.png"}]}])
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, H3_WHOLE_CAPS, str(storage_dir))
    assert "'whole'" in str(excinfo.value)


def test_references_capability_whole_allows_an_absent_selection(storage_dir):
    doc = _base_doc("t2v", segments=[{"id": "seg-1", "prompt": "a"}])
    out = normalize_video_director(doc, H3_WHOLE_CAPS, str(storage_dir))
    assert out["segments"][0]["references"] is None


def test_references_per_shot_absent_selection_inherits_the_pool(storage_dir):
    doc = _base_doc("t2v", segments=[{"id": "seg-1", "prompt": "a"}])
    out = normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir))
    assert out["segments"][0]["references"] is None
    assert out["segments"][0]["reference_indices"] is None


def test_references_per_shot_path_entry_resolves(storage_dir):
    # A `path`/`relative_path` entry must still name an item already sitting
    # in the pool (form_data's reference_fields) -- see
    # test_references_per_shot_path_entry_not_in_pool_is_rejected below for
    # what happens when it doesn't.
    doc = _base_doc(
        "t2v",
        segments=[{"id": "seg-1", "prompt": "a", "references": [{"relative_path": "image.png"}]}],
    )
    form_data = {"references": [{"relative_path": "image.png"}]}
    out = normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir), form_data)
    assert out["segments"][0]["references"][0]["path"] == str(storage_dir / "image.png")
    assert out["segments"][0]["reference_indices"] == [0]


def test_references_per_shot_path_entry_not_in_pool_is_rejected(storage_dir):
    # The file exists on disk and inside storage_dir -- it's just not part of
    # ANY reference_fields item, so it isn't a valid pool selection.
    doc = _base_doc(
        "t2v",
        segments=[{"id": "seg-1", "prompt": "a", "references": [{"relative_path": "image.png"}]}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir), form_data={})
    assert "not part of this preset's reference pool" in str(excinfo.value)


def test_references_per_shot_form_media_entry_resolves(storage_dir):
    doc = _base_doc(
        "t2v",
        segments=[{
            "id": "seg-1", "prompt": "a",
            "references": [{"form_media": {"field": "references", "label": "Hero"}}],
        }],
    )
    form_data = {"references": [{"path": "storage/uploads/hero.png", "relative_path": "image.png", "label": "Hero"}]}
    out = normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir), form_data)
    assert out["segments"][0]["references"][0]["path"] == str(storage_dir / "image.png")
    assert out["segments"][0]["reference_indices"] == [0]


def test_references_per_shot_indices_pack_multiple_fields_in_declared_order(storage_dir):
    # reference_fields = ["references", "reference_videos", "reference_audios"]:
    # the packed pool is every "references" item, then every "reference_videos"
    # item, then every "reference_audios" item -- an entry from the SECOND
    # field lands at an index offset by the first field's own item count.
    doc = _base_doc(
        "t2v",
        segments=[{
            "id": "seg-1", "prompt": "a",
            "references": [
                {"form_media": {"field": "reference_videos", "label": "Clip"}},
                {"form_media": {"field": "references", "label": "Hero"}},
            ],
        }],
    )
    form_data = {
        "references": [
            {"relative_path": "image.png", "label": "Hero"},
            {"relative_path": "nested/ref.png", "label": "Other"},
        ],
        "reference_videos": [{"relative_path": "clip.mp4", "label": "Clip"}],
    }
    out = normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir), form_data)
    # "Clip" is reference_videos[0], which sits AFTER both "references" items
    # in the packed pool (index 2); "Hero" is references[0] (index 0).
    # Order is the SELECTION's order (Clip requested first), not sorted.
    assert out["segments"][0]["reference_indices"] == [2, 0]


def test_references_per_shot_indices_dedup_but_preserve_selection_order(storage_dir):
    doc = _base_doc(
        "t2v",
        segments=[{
            "id": "seg-1", "prompt": "a",
            "references": [
                {"form_media": {"field": "references", "label": "Second"}},
                {"form_media": {"field": "references", "label": "First"}},
                {"form_media": {"field": "references", "label": "Second"}},
            ],
        }],
    )
    form_data = {
        "references": [
            {"relative_path": "image.png", "label": "First"},
            {"relative_path": "nested/ref.png", "label": "Second"},
        ],
    }
    out = normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir), form_data)
    # "Second" (index 1) selected first, then "First" (index 0), then
    # "Second" again -- the duplicate collapses, and the surviving order is
    # first-occurrence, not the pool's own 0..1 order.
    assert out["segments"][0]["reference_indices"] == [1, 0]
    assert len(out["segments"][0]["references"]) == 3


def test_references_per_shot_empty_list_is_rejected(storage_dir):
    doc = _base_doc("t2v", segments=[{"id": "seg-1", "prompt": "a", "references": []}])
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir))
    assert "is empty" in str(excinfo.value)


# ---------------------------------------------------------------------------
# `continuation: null` -- structural hard-cut-only chains (references cannot
# combine with continuation; see docs/video-director.md "Preset mode
# overlays" and generator/video_minimax_h3/windows.py's module docstring).
# ---------------------------------------------------------------------------

CONTINUATION_DISABLED_CAPS = {
    "modes": {
        "t2v": {}, "i2v": {}, "flf": {},
        "director": {"max_segments": 6, "max_frames_per_segment": 345, "continuation": None},
    },
    "limits": _LIMITS,
    "segment_routing": True,
}


def test_continuation_disabled_derives_a_prompt_only_later_segment_as_a_hard_cut(storage_dir):
    # Under normal chain rules a prompt-only segment after the first derives
    # "chain" (continues); under continuation: null it derives "t2v" instead
    # -- silently, no error, since nothing was explicitly requested.
    doc = _base_doc(
        "director",
        settings={"fps": 24, "duration": None, "resolution": "", "seed": -1, "continuation": None},
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 81},
            {"id": "seg-2", "prompt": "b", "frames": 81},
        ],
    )
    out = normalize_video_director(doc, CONTINUATION_DISABLED_CAPS, str(storage_dir))
    assert [s["sub_type"] for s in out["segments"]] == ["t2v", "t2v"]


def test_continuation_disabled_rejects_an_explicit_chain_override(storage_dir):
    doc = _base_doc(
        "director",
        settings={"fps": 24, "duration": None, "resolution": "", "seed": -1, "continuation": None},
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 81},
            {"id": "seg-2", "prompt": "b", "frames": 81, "sub_type": "chain"},
        ],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, CONTINUATION_DISABLED_CAPS, str(storage_dir))
    assert "segments[1]" in str(excinfo.value)
    assert "condition-row overlay" in str(excinfo.value)


def test_continuation_disabled_rejects_a_settings_continuation_block(storage_dir):
    doc = _base_doc(
        "director",
        settings={"fps": 24, "duration": None, "resolution": "", "seed": -1,
                   "continuation": {"source": "tail_frames", "overlap_frames": 4, "stitch": True}},
        segments=[{"id": "seg-1", "prompt": "a", "frames": 81}],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, CONTINUATION_DISABLED_CAPS, str(storage_dir))
    assert "settings.continuation" in str(excinfo.value)
    assert "condition-row overlay" in str(excinfo.value)


def test_continuation_absent_key_does_not_disable_chain_derivation(storage_dir):
    # Regression guard: WAN_CAPS's director mode never declares `continuation`
    # at all -- absence must NOT be treated as "explicitly null".
    doc = _base_doc(
        "director",
        settings={"fps": 24, "duration": None, "resolution": "", "seed": -1, "continuation": None},
        segments=[
            {"id": "seg-1", "prompt": "a", "frames": 81},
            {"id": "seg-2", "prompt": "b", "frames": 49},
        ],
    )
    out = normalize_video_director(doc, WAN_CAPS, str(storage_dir))
    assert [s["sub_type"] for s in out["segments"]] == ["t2v", "chain"]


def test_references_per_shot_form_media_field_must_be_declared(storage_dir):
    doc = _base_doc(
        "t2v",
        segments=[{
            "id": "seg-1", "prompt": "a",
            "references": [{"form_media": {"field": "not_a_reference_field", "label": "Hero"}}],
        }],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir), form_data={})
    assert "reference_fields" in str(excinfo.value)


def test_references_per_shot_form_media_unmatched_label_errors(storage_dir):
    doc = _base_doc(
        "t2v",
        segments=[{
            "id": "seg-1", "prompt": "a",
            "references": [{"form_media": {"field": "references", "label": "Nope"}}],
        }],
    )
    form_data = {"references": [{"relative_path": "image.png", "label": "Hero"}]}
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir), form_data)
    assert "no item on form field" in str(excinfo.value)


def test_references_per_shot_both_path_and_form_media_is_an_error(storage_dir):
    doc = _base_doc(
        "t2v",
        segments=[{
            "id": "seg-1", "prompt": "a",
            "references": [{"relative_path": "image.png", "form_media": {"field": "references", "label": "Hero"}}],
        }],
    )
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir), form_data={})
    assert "not both" in str(excinfo.value)


def test_references_per_shot_must_be_a_list(storage_dir):
    doc = _base_doc("t2v", segments=[{"id": "seg-1", "prompt": "a", "references": "not-a-list"}])
    with pytest.raises(VideoDirectorValidationError) as excinfo:
        normalize_video_director(doc, H3_REFS_CAPS, str(storage_dir))
    assert "must be a list" in str(excinfo.value)
