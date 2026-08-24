"""Tests for the native MiniMax-H3 preset's `refs` (ref2va) mode.

The load-bearing property is the SHARED loader: `prompt_encoder.reference_image`
and `generator.reference_images` must both come from the one `references` node,
because the encoder numbers its inputs `<Picture 1>..<Picture N>` by array
position and the generator packs its reference rows in the same order. Two
loaders would let the two orders drift, and nothing downstream could notice.

Mirrors tests/features/presets/test_minimax_h3_sol_attn_wiring.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.features.video_director import normalize_video_director
from src.features.video_director.normalize import apply_preset_mode_overlay
from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe
from src.platform.templating.processor import TemplateProcessor

_REFERENCES = [
    {"path": "/media/ref-woman.png", "name": "ref-woman.png", "type": "image", "label": "the woman"},
    {"path": "/media/ref-cafe.png", "name": "ref-cafe.png", "type": "image", "label": "the cafe"},
]
_REFERENCE_VIDEOS = [
    {"path": "/media/ref-walk.mp4", "name": "ref-walk.mp4", "type": "video", "label": "the walk cycle"},
]
_REFERENCE_AUDIOS = [
    {"path": "/media/ref-voice.wav", "name": "ref-voice.wav", "type": "audio", "label": "her voice"},
]


@pytest.fixture(scope="module")
def h3_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "MiniMax-H3" in str(p.path)), None)
    if template is None:
        pytest.skip("native/MiniMax-H3 preset not present")
    return template


def _process(h3_template, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "model": "/models/minimax_h3_ref2va.safetensors",
        "text_encoder": "/models/qwen3_vl.safetensors",
        "video_vae": "/models/h3_video_vae.safetensors",
        "audio_vae": "/models/h3_audio_vae.safetensors",
        "resolution": "1344x768",
        "prompt": "a dragon",
        "references": _REFERENCES,
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {
        "prompts": [{"positive": "a dragon", "negative": ""}],
        "mode": "refs",
        "form_data": form_data,
    }
    return processor.process(h3_template, generation_data)


def _pipe(pipes, name, pipe_id=None):
    return next(p for p in pipes if p["name"] == name and (pipe_id is None or p["id"] == pipe_id))


def _edge(pipe, input_name):
    return next(i for i in pipe["input"] if i["name"] == input_name)


def _mode_dir(h3_template) -> Path:
    return Path(h3_template.path) / "modes" / "refs"


def _walk(node):
    yield node
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            yield from _walk(child)


def _tab_fields(h3_template, tab: str) -> list:
    return yaml.safe_load((_mode_dir(h3_template) / "tabs" / f"{tab}.yml").read_text())["fields"]


def _named_field(h3_template, tab: str, name: str) -> dict:
    return next(
        node for field in _tab_fields(h3_template, tab) for node in _walk(field)
        if node.get("name") == name
    )


def test_the_refs_mode_is_declared(h3_template):
    assert "refs" in h3_template.modes
    assert _mode_dir(h3_template).is_dir()


def test_every_reference_modality_has_a_multi_field_at_the_pipe_s_own_limit(h3_template):
    """The generator refuses more references than the released checkpoint
    packs, per modality, so the form must not let a user assemble a request
    that can only fail."""
    from src.pipelines.pipes.generator.video_minimax_h3.main import (
        _MAX_REFERENCE_AUDIOS,
        _MAX_REFERENCE_IMAGES,
        _MAX_REFERENCE_VIDEOS,
    )

    for name, field_type, limit in (
        ("references", "image", _MAX_REFERENCE_IMAGES),
        ("reference_videos", "video", _MAX_REFERENCE_VIDEOS),
        ("reference_audios", "audio", _MAX_REFERENCE_AUDIOS),
    ):
        field = _named_field(h3_template, "references", name)
        assert field["type"] == field_type
        assert field["configuration"]["multi"] is True
        assert field["configuration"]["max_items"] == limit


def test_no_reference_field_is_individually_required(h3_template):
    """Any ONE of the three satisfies the request, which a per-field
    `required` cannot express -- the generator's `mode: references` guard is
    what refuses an empty set instead."""
    for name in ("references", "reference_videos", "reference_audios"):
        assert _named_field(h3_template, "references", name).get("required") is not True
    cfg = _pipe(_process(h3_template), "generator/video_minimax_h3")["config"]
    assert cfg["mode"] == "references"


def test_each_reference_field_ai_hint_states_its_own_label_numbering(h3_template):
    """The packed order is only usable by the assistant if each picker says
    which label its position maps to."""
    for name, label in (
        ("references", "<Picture 1>"), ("reference_videos", "<Video 1>"), ("reference_audios", "<Audio 1>"),
    ):
        assert label in _named_field(h3_template, "references", name)["ai_hint"]


def test_both_reference_consumers_read_the_same_loader(h3_template):
    pipes = _process(h3_template)

    loader = _pipe(pipes, "media_loader", "references")
    assert loader["enabled"] is True
    assert loader["config"]["media"] == [
        {"type": "image", "path": "/media/ref-woman.png"},
        {"type": "image", "path": "/media/ref-cafe.png"},
    ]

    encoder_edge = _edge(_pipe(pipes, "prompt_encoder"), "reference_image")
    generator_edge = _edge(_pipe(pipes, "generator/video_minimax_h3"), "reference_images")
    assert encoder_edge["provider"] == "references"
    assert generator_edge["provider"] == "references"
    assert encoder_edge["provider"] == generator_edge["provider"]
    assert encoder_edge["output_var"] == generator_edge["output_var"] == "image"


def test_every_modality_has_one_loader_that_both_consumers_read(h3_template):
    """The one-loader rule holds per KIND: two loaders over one field would
    let the encoder's order and the generator's drift with nothing downstream
    able to notice."""
    pipes = _process(h3_template, {
        "references": _REFERENCES,
        "reference_videos": _REFERENCE_VIDEOS,
        "reference_audios": _REFERENCE_AUDIOS,
    })
    for loader_id, kind, encoder_input, generator_input, expected in (
        ("references", "image", "reference_image", "reference_images", _REFERENCES),
        ("reference_videos", "video", "reference_video", "reference_videos", _REFERENCE_VIDEOS),
        ("reference_audios", "audio", "reference_audio", "reference_audios", _REFERENCE_AUDIOS),
    ):
        loader = _pipe(pipes, "media_loader", loader_id)
        assert loader["enabled"] is True
        assert loader["config"]["media"] == [
            {"type": kind, "path": entry["path"]} for entry in expected
        ]
        encoder_edge = _edge(_pipe(pipes, "prompt_encoder"), encoder_input)
        generator_edge = _edge(_pipe(pipes, "generator/video_minimax_h3"), generator_input)
        assert encoder_edge["provider"] == generator_edge["provider"] == loader_id
        assert encoder_edge["output_var"] == generator_edge["output_var"] == kind


def test_every_modality_s_raw_value_reaches_the_generator_for_the_count_check(h3_template):
    cfg = _pipe(_process(h3_template, {
        "references": _REFERENCES,
        "reference_videos": _REFERENCE_VIDEOS,
        "reference_audios": _REFERENCE_AUDIOS,
    }), "generator/video_minimax_h3")["config"]
    assert cfg["references"] == _REFERENCES
    assert cfg["reference_videos"] == _REFERENCE_VIDEOS
    assert cfg["reference_audios"] == _REFERENCE_AUDIOS


def test_a_video_only_request_renders_without_any_image_reference(h3_template):
    """The released contract allows a reference set with no images at all
    (only an audio-ONLY set is refused), so the preset has to be able to
    render one."""
    pipes = _process(h3_template, {"references": [], "reference_videos": _REFERENCE_VIDEOS})
    assert _pipe(pipes, "media_loader", "references")["enabled"] is False
    assert _pipe(pipes, "media_loader", "reference_videos")["enabled"] is True


def test_the_reference_video_truncation_matches_the_generated_frame_count(h3_template):
    """The encoder and the generator's condition-encode must cut a reference
    video at the SAME frame; the two configs are rendered from one expression
    and have to stay equal for every duration."""
    for form_over in ({}, {"duration": 8.7}, {"duration": 14.3}):
        pipes = _process(h3_template, form_over)
        assert (
            _pipe(pipes, "prompt_encoder")["config"]["reference_video_frames"]
            == _pipe(pipes, "generator/video_minimax_h3")["config"]["frames"]
        )


def test_the_raw_references_value_also_reaches_the_generator(h3_template):
    """The generator cross-validates its `references` config against the
    loaded `reference_images` array, so the raw form value has to arrive too
    -- and as real dicts, not a stringified list."""
    cfg = _pipe(_process(h3_template), "generator/video_minimax_h3")["config"]
    assert cfg["references"] == _REFERENCES
    assert isinstance(cfg["references"], list)
    assert all(isinstance(entry, dict) for entry in cfg["references"])


def test_the_config_and_the_loader_never_disagree_on_count(h3_template):
    """Both views come from `form.references` in the same file, so the pipe's
    mismatch guard can only fire on a real loader failure -- never on the
    preset having wired the two to different sources."""
    for references in ([], _REFERENCES, _REFERENCES + [{"path": "/media/ref-3.png"}]):
        pipes = _process(h3_template, {"references": references})
        loaded = _pipe(pipes, "media_loader", "references")["config"]["media"] if references else []
        declared = _pipe(pipes, "generator/video_minimax_h3")["config"]["references"]
        assert len(declared) == len(loaded) == len(references)


def test_the_loader_preserves_form_order(h3_template):
    """`<Picture N>` numbering is array position, so a reordered form must
    produce a reordered loader rather than a canonicalized one."""
    pipes = _process(h3_template, {"references": list(reversed(_REFERENCES))})
    paths = [entry["path"] for entry in _pipe(pipes, "media_loader", "references")["config"]["media"]]
    assert paths == ["/media/ref-cafe.png", "/media/ref-woman.png"]


def test_the_loader_accepts_bare_path_strings(h3_template):
    """A multi media field's items may be plain paths (tests.yml writes them
    that way) as well as full media references."""
    pipes = _process(h3_template, {"references": ["/media/a.png", "/media/b.png"]})
    assert _pipe(pipes, "media_loader", "references")["config"]["media"] == [
        {"type": "image", "path": "/media/a.png"},
        {"type": "image", "path": "/media/b.png"},
    ]


def test_the_loader_is_disabled_without_references(h3_template):
    pipes = _process(h3_template, {"references": []})
    assert _pipe(pipes, "media_loader", "references")["enabled"] is False


def test_the_generator_carries_no_fl2va_inputs_but_threads_the_director(h3_template):
    """ref2va references are mutually exclusive with fl2va's anchor overlay,
    so keyframe_anchors is never rendered here -- but the Video Director
    document IS threaded now (refs-mode director chains, hard-cut-only)."""
    generator = _pipe(_process(h3_template), "generator/video_minimax_h3")
    assert {i["name"] for i in generator["input"]} == {
        "model", "conditioning", "seed",
        "reference_images", "reference_videos", "reference_audios",
    }
    assert "keyframe_anchors" not in generator["config"]
    assert "document" in generator["config"]


def test_the_mode_declares_no_keyframe_fields(h3_template):
    """fl2va's anchors have no meaning here and would be silently dropped by
    the pipeline -- they must not be offered."""
    names = {
        node.get("name")
        for tab in ("generation", "references")
        for field in _tab_fields(h3_template, tab)
        for node in _walk(field)
    }
    assert "first_frame" not in names
    assert "last_frame" not in names


def test_the_reference_pixel_budget_reaches_the_prompt_encoder(h3_template):
    cfg = _pipe(_process(h3_template, {"reference_pixel_budget": "2"}), "prompt_encoder")["config"]
    assert cfg["image_pixel_budget"] == "2"


def test_the_clip_length_in_seconds_reaches_the_generator_as_frames(h3_template):
    """With no Video Director document attached (a direct API/test request,
    per this mode's Generation tab header comment), the form's own Clip
    length - SECONDS, the app-wide duration unit - is what the generator
    runs, converted at the fixed 24 fps (floored; the pipe snaps up to the
    17n+5 lattice itself)."""
    cfg = _pipe(_process(h3_template, {"duration": 8.7}), "generator/video_minimax_h3")["config"]
    assert cfg["frames"] == 208

    cfg = _pipe(_process(h3_template, {"duration": 5}), "generator/video_minimax_h3")["config"]
    assert cfg["frames"] == 120


def test_omitted_duration_falls_back_to_default_frames(h3_template):
    cfg = _pipe(_process(h3_template, {}), "generator/video_minimax_h3")["config"]
    assert cfg["frames"] == 124


def test_the_video_director_is_wired_into_this_mode(h3_template):
    """refs joined preset_modes, with an overlay that scopes what the director
    may do here: per-shot reference selection, and no continuation (the
    ref2va prefix layout cannot coexist with continuation's anchor overlay,
    so every shot is an independent cut)."""
    vd = h3_template.vars["video_director"]
    assert vd["preset_modes"] == ["video", "refs"]
    override = vd["preset_mode_overrides"]["refs"]
    assert override["references"] == "per_shot"
    assert override["reference_fields"] == ["references", "reference_videos", "reference_audios"]
    director = override["modes"]["director"]
    assert "continuation" in director and director["continuation"] is None


# -- Video Director active in refs mode: the shot prompt must reach the encoder --------

@pytest.fixture(scope="module")
def refs_director_capabilities(h3_template):
    """The EFFECTIVE (post-overlay) Director capabilities for refs mode --
    what `GenerationOrchestrator.start_generation()` actually normalizes
    against (`apply_preset_mode_overlay(base, 'refs')`)."""
    base = (h3_template.vars or {}).get("video_director") or {}
    return apply_preset_mode_overlay(base, "refs")


def _director_document(capabilities, storage_dir, segments, form_data=None):
    return normalize_video_director({
        "schema_version": 1,
        "mode": "director",
        "settings": {"fps": 24, "seed": 1234},
        "segments": segments,
        "media": [],
        "audio": [],
    }, capabilities, storage_dir, form_data or {})


def test_a_director_request_in_refs_mode_gets_one_conditioning_per_shot(h3_template, refs_director_capabilities, tmp_path):
    """Mirrors test_minimax_h3_director_wiring.py's
    test_a_director_request_gets_one_conditioning_per_shot for `video` mode:
    the director encoder -- not the plain, top-box one -- must carry the
    per-shot prompts, and the generator's conditioning edge must read it."""
    document = _director_document(refs_director_capabilities, str(tmp_path), [
        {"id": "seg-0", "prompt": "the woman walks into frame", "frames": 124},
        {"id": "seg-1", "prompt": "she sits at the cafe table", "frames": 124},
    ], {"references": _REFERENCES})
    pipes = _process(h3_template, {"references": _REFERENCES, "video_director": document})

    encoder = _pipe(pipes, "prompt_encoder", "prompt_encoder_director")
    assert encoder["enabled"] is True
    assert encoder["config"]["quantity"] == 2
    assert [pair["positive"] for pair in encoder["config"]["pairs"]] == [
        "the woman walks into frame", "she sits at the cafe table",
    ]

    generator = _pipe(pipes, "generator/video_minimax_h3")
    sources = {edge["name"]: edge["provider"] for edge in generator["input"]}
    assert sources["conditioning"] == "prompt_encoder_director"
    assert generator["config"]["document"]["segments"][0]["prompt"] == "the woman walks into frame"


def test_a_director_request_in_refs_mode_carries_per_shot_reference_selections(h3_template, refs_director_capabilities, tmp_path):
    storage_dir = str(tmp_path)
    ref1 = Path(storage_dir) / "woman.png"
    ref1.write_bytes(b"")
    ref2 = Path(storage_dir) / "cafe.png"
    ref2.write_bytes(b"")
    references = [{"path": str(ref1)}, {"path": str(ref2)}]

    document = _director_document(refs_director_capabilities, storage_dir, [
        {"id": "seg-0", "prompt": "the woman", "frames": 124, "references": [{"path": str(ref1)}]},
        {"id": "seg-1", "prompt": "the cafe", "frames": 124, "references": [{"path": str(ref2)}]},
    ], {"references": references})
    pipes = _process(h3_template, {"references": references, "video_director": document})

    encoder = _pipe(pipes, "prompt_encoder", "prompt_encoder_director")
    assert encoder["config"]["reference_selections"] == [[0], [1]]


def test_the_director_encoder_sends_the_shot_s_own_prompt_to_the_text_encoder(h3_template, refs_director_capabilities, tmp_path):
    """The end-to-end repro: run the ACTUAL `PromptEncoderPipe.process()`
    against the rendered `prompt_encoder_director` config and a stub CLIP,
    and assert the text handed to `clip.encode_prompts` is each shot's own
    prompt -- not the top-of-form (Generation-tab) prompt, and not empty."""
    document = _director_document(refs_director_capabilities, str(tmp_path), [
        {"id": "seg-0", "prompt": "a red dragon flying over mountains", "frames": 124},
        {"id": "seg-1", "prompt": "a blue whale swimming in the deep", "frames": 124},
    ], {"references": _REFERENCES})
    pipes = _process(h3_template, {
        "references": _REFERENCES, "video_director": document,
        "prompt": "an unrelated top-of-form prompt that must not leak in",
    })
    config = _pipe(pipes, "prompt_encoder", "prompt_encoder_director")["config"]

    captured_prompts = []

    class _StubClip:
        def encode_prompts(self, requests):
            for request in requests:
                captured_prompts.append(request["prompt"])
            return [Mock(embeds={"context": Mock(), "token_tags": Mock()}) for _ in requests]

    pipe = PromptEncoderPipe(config)
    pipe_input = PipeInput(input={
        "text_encoder": _StubClip(),
        "reference_image": [Mock() for _ in _REFERENCES],
        "reference_video": [],
        "reference_audio": [],
        "MODELS": None,
    })
    pipe.process(pipe_input, lambda output: None)

    assert captured_prompts == [
        "a red dragon flying over mountains", "a blue whale swimming in the deep",
    ]


# -- a document's own duration wins over the Clip length slider ----------------

def test_a_derived_single_shot_document_s_duration_reaches_the_generator_as_frames(
    h3_template, refs_director_capabilities, tmp_path,
):
    """A single-shot document (mode t2v/i2v/flf, never 'director') takes the
    plain single-window ref2va path -- the generator's `document` config stays
    `None` for it (mode != 'director', see modes/refs/pipeline.yml) -- but its
    own `settings.duration` must still govern `frames`, not the Clip length
    slider's own (possibly stale) form value."""
    document = normalize_video_director({
        "schema_version": 1, "mode": "t2v",
        "settings": {"fps": 24, "seed": 1234, "duration": 9.5},
        "segments": [{"id": "seg-0", "prompt": "the woman walks into frame"}],
        "media": [], "audio": [],
    }, refs_director_capabilities, str(tmp_path))
    assert document["mode"] == "t2v"

    cfg = _pipe(_process(h3_template, {
        "references": _REFERENCES, "video_director": document, "duration": 5,
    }), "generator/video_minimax_h3")["config"]
    assert cfg["document"] is None  # confirms this stayed on the single-window path
    assert cfg["frames"] == 228  # 9.5s * 24fps, NOT the stale duration=5 slider value


def test_a_routed_multi_segment_document_s_duration_also_reaches_the_generator(
    h3_template, refs_director_capabilities, tmp_path,
):
    """The top-level `frames` this config carries is vestigial once a plan
    exists (each window's own frames drives the real windows), but it must
    still come from the document rather than the Clip length slider, the same
    rule the single-shot case above follows."""
    document = normalize_video_director({
        "schema_version": 1, "mode": "director",
        "settings": {"fps": 24, "seed": 1234, "duration": 12.3},
        "segments": [
            {"id": "seg-0", "prompt": "the woman walks into frame", "frames": 124},
            {"id": "seg-1", "prompt": "she sits at the cafe table", "frames": 124},
        ],
        "media": [], "audio": [],
    }, refs_director_capabilities, str(tmp_path), {"references": _REFERENCES})

    cfg = _pipe(_process(h3_template, {
        "references": _REFERENCES, "video_director": document, "duration": 5,
    }), "generator/video_minimax_h3")["config"]
    assert cfg["frames"] == 295  # 12.3s * 24fps, floored -- NOT the stale duration=5 slider value


def test_the_refs_mode_has_its_own_llm_guide(h3_template):
    manifest = yaml.safe_load((Path(h3_template.path) / "preset.yml").read_text())
    guide = manifest["llm"]["modes"]["refs"]["guide"]
    assert guide.strip()
    # ChatContextBuilder truncates a guide at 3000 chars, which would cut this
    # one mid-section.
    assert len(guide) < 3000
    for section in (
        "subject_definitions", "summary", "retention_analysis",
        "detailed_description", "overall_soundscape", "non_diegetic_music",
    ):
        assert section in guide
    # Each modality's own label counter has to be stated: the assistant writes
    # the prompt against these numbers, and they are per-picker positions.
    for label in ("<Picture 1>", "<Video 1>", "<Audio 1>"):
        assert label in guide
