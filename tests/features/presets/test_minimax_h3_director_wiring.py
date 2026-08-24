"""Tests for Video Director wiring in the native MiniMax-H3 preset's `video`
mode: the capability declaration must parse through the REAL normalizer, and a
normalized document must reach `generator/video_minimax_h3` as a live nested
dict alongside the per-shot conditioning and image loaders it needs.

Sibling of tests/features/presets/test_minimax_h3_manual_sigmas_wiring.py.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.features.video_director import normalize_video_director
from src.features.video_director.normalize import VideoDirectorValidationError
from src.platform.templating.processor import TemplateProcessor


@pytest.fixture(scope="module")
def h3_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "MiniMax-H3" in str(p.path)), None)
    if template is None:
        pytest.skip("MiniMax-H3 preset not present")
    return template


@pytest.fixture(scope="module")
def capabilities(h3_template):
    """Read exactly where the orchestrator reads it from."""
    return (h3_template.vars or {}).get("video_director") or {}


def _process(h3_template, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "model": "/models/minimax_h3.safetensors",
        "text_encoder": "/models/qwen3_vl.safetensors",
        "video_vae": "/models/h3_video_vae.safetensors",
        "audio_vae": "/models/h3_audio_vae.safetensors",
        "resolution": "1344x768",
        "prompt": "a dragon",
    }
    form_data.update(form_over or {})
    return processor.process(h3_template, {
        "prompts": [{"positive": "a dragon", "negative": ""}],
        "mode": "video",
        "form_data": form_data,
    })


def _pipe(pipes, name, pipe_id=None):
    return next(p for p in pipes if p["name"] == name and (pipe_id is None or p.get("id") == pipe_id))


def _raw_document(segment_count=2, *, frames=124, overlap=17, stitch=True, media=(), audio=()):
    return {
        "schema_version": 1,
        "mode": "director",
        "settings": {
            "fps": 24, "seed": 1234,
            "continuation": {"source": "tail_frames", "overlap_frames": overlap, "stitch": stitch},
        },
        "segments": [
            {"id": f"seg-{i}", "prompt": f"shot {i}", "frames": frames} for i in range(segment_count)
        ],
        "media": list(media),
        "audio": list(audio),
    }


# -- the capability declaration, through the real normalizer --------------------

def test_the_preset_declares_a_routed_multi_segment_director(capabilities):
    assert capabilities["preset_modes"] == ["video", "refs"]
    assert capabilities["segment_routing"] is True
    assert set(capabilities["modes"]) == {"t2v", "i2v", "flf", "director"}
    director = capabilities["modes"]["director"]
    assert director["keyframes"] == "anywhere"
    assert director["audio"] is True
    assert director["continuation"] == {"source": "tail_frames", "overlap_frames": 17, "stitch": True}
    # The preset no longer declares tips -- DirectorTipsBanner is a generic
    # capability other presets may use, but this one ships with none.
    assert not director.get("tips")


def test_a_director_document_normalizes_against_the_declared_capabilities(capabilities, tmp_path):
    document = normalize_video_director(_raw_document(3), capabilities, str(tmp_path))
    assert document["mode"] == "director"
    assert [s["sub_type"] for s in document["segments"]] == ["t2v", "chain", "chain"]
    assert document["settings"]["continuation"] == {
        "source": "tail_frames", "overlap_frames": 17, "stitch": True,
    }
    # derive_ltx_media_fields runs unconditionally -- the pipe reads these.
    assert document["media_images"] == [] and document["media_placements"] == []


@pytest.mark.parametrize("kwargs,message", [
    ({"segment_count": 7}, "at most 6 segments"),
    ({"frames": 400}, "between 1 and 345"),
    ({"overlap": 51}, "max_overlap_frames"),
])
def test_the_declared_caps_are_enforced_by_the_normalizer(capabilities, tmp_path, kwargs, message):
    with pytest.raises(VideoDirectorValidationError, match=message):
        normalize_video_director(_raw_document(**kwargs), capabilities, str(tmp_path))


def test_the_declared_caps_admit_a_keyframe_anywhere_on_the_timeline(capabilities, tmp_path):
    image = tmp_path / "k.png"
    image.write_bytes(b"")
    document = normalize_video_director(
        _raw_document(2, media=[{"role": "keyframe", "at": 6.0, "media": {"path": str(image), "type": "image"}}]),
        capabilities, str(tmp_path),
    )
    (placement,) = document["media_placements"]
    assert placement["frame"] == 144  # 6.0s at 24 fps
    assert document["media_images"] == [str(image)]


def test_a_non_director_mode_is_declared_so_the_editors_default_is_not_director(capabilities):
    """Regression guard for the bug where this preset declared ONLY `director`.

    frontend/src/lib/utils/videoDirector.ts derives the Director editor's
    default document mode from a FIXED `MODE_ORDER = ['t2v','i2v','flf',
    'director']` filtered down to whichever of those keys this dict declares
    -- not YAML declaration order. With `director` the only declared key,
    that default was always `director`, and `+page.svelte` attaches a
    `video_director` document on every submit once a preset declares this
    capability -- so EVERY H3 video request, including a plain Generation-tab
    prompt with no edges, carried a document whose `mode == "director"`.
    `build_director_plan()` (src/pipelines/pipes/generator/video_minimax_h3/
    windows.py) treats any such document as a routed multi-segment run and
    `generate_one()` (.../main.py) takes that branch before it ever reads the
    `keyframe_images`/`keyframe_anchors` a single-shot request needs -- so
    those, and plain t2va, were unreachable. At least one of t2v/i2v/flf must
    stay declared so the default mode is never `director` again -- this is
    also what lets the Video Director's own first/last-frame edge wells
    (there is no separate manual field for them) reach the generator at all.
    """
    assert set(capabilities["modes"]) & {"t2v", "i2v", "flf"}


def test_a_plain_single_shot_document_now_normalizes(capabilities, tmp_path):
    """Once unreachable: the minimal document the editor now defaults to
    (mode `t2v`, one prompt-only segment, no `frames`/`loras`/`cfg` -- those
    are chain-only fields) normalizes instead of hitting 'unsupported mode'."""
    document = {
        "schema_version": 1,
        "mode": "t2v",
        "settings": {"fps": 24, "seed": 1234},
        "segments": [{"id": "seg-0", "prompt": "a dragon"}],
        "media": [],
        "audio": [],
    }
    normalized = normalize_video_director(document, capabilities, str(tmp_path))
    assert normalized["mode"] == "t2v"


def test_an_actually_unsupported_mode_still_errors(capabilities, tmp_path):
    with pytest.raises(VideoDirectorValidationError, match="unsupported mode"):
        normalize_video_director({**_raw_document(1), "mode": "bogus"}, capabilities, str(tmp_path))


def test_a_director_mode_document_still_builds_a_windowed_plan_but_a_t2v_one_does_not(tmp_path):
    """The exact branch `generate_one()` keys off (`plan is not None`),
    proven against the two document shapes the frontend's default now
    chooses between: a `director` document (the pre-fix default, which took
    over the whole request) and a `t2v` one (the post-fix default, which
    leaves the plain Keyframes-tab path -- and its keyframe_images/anchors --
    in control)."""
    from src.pipelines.pipes.generator.video_minimax_h3.windows import build_director_plan

    director_doc = {
        "schema_version": 1, "mode": "director",
        "settings": {"fps": 24, "seed": 42, "continuation": {"source": "tail_frames", "overlap_frames": 17, "stitch": True}},
        "segments": [{"id": "chain-0", "prompt": "", "frames": 124, "sub_type": "t2v"}],
        "media": [], "audio": [],
    }
    t2v_doc = {
        "schema_version": 1, "mode": "t2v",
        "settings": {"fps": 24, "seed": 42},
        "segments": [{"id": "seg-0", "prompt": ""}],
        "media": [], "audio": [],
    }
    assert build_director_plan(director_doc, default_seed=-1) is not None
    assert build_director_plan(t2v_doc, default_seed=-1) is None


# -- the rendered pipeline --------------------------------------------------------

def test_without_a_document_the_director_nodes_stay_off(h3_template):
    pipes = _process(h3_template)
    assert _pipe(pipes, "media_loader", "director_media")["enabled"] is False
    assert _pipe(pipes, "prompt_encoder", "prompt_encoder_director")["enabled"] is False
    assert _pipe(pipes, "generator/video_minimax_h3")["config"]["document"] is None


def test_without_a_document_the_conditioning_still_comes_from_the_plain_encoder(h3_template):
    generator = _pipe(_process(h3_template), "generator/video_minimax_h3")
    sources = {edge["name"]: edge["provider"] for edge in generator["input"]}
    assert sources["conditioning"] == "prompt_encoder"


def test_the_document_reaches_the_generator_as_a_live_dict(h3_template, capabilities, tmp_path):
    """Raw passthrough, not a stringified copy: the pipe walks
    `document["segments"]` and `document["settings"]["continuation"]`."""
    document = normalize_video_director(_raw_document(3), capabilities, str(tmp_path))
    config = _pipe(_process(h3_template, {"video_director": document}), "generator/video_minimax_h3")["config"]
    assert isinstance(config["document"], dict)
    assert [s["prompt"] for s in config["document"]["segments"]] == ["shot 0", "shot 1", "shot 2"]
    assert config["document"]["settings"]["continuation"]["overlap_frames"] == 17


def test_a_director_request_gets_one_conditioning_per_shot(h3_template, capabilities, tmp_path):
    document = normalize_video_director(_raw_document(3), capabilities, str(tmp_path))
    pipes = _process(h3_template, {"video_director": document})

    encoder = _pipe(pipes, "prompt_encoder", "prompt_encoder_director")
    assert encoder["enabled"] is True
    assert encoder["config"]["quantity"] == 3
    assert [pair["positive"] for pair in encoder["config"]["pairs"]] == ["shot 0", "shot 1", "shot 2"]

    generator = _pipe(pipes, "generator/video_minimax_h3")
    sources = {edge["name"]: edge["provider"] for edge in generator["input"]}
    assert sources["conditioning"] == "prompt_encoder_director"


def test_director_images_load_through_their_own_node_and_not_the_text_encoder(h3_template, capabilities, tmp_path):
    """A Director keyframe conditions latent frames only. Routing it through
    the shared `keyframes` loader would put it in front of the text encoder's
    vision tower for EVERY shot, since the H3 clip adapter forwards its whole
    image batch to every request."""
    image = tmp_path / "k.png"
    image.write_bytes(b"")
    document = normalize_video_director(
        _raw_document(2, media=[{"role": "keyframe", "at": 3.0, "media": {"path": str(image), "type": "image"}}]),
        capabilities, str(tmp_path),
    )
    pipes = _process(h3_template, {"video_director": document})

    loader = _pipe(pipes, "media_loader", "director_media")
    assert loader["enabled"] is True
    assert [entry["path"] for entry in loader["config"]["media"]] == [str(image)]
    assert _pipe(pipes, "media_loader", "keyframes")["enabled"] is False

    encoder = _pipe(pipes, "prompt_encoder", "prompt_encoder_director")
    assert "image" not in {edge["name"] for edge in encoder.get("input") or []}
    generator = _pipe(pipes, "generator/video_minimax_h3")
    assert {edge["name"]: edge["provider"] for edge in generator["input"]}["director_image"] == "director_media"


def _single_shot_document(mode, capabilities, tmp_path, *, first=True, last=True):
    """A `t2v`/`i2v`/`flf` document shaped exactly like `buildDirectorSubmission`'s
    single-shot branches (frontend/src/lib/utils/videoDirector.ts:1386-1436):
    one prompt-only segment, edge media addressed by `role` alone -- the shape
    the Video Director's own first/last-frame edge wells submit, now the only
    way to attach one (there is no manual Keyframes tab)."""
    media = []
    if first:
        image = tmp_path / "first.png"
        image.write_bytes(b"")
        media.append({"id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0,
                       "strength": 1.0, "media": {"path": str(image), "type": "image"}})
    if last:
        image = tmp_path / "last.png"
        image.write_bytes(b"")
        media.append({"id": "m-2", "role": "last", "segment_id": "seg-1", "at": 5,
                       "strength": 1.0, "media": {"path": str(image), "type": "image"}})
    document = {
        "schema_version": 1, "mode": mode,
        "settings": {"fps": 24, "seed": 1234, "duration": 5},
        "segments": [{"id": "seg-1", "prompt": "a dragon"}],
        "media": media, "audio": [],
    }
    return normalize_video_director(document, capabilities, str(tmp_path))


def test_an_flf_single_shot_document_s_edges_reach_the_generator_as_image_and_anchors(h3_template, capabilities, tmp_path):
    """The property the removed Keyframes tab used to guarantee, now proven
    against the Video Director's own edge wells: a first+last pair reaches
    BOTH the text encoder (`prompt_encoder`'s `image` input) and the
    generator's condition rows (`image` + `keyframe_anchors`), in the same
    order, without taking over the routed-plan (`director_media`) path."""
    document = _single_shot_document("flf", capabilities, tmp_path)
    pipes = _process(h3_template, {"video_director": document})

    loader = _pipe(pipes, "media_loader", "keyframes")
    assert loader["enabled"] is True
    assert [entry["path"] for entry in loader["config"]["media"]] == [
        document["media"][0]["media"]["path"], document["media"][1]["media"]["path"],
    ]

    plain_encoder = next(p for p in pipes if p["name"] == "prompt_encoder" and p.get("id") is None)
    sources = {edge["name"]: edge["provider"] for edge in plain_encoder["input"]}
    assert sources["image"] == "keyframes"

    generator = _pipe(pipes, "generator/video_minimax_h3")
    assert {edge["name"]: edge["provider"] for edge in generator["input"]}["image"] == "keyframes"
    assert generator["config"]["keyframe_anchors"] == ["first", "last"]

    # The routed-plan path stays off for a derived single-shot document -- see
    # test_a_director_mode_document_still_builds_a_windowed_plan_but_a_t2v_one_does_not.
    assert _pipe(pipes, "media_loader", "director_media")["enabled"] is False


def test_an_i2v_single_shot_document_only_carries_the_leading_anchor(h3_template, capabilities, tmp_path):
    document = _single_shot_document("i2v", capabilities, tmp_path, last=False)
    pipes = _process(h3_template, {"video_director": document})

    loader = _pipe(pipes, "media_loader", "keyframes")
    assert loader["enabled"] is True
    assert [entry["path"] for entry in loader["config"]["media"]] == [document["media"][0]["media"]["path"]]
    assert _pipe(pipes, "generator/video_minimax_h3")["config"]["keyframe_anchors"] == ["first"]


def test_a_plain_t2v_document_leaves_the_keyframes_node_off(h3_template, capabilities, tmp_path):
    """No manual field to fall back to: with no edges attached, the removed
    Keyframes tab's replacement stays off exactly like the tab used to."""
    document = _single_shot_document("t2v", capabilities, tmp_path, first=False, last=False)
    pipes = _process(h3_template, {"video_director": document})

    assert _pipe(pipes, "media_loader", "keyframes")["enabled"] is False
    assert _pipe(pipes, "generator/video_minimax_h3")["config"]["keyframe_anchors"] == []


def test_the_rendered_document_builds_the_plan_the_pipe_will_run(h3_template, capabilities, tmp_path):
    """End of the chain: preset vars -> normalizer -> rendered config -> plan."""
    from src.pipelines.pipes.generator.video_minimax_h3.windows import build_director_plan

    document = normalize_video_director(_raw_document(3), capabilities, str(tmp_path))
    config = _pipe(_process(h3_template, {"video_director": document}), "generator/video_minimax_h3")["config"]
    plan = build_director_plan(config["document"], default_seed=-1)

    assert [w.frames for w in plan.windows] == [124, 124, 124]
    assert [w.overlap_frames for w in plan.windows] == [0, 17, 17]
    assert plan.total_frames == 124 * 3 - 34
    assert plan.stitch is True


# -- a document's own duration wins over the form's stale/absent field ------------

def test_a_derived_single_shot_document_s_duration_reaches_the_generator_as_frames(h3_template, capabilities, tmp_path):
    """A single-shot document (mode t2v/i2v/flf, never 'director') takes the
    plain single-window path -- `build_director_plan` returns `None` for it --
    but its own `settings.duration` must still govern the generator's `frames`,
    not `form.frames`: this mode's Generation tab has no such field for a user
    to ever set (tabs/generation.yml's header comment), so before this fix the
    pipeline always fell back to the preset's fixed `default_frames`
    regardless of what the shot card actually said."""
    from src.pipelines.pipes.generator.video_minimax_h3.windows import build_director_plan

    document = normalize_video_director({
        "schema_version": 1, "mode": "t2v",
        "settings": {"fps": 24, "seed": 1234, "duration": 9.5},
        "segments": [{"id": "seg-0", "prompt": "a dragon"}],
        "media": [], "audio": [],
    }, capabilities, str(tmp_path))
    assert document["mode"] == "t2v"
    assert build_director_plan(document, default_seed=-1) is None

    # A stale/unrelated `form.frames` must lose to the document once one is attached.
    config = _pipe(_process(h3_template, {"video_director": document, "frames": 999}), "generator/video_minimax_h3")["config"]
    assert config["frames"] == 228  # 9.5s * 24fps


def test_a_routed_multi_segment_document_s_duration_also_reaches_the_generator(h3_template, capabilities, tmp_path):
    """The top-level `frames` this config carries is vestigial once a plan
    exists -- each window's own `frames` is what `build_director_plan` (and
    therefore the actual generation) runs on -- but it must still be sourced
    from the document rather than a stale form field, the same rule the
    single-shot case above follows."""
    raw = _raw_document(2, frames=124)
    raw["settings"]["duration"] = 12.3
    document = normalize_video_director(raw, capabilities, str(tmp_path))
    assert document["mode"] == "director"

    config = _pipe(_process(h3_template, {"video_director": document, "frames": 999}), "generator/video_minimax_h3")["config"]
    assert config["frames"] == 295  # 12.3s * 24fps, floored


def test_the_presets_default_overlap_is_one_whole_vae_chunk(capabilities):
    """The declared default has to be an overlap the seam arithmetic is exact
    for -- a multiple of 17 pixel frames."""
    from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
        head_frames_for_latents, tail_latents_for_frames,
    )

    director = capabilities["modes"]["director"]
    for frames in (director["continuation"]["overlap_frames"], director["max_overlap_frames"]):
        assert head_frames_for_latents(tail_latents_for_frames(frames)) == frames
