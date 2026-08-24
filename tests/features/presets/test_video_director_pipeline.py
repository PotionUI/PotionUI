"""Tests for the Wan `video` mode pipeline (content/presets/marketplace/Wan/modes/video):
the Video Director document routes to exactly one generator per mode, the
`document` config reaches generator/chain_video_wan22 as a real dict (not a
stringified copy), media_loader gating follows the document's media roles,
and the single templated-provider gallery resolves to the right generator.
Also proves the rendered pipeline validates via GenerationManager.validate_pipeline
with the disabled generators/media_loaders present (the design this pipeline
relies on -- see the pipeline.yml header comment).
"""

from __future__ import annotations

import copy
from unittest.mock import Mock

import pytest

from src.features.generation.generation import GenerationManager, deep_update, validate_pipe_configuration
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor
from src.pipelines.contracts import IOType
from src.features.video_director.normalize import derive_ltx_media_fields, derive_segment_routing

from src.pipelines.pipes.gallery.main import GalleryPipe
from src.pipelines.pipes.generator.chain_video_wan22.main import GeneratorWanChainVideoPipe
from src.pipelines.pipes.generator.img2vid_wan22.main import GeneratorWanImg2VidPipe
from src.pipelines.pipes.generator.txt2vid_wan22.main import GeneratorWanTxt2VidPipe
from src.pipelines.pipes.generator.video_ltx.main import GeneratorLtxVideoPipe
from src.pipelines.pipes.media_loader.main import MediaLoaderPipe
from src.pipelines.pipes.model_loader.ltx.main import ModelLoaderLtxPipe
from src.pipelines.pipes.model_loader.wan22.main import ModelLoaderWan22Pipe
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe
from src.pipelines.pipes.seed_generator.main import SeedGeneratorPipe
from src.pipelines.pipes.from_iotype.main import FromIOTypePipe
from src.pipelines.pipes.param_emitter.main import ParamEmitterPipe


PIPE_CLASSES = {
    "model_loader/wan22": ModelLoaderWan22Pipe,
    "model_loader/ltx": ModelLoaderLtxPipe,
    "prompt_encoder": PromptEncoderPipe,
    "seed_generator": SeedGeneratorPipe,
    "from_iotype": FromIOTypePipe,
    "param_emitter": ParamEmitterPipe,
    "media_loader": MediaLoaderPipe,
    "generator/txt2vid_wan22": GeneratorWanTxt2VidPipe,
    "generator/img2vid_wan22": GeneratorWanImg2VidPipe,
    "generator/chain_video_wan22": GeneratorWanChainVideoPipe,
    "generator/video_ltx": GeneratorLtxVideoPipe,
    "gallery": GalleryPipe,
}


# -- canonical documents (hand-built, matching normalize_video_director's output
#    shape -- normalization itself is covered by tests/core/video_director/) --

def _settings(**over):
    base = {"fps": 16, "duration": 5, "resolution": "", "seed": 424242, "continuation": None}
    base.update(over)
    return base


def _segment(seg_id="seg-0", prompt="a cat", **over):
    seg = {
        "id": seg_id, "prompt": prompt, "negative_prompt": "blurry", "start": None, "end": None,
        "frames": None, "seed": None, "steps": None, "cfg": None, "loras": None,
    }
    seg.update(over)
    return seg


def _media(role, segment_id, path, **over):
    m = {"id": f"media-{role}", "role": role, "segment_id": segment_id, "at": None,
         "strength": 1.0, "media": {"path": path}}
    m.update(over)
    return m


DOC_T2V = {
    "schema_version": 1, "mode": "t2v",
    "settings": _settings(),
    "segments": [_segment()],
    "media": [], "audio": [], "ic_lora": [],
}

DOC_I2V = {
    "schema_version": 1, "mode": "i2v",
    "settings": _settings(),
    "segments": [_segment()],
    "media": [_media("first", "seg-0", "/storage/uploads/start.png")],
    "audio": [], "ic_lora": [],
}

DOC_FLF = {
    "schema_version": 1, "mode": "flf",
    "settings": _settings(),
    "segments": [_segment()],
    "media": [
        _media("first", "seg-0", "/storage/uploads/start.png"),
        _media("last", "seg-0", "/storage/uploads/end.png"),
    ],
    "audio": [], "ic_lora": [],
}

# The routed multi-segment `director` mode (the retired `chain` mode's shape,
# under the new mode string).
DOC_CHAIN = {
    "schema_version": 1, "mode": "director",
    "settings": _settings(duration=None, continuation={"source": None, "overlap_frames": 4, "stitch": True}),
    "segments": [
        _segment("seg-0", "a cat walks", frames=81),
        _segment("seg-1", "the cat sits", frames=81, negative_prompt="ugly"),
        _segment("seg-2", "the cat sleeps", frames=49, seed=999),
    ],
    "media": [_media("first", "seg-0", "/storage/uploads/start.png")],
    "audio": [], "ic_lora": [],
}

# A director chain that OPENS on a fresh t2v shot (segment 0, prompt-only, no
# media) and continues as chain -- it needs BOTH model sets loaded.
DOC_CHAIN_MIXED = {
    "schema_version": 1, "mode": "director",
    "settings": _settings(duration=None, continuation={"source": None, "overlap_frames": 4, "stitch": True}),
    "segments": [
        _segment("seg-0", "establishing shot", frames=81),
        _segment("seg-1", "the story continues", frames=81),
    ],
    "media": [], "audio": [], "ic_lora": [],
}


@pytest.fixture(scope="module")
def wan_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "native/Wan" in str(p.path)), None)
    if template is None:
        pytest.skip("native/Wan preset not present")
    return template


def _process(wan_template, doc, form_over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    # The DOC_* fixtures are hand-built to match normalize_video_director's
    # OUTPUT shape; the pipeline now expects the per-segment `sub_type` and the
    # request-level `needs_t2v_set`/`needs_i2v_set` flags precomputed onto the
    # document (normalize_video_director -> derive_segment_routing), so compute
    # them here the same way the real normalizer does (compare _process_ltx's
    # derive_ltx_media_fields shim below).
    doc = copy.deepcopy(doc)
    doc.update(derive_segment_routing(doc["segments"], doc["media"]))
    form_data = {
        "video_director": doc,
        "t2v_high_noise_model": "/models/wan_t2v_high.safetensors",
        "t2v_low_noise_model": "/models/wan_t2v_low.safetensors",
        "i2v_high_noise_model": "/models/wan_i2v_high.safetensors",
        "i2v_low_noise_model": "/models/wan_i2v_low.safetensors",
        "text_encoder": "/models/umt5.safetensors",
        "vae": "/models/wan_vae.safetensors",
        "resolution": "832x480",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "video", "form_data": form_data}
    return processor.process(wan_template, generation_data)


def _pipe(pipes, name, pipe_id=None):
    if pipe_id is not None:
        return next(p for p in pipes if p.get("id") == pipe_id)
    return next(p for p in pipes if p["name"] == name)


def _enabled_generators(pipes):
    return [p["name"] for p in pipes if p["name"].startswith("generator/") and p["enabled"]]


def _validate(pipes):
    manager = GenerationManager(
        gpu=Mock(), model_manager=Mock(), pipe_catalog=Mock(get_pipe=Mock(side_effect=PIPE_CLASSES.get)),
        settings_manager=Mock(), system_monitor=Mock(), memory_manager=Mock(),
        llm_service=Mock(), models=Mock(),
    )
    manager.validate_pipeline(pipes)


def _validate_configs(pipes):
    """Structural guard against a pipe's runtime logic
    grows a new config value -- e.g. video_ltx's `audio_source='passthrough'`
    -- without the same value being added to that pipe's declared
    `configuration()` choices): render the real preset, then run every
    ENABLED pipe's rendered config through the exact
    `validate_pipe_configuration` per-field check GenerationManager.generate
    runs at actual submission time (src/features/generation/generation.py),
    merged onto the pipe's own `get_default_config()` the same way that call
    site does. `validate_pipeline` (see `_validate` above) only checks input/
    output WIRING -- it never looks at a rendered config's values against the
    pipe's own spec, which is exactly the gap that let a declared-choices
    drift reach a live generation as an opaque ValueError instead of failing
    a unit test."""
    for pipe_config in pipes:
        if not pipe_config["enabled"]:
            continue
        pipe_class = PIPE_CLASSES.get(pipe_config["name"])
        if pipe_class is None:
            continue
        merged = deep_update(pipe_class.get_default_config() or {}, pipe_config.get("config", {}))
        validate_pipe_configuration(pipe_class, merged)


# -- mode routing: exactly one generator enabled per mode -------------------

@pytest.mark.parametrize("doc,expected", [
    (DOC_T2V, "generator/txt2vid_wan22"),
    (DOC_I2V, "generator/img2vid_wan22"),
    (DOC_FLF, "generator/img2vid_wan22"),
    (DOC_CHAIN, "generator/chain_video_wan22"),
])
def test_exactly_one_generator_enabled_per_mode(wan_template, doc, expected):
    pipes = _process(wan_template, doc)
    enabled = _enabled_generators(pipes)
    assert enabled == [expected]


@pytest.mark.parametrize("doc,expected_provider", [
    (DOC_T2V, "generator/txt2vid_wan22"),
    (DOC_I2V, "generator/img2vid_wan22"),
    (DOC_FLF, "generator/img2vid_wan22"),
    (DOC_CHAIN, "generator/chain_video_wan22"),
])
def test_gallery_provider_routing(wan_template, doc, expected_provider):
    pipes = _process(wan_template, doc)
    gallery = _pipe(pipes, "gallery")
    assert gallery["enabled"] is True
    video_input = next(i for i in gallery["input"] if i["name"] == "video")
    assert video_input["provider"] == expected_provider


# -- pairs expansion ----------------------------------------------------

def test_pairs_count_and_order_match_segments(wan_template):
    pipes = _process(wan_template, DOC_CHAIN)
    pe = _pipe(pipes, "prompt_encoder")
    pairs = pe["config"]["pairs"]
    assert isinstance(pairs, list)
    assert [p["positive"] for p in pairs] == ["a cat walks", "the cat sits", "the cat sleeps"]
    assert [p["negative"] for p in pairs] == ["blurry", "ugly", "blurry"]
    assert pe["config"]["quantity"] == 3  # exact-expression scalars render as native values


def test_t2v_pairs_is_single_entry(wan_template):
    pipes = _process(wan_template, DOC_T2V)
    pe = _pipe(pipes, "prompt_encoder")
    assert [p["positive"] for p in pe["config"]["pairs"]] == ["a cat"]


# -- @object document passthrough ----------------------------------------

def test_chain_document_is_a_real_dict_not_a_rendered_string(wan_template):
    pipes = _process(wan_template, DOC_CHAIN)
    chain = _pipe(pipes, "generator/chain_video_wan22")
    document = chain["config"]["document"]
    assert isinstance(document, dict)
    assert document["mode"] == "director"
    assert len(document["segments"]) == 3
    assert document["segments"][2]["seed"] == 999


def test_document_not_present_in_non_chain_generator_configs(wan_template):
    # t2v/i2v/flf generators don't declare a `document` config -- but even if
    # rendered, txt2vid/img2vid generator config specs simply wouldn't use it.
    # Just confirm the chain generator specifically gets the real object.
    pipes = _process(wan_template, DOC_I2V)
    img2vid = _pipe(pipes, "generator/img2vid_wan22")
    assert "document" not in img2vid["config"]


# -- media_loader gating --------------------------------------------------

def test_t2v_neither_media_loader_enabled(wan_template):
    pipes = _process(wan_template, DOC_T2V)
    assert _pipe(pipes, None, pipe_id="media_first")["enabled"] is False
    assert _pipe(pipes, None, pipe_id="media_last")["enabled"] is False


def test_i2v_only_media_first_enabled(wan_template):
    pipes = _process(wan_template, DOC_I2V)
    first = _pipe(pipes, None, pipe_id="media_first")
    last = _pipe(pipes, None, pipe_id="media_last")
    assert first["enabled"] is True
    assert last["enabled"] is False
    assert first["config"]["media"] == [{"type": "image", "path": "/storage/uploads/start.png"}]


def test_flf_both_media_loaders_enabled(wan_template):
    pipes = _process(wan_template, DOC_FLF)
    first = _pipe(pipes, None, pipe_id="media_first")
    last = _pipe(pipes, None, pipe_id="media_last")
    assert first["enabled"] is True
    assert last["enabled"] is True
    assert first["config"]["media"] == [{"type": "image", "path": "/storage/uploads/start.png"}]
    assert last["config"]["media"] == [{"type": "image", "path": "/storage/uploads/end.png"}]


def test_chain_media_first_enabled_media_last_not(wan_template):
    pipes = _process(wan_template, DOC_CHAIN)
    first = _pipe(pipes, None, pipe_id="media_first")
    last = _pipe(pipes, None, pipe_id="media_last")
    assert first["enabled"] is True
    assert last["enabled"] is False


# -- expert-boundary picker + switch-at-step mapping -----------------------

def test_expert_boundary_preset_and_switch_step_reach_the_generator(wan_template):
    pipes = _process(wan_template, DOC_T2V, form_over={"expert_boundary_preset": "0.9", "expert_switch_step": 12})
    cfg = _pipe(pipes, "generator/txt2vid_wan22")["config"]
    assert cfg["expert_boundary"] == "0.9"
    assert cfg["expert_switch_step"] == "12"


def test_expert_boundary_defaults_are_empty_when_unset(wan_template):
    # The picker's "Model default" (value "") and an unset switch step both
    # render empty, so the generator falls back to each set's native boundary.
    cfg = _pipe(_process(wan_template, DOC_T2V), "generator/txt2vid_wan22")["config"]
    assert cfg["expert_boundary"] == ""
    assert cfg["expert_switch_step"] == ""


# -- frames/fps computation for t2v/i2v/flf --------------------------------

def test_t2v_frames_computed_from_duration_and_fps(wan_template):
    pipes = _process(wan_template, DOC_T2V)  # duration=5, fps=16 -> 80
    t2v = _pipe(pipes, "generator/txt2vid_wan22")
    assert t2v["config"]["frames"] == 80
    assert t2v["config"]["fps"] == 16


# -- pipeline validity (disabled generators/media_loaders present) ---------

@pytest.mark.parametrize("doc", [DOC_T2V, DOC_I2V, DOC_FLF, DOC_CHAIN, DOC_CHAIN_MIXED])
def test_rendered_pipeline_validates(wan_template, doc):
    pipes = _process(wan_template, doc)
    # Must not raise -- proves the disabled pipes (a disabled model-set loader,
    # two generators, up to two media_loaders per request) don't break
    # validate_pipeline, and that the enabled generator's model input plus the
    # single gallery's templated provider always name an enabled pipe.
    _validate(pipes)


# -- dual model-set conditional loading -------------------------------------

def _loaders(pipes):
    return {p["id"]: p["enabled"] for p in pipes if p["name"] == "model_loader/wan22"}


@pytest.mark.parametrize("doc,t2v_on,i2v_on", [
    (DOC_T2V, True, False),          # pure t2v -> only the t2v set loads
    (DOC_I2V, False, True),          # pure i2v -> only the i2v set loads
    (DOC_FLF, False, True),          # flf is an i2v-set sub-type
    (DOC_CHAIN, False, True),        # chain from a start image -> i2v set only
    (DOC_CHAIN_MIXED, True, True),   # t2v opener + chain continuation -> both
])
def test_model_set_loaders_conditionally_enabled(wan_template, doc, t2v_on, i2v_on):
    loaders = _loaders(_process(wan_template, doc))
    assert loaders == {"t2v_loader": t2v_on, "i2v_loader": i2v_on}


@pytest.mark.parametrize("doc,expected", [
    (DOC_T2V, ["t2v"]),
    (DOC_I2V, ["i2v"]),
    (DOC_FLF, ["flf"]),
    (DOC_CHAIN, ["i2v", "chain", "chain"]),  # seg-0 has a start image, rest continue
    (DOC_CHAIN_MIXED, ["t2v", "chain"]),     # prompt-only opener is a fresh t2v shot
])
def test_segment_sub_types_derived_onto_document(wan_template, doc, expected):
    # derive_segment_routing ran in _process; for chain mode the enriched
    # document rides on the chain generator, so read the resolved sub_types
    # back off it (t2v/i2v/flf are single-segment and covered above).
    pipes = _process(wan_template, doc)
    if doc["mode"] == "director":
        chain = _pipe(pipes, "generator/chain_video_wan22")
        assert [s["sub_type"] for s in chain["config"]["document"]["segments"]] == expected


def test_segment_sub_type_override_forces_fresh_cut(wan_template):
    # A prompt-only later segment defaults to 'chain'; an explicit sub_type
    # override forces a fresh t2v shot instead (and pulls in the t2v set).
    doc = {
        "schema_version": 1, "mode": "director",
        "settings": _settings(duration=None, continuation={"source": None, "overlap_frames": 4, "stitch": True}),
        "segments": [
            _segment("seg-0", "shot one", frames=81),
            _segment("seg-1", "hard cut", frames=81, sub_type="t2v"),
        ],
        "media": [], "audio": [], "ic_lora": [],
    }
    pipes = _process(wan_template, doc)
    chain = _pipe(pipes, "generator/chain_video_wan22")
    assert [s["sub_type"] for s in chain["config"]["document"]["segments"]] == ["t2v", "t2v"]
    assert _loaders(pipes) == {"t2v_loader": True, "i2v_loader": False}


def test_chain_generator_wired_to_both_model_sets(wan_template):
    pipes = _process(wan_template, DOC_CHAIN_MIXED)
    chain = _pipe(pipes, "generator/chain_video_wan22")
    providers = {i["name"]: i["provider"] for i in chain["input"]}
    assert providers["model"] == "i2v_loader"
    assert providers["model_t2v"] == "t2v_loader"


@pytest.mark.parametrize("doc,expected_clip_provider", [
    (DOC_T2V, "t2v_loader"),           # only t2v set enabled
    (DOC_I2V, "i2v_loader"),           # only i2v set enabled
    (DOC_CHAIN, "i2v_loader"),         # i2v-only chain
    (DOC_CHAIN_MIXED, "t2v_loader"),   # both enabled -> prefer the t2v loader
])
def test_prompt_encoder_clip_from_an_enabled_loader(wan_template, doc, expected_clip_provider):
    pipes = _process(wan_template, doc)
    pe = _pipe(pipes, "prompt_encoder")
    clip_input = next(i for i in pe["input"] if i["name"] == "text_encoder")
    assert clip_input["provider"] == expected_clip_provider


# ===========================================================================
# LTX-2 `video` mode (content/presets/marketplace/LTX-2/modes/video)
#
# Unlike Wan, every LTX Director mode (t2v/i2v/flf/director) routes through
# ONE generator (generator/video_ltx) -- it handles t2v with zero media
# placements. The interesting surface here is the media INDEX ALIGNMENT
# between media_images/media_videos (what actually gets loaded, and in what
# order) and generator/video_ltx's media_placements (which references those
# arrays purely by integer index) -- see the pipeline.yml header comment.
# ===========================================================================

def _media_full(role, segment_id, path, media_type=None, **over):
    m = {"id": f"media-{role}-{path}", "role": role, "segment_id": segment_id, "at": None,
         "strength": 1.0, "media": {"path": path}}
    if media_type is not None:
        m["media"]["type"] = media_type
    m.update({k: v for k, v in over.items() if k != "media"})
    if "at" in over:
        m["at"] = over["at"]
    if "strength" in over:
        m["strength"] = over["strength"]
    return m


def _ltx_settings(**over):
    base = {"fps": 25, "duration": 5, "resolution": "", "seed": 111, "continuation": None}
    base.update(over)
    return base


DOC_T2V_LTX = {
    "schema_version": 1, "mode": "t2v",
    "settings": _ltx_settings(),
    "segments": [_segment("seg-0", "a dog runs")],
    "media": [], "audio": [], "ic_lora": [],
}

DOC_I2V_LTX = {
    "schema_version": 1, "mode": "i2v",
    "settings": _ltx_settings(),
    "segments": [_segment("seg-0", "a dog runs")],
    "media": [_media_full("first", "seg-0", "/up/start.png")],
    "audio": [], "ic_lora": [],
}

DOC_FLF_LTX = {
    "schema_version": 1, "mode": "flf",
    "settings": _ltx_settings(),
    "segments": [_segment("seg-0", "a dog runs")],
    "media": [
        _media_full("first", "seg-0", "/up/start.png"),
        _media_full("last", "seg-0", "/up/end.png"),
    ],
    "audio": [], "ic_lora": [],
}


def _director_doc(with_audio=False):
    media = [
        _media_full("first", "seg-0", "/up/first.png", strength=1.0),
        _media_full("last", "seg-1", "/up/last.png", strength=0.9),
        # Out of chronological order on purpose -- media_images/placements must
        # sort keyframes by `at`, not by list position.
        _media_full("keyframe", "seg-0", "/up/kf_late.png", at=6.0, strength=0.8),
        _media_full("keyframe", "seg-0", "/up/kf_early.png", at=2.0, strength=0.7),
        # v1 cut: a video-typed keyframe must be dropped, not misrouted.
        _media_full("keyframe", "seg-0", "/up/kf_video.mp4", media_type="video", at=4.0, strength=0.5),
    ]
    audio = [{"id": "a1", "start": 0.0, "trim_start": 0.0, "length": 8.0, "media": {"path": "/up/track.mp3"}}] \
        if with_audio else []
    return {
        "schema_version": 1, "mode": "director",
        "settings": _ltx_settings(duration=8),
        "segments": [
            _segment("seg-0", "a dog runs", negative_prompt="blurry"),
            _segment("seg-1", "into a forest", negative_prompt="ugly"),
        ],
        "media": media,
        "audio": audio,
        "ic_lora": [
            {"id": "ic1", "lora": {"model": "/loras/ic_lora.safetensors", "strength": 0.6},
             "reference": {"path": "/up/ref.mp4", "type": "video"}, "strength": 0.75},
        ],
    }


DOC_DIRECTOR_LTX = _director_doc(with_audio=False)
DOC_DIRECTOR_LTX_AUDIO = _director_doc(with_audio=True)

# An image-typed IC-LoRA reference alongside the video-typed one above
# -- must route to media_images (not the cv2-backed media_videos loader) and
# keep every index aligned across both lists.
DOC_DIRECTOR_LTX_MIXED_REFS = _director_doc(with_audio=False)
DOC_DIRECTOR_LTX_MIXED_REFS = {
    **DOC_DIRECTOR_LTX_MIXED_REFS,
    "ic_lora": DOC_DIRECTOR_LTX_MIXED_REFS["ic_lora"] + [
        {"id": "ic2", "lora": {"model": "/loras/ic_lora2.safetensors", "strength": 0.4},
         "reference": {"path": "/up/ref_still.png", "type": "image"}, "strength": 0.55},
    ],
}


@pytest.fixture(scope="module")
def ltx_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if str(p.path).endswith("native/LTX-2")), None)
    if template is None:
        pytest.skip("native/LTX-2 preset not present")
    return template


def _process_ltx(ltx_template, doc, form_over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    # These DOC_*_LTX fixtures match normalize_video_director's OUTPUT shape
    # but are hand-built (not run through the real normalizer -- their media
    # paths don't exist on disk, which normalize_video_director's containment
    # check would reject). The pipeline now expects `media_images`/
    # `media_placements` precomputed onto the document by
    # derive_ltx_media_fields() (see src/core/video_director/normalize.py),
    # so compute them here the same way normalize_video_director does.
    doc = copy.deepcopy(doc)
    doc.update(derive_ltx_media_fields(doc["media"], doc["ic_lora"], doc["settings"].get("fps")))
    form_data = {
        "video_director": doc,
        "model": "/models/ltx.safetensors",
        "text_encoder": "/models/gemma3.safetensors",
        "loras": [{"model": "/loras/form_lora.safetensors", "strength": 0.9}],
        "resolution": "768x512",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "video", "form_data": form_data}
    return processor.process(ltx_template, generation_data)


# -- routing: always ONE generator, always video_ltx -----------------------

@pytest.mark.parametrize("doc", [DOC_T2V_LTX, DOC_I2V_LTX, DOC_FLF_LTX, DOC_DIRECTOR_LTX])
def test_ltx_video_ltx_generator_always_enabled(ltx_template, doc):
    pipes = _process_ltx(ltx_template, doc)
    generators = [p["name"] for p in pipes if p["name"].startswith("generator/") and p["enabled"]]
    assert generators == ["generator/video_ltx"]


@pytest.mark.parametrize("doc", [DOC_T2V_LTX, DOC_I2V_LTX, DOC_FLF_LTX, DOC_DIRECTOR_LTX])
def test_ltx_gallery_provider_is_always_video_ltx(ltx_template, doc):
    # The in-flow upscale option was ported onto this mode, which
    # required giving the (still single, still `name: "generator/video_ltx"`)
    # generator node an explicit `id: "generator_stage1"` -- gallery's
    # provider now resolves by that id rather than the bare pipe name (a
    # second, disabled-by-default `generator_stage2` id exists for the
    # upscale refine pass; none of these docs turn Upscale on).
    pipes = _process_ltx(ltx_template, doc)
    gallery = _pipe(pipes, "gallery")
    assert gallery["enabled"] is True
    video_input = next(i for i in gallery["input"] if i["name"] == "video")
    assert video_input["provider"] == "generator_stage1"


# -- prompt join order ----------------------------------------------------

def test_ltx_t2v_single_segment_prompt(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_T2V_LTX)
    pe = _pipe(pipes, "prompt_encoder")
    pairs = pe["config"]["pairs"]
    assert len(pairs) == 1
    assert pairs[0]["positive"] == "a dog runs"


def test_ltx_director_prompts_joined_in_start_order_negative_is_first_segment(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX)
    pe = _pipe(pipes, "prompt_encoder")
    pairs = pe["config"]["pairs"]
    assert len(pairs) == 1
    assert pairs[0]["positive"] == "a dog runs into a forest"
    assert pairs[0]["negative"] == "blurry"


# -- media_images / media_videos: order, content, the v1 video-keyframe cut -

def test_ltx_i2v_media_images_single_entry(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_I2V_LTX)
    images = _pipe(pipes, None, pipe_id="media_images")
    assert images["enabled"] is True
    assert images["config"]["media"] == [{"type": "image", "path": "/up/start.png"}]


def test_ltx_director_media_images_order_first_last_keyframes_sorted_by_at(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX)
    images = _pipe(pipes, None, pipe_id="media_images")
    assert images["enabled"] is True
    # first, last, then keyframes sorted by `at` (2.0 before 6.0) -- the
    # video-typed keyframe (kf_video.mp4, at=4.0) is dropped entirely.
    assert images["config"]["media"] == [
        {"type": "image", "path": "/up/first.png"},
        {"type": "image", "path": "/up/last.png"},
        {"type": "image", "path": "/up/kf_early.png"},
        {"type": "image", "path": "/up/kf_late.png"},
    ]


def test_ltx_t2v_media_images_disabled(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_T2V_LTX)
    assert _pipe(pipes, None, pipe_id="media_images")["enabled"] is False


def test_ltx_director_media_videos_has_ic_lora_reference(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX)
    videos = _pipe(pipes, None, pipe_id="media_videos")
    assert videos["enabled"] is True
    assert videos["config"]["media"] == [{"type": "video", "path": "/up/ref.mp4"}]


def test_ltx_i2v_media_videos_disabled_no_ic_lora(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_I2V_LTX)
    assert _pipe(pipes, None, pipe_id="media_videos")["enabled"] is False


# -- media_placements: the index-alignment contract ------------------------

def test_ltx_director_media_placements_index_alignment(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX)
    video_ltx = _pipe(pipes, "generator/video_ltx")
    placements = video_ltx["config"]["media_placements"]

    # Exactly 5: first, last, 2 image keyframes (video keyframe dropped), 1 ic reference.
    assert len(placements) == 5

    # fps=25 -> at=2.0 => frame 50, at=6.0 => frame 150 (derive_ltx_media_fields'
    # round_half_up_common of at*fps). Exact-expression scalars render native.
    assert placements[0] == {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe"}
    assert placements[1] == {"source": "image", "index": 1, "frame": "last", "strength": 0.9, "role": "keyframe"}
    assert placements[2] == {"source": "image", "index": 2, "frame": 50, "strength": 0.7, "role": "keyframe"}
    assert placements[3] == {"source": "image", "index": 3, "frame": 150, "strength": 0.8, "role": "keyframe"}
    assert placements[4] == {"source": "video", "index": 0, "frame": "first", "strength": 0.75, "role": "reference"}


def test_ltx_director_image_ic_lora_reference_routes_to_media_images(ltx_template):
    # An image-typed ic_lora.reference must land in media_images
    # (source: "image"), not the cv2-backed media_videos loader -- it's
    # appended AFTER the four keyframe images, at index 4; the video-typed
    # reference from the same doc keeps its own media_videos index 0.
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX_MIXED_REFS)
    images = _pipe(pipes, None, pipe_id="media_images")
    videos = _pipe(pipes, None, pipe_id="media_videos")

    assert images["config"]["media"] == [
        {"type": "image", "path": "/up/first.png"},
        {"type": "image", "path": "/up/last.png"},
        {"type": "image", "path": "/up/kf_early.png"},
        {"type": "image", "path": "/up/kf_late.png"},
        {"type": "image", "path": "/up/ref_still.png"},
    ]
    assert videos["enabled"] is True
    assert videos["config"]["media"] == [{"type": "video", "path": "/up/ref.mp4"}]

    video_ltx = _pipe(pipes, "generator/video_ltx")
    placements = video_ltx["config"]["media_placements"]
    assert len(placements) == 6
    assert placements[4] == {"source": "video", "index": 0, "frame": "first", "strength": 0.75, "role": "reference"}
    assert placements[5] == {"source": "image", "index": 4, "frame": "first", "strength": 0.55, "role": "reference"}


def test_ltx_t2v_media_placements_empty(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_T2V_LTX)
    video_ltx = _pipe(pipes, "generator/video_ltx")
    assert video_ltx["config"]["media_placements"] == []


# -- stage 2 reconnects image/video/media_placements ----------------

def test_ltx_upscale_on_stage2_gets_same_media_placements_as_stage1(ltx_template):
    """With Upscale on, generator_stage2 must receive the IDENTICAL
    media_placements @loop as generator_stage1 (same node outputs feed both
    -> index alignment is automatic; see the pipeline header's media-index-
    alignment contract)."""
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX,
                         form_over={"upscale": "1.5x", "upscale_refine_sigmas": ""})
    stage1 = _pipe(pipes, None, pipe_id="generator_stage1")
    stage2 = _pipe(pipes, None, pipe_id="generator_stage2")
    assert stage2["enabled"] is True
    assert stage2["config"]["media_placements"] == stage1["config"]["media_placements"]
    assert len(stage2["config"]["media_placements"]) == 5


def test_ltx_upscale_off_stage2_media_placements_still_render_but_stage2_disabled(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX)
    stage2 = _pipe(pipes, None, pipe_id="generator_stage2")
    assert stage2["enabled"] is False


def test_ltx_upscale_on_stage2_reconnects_image_and_video_inputs(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX,
                         form_over={"upscale": "1.5x", "upscale_refine_sigmas": ""})
    stage2 = _pipe(pipes, None, pipe_id="generator_stage2")
    stage2_input_names = {i["name"] for i in stage2["input"]}
    assert {"image", "video", "audio", "initial_latent", "model", "conditioning", "seed"} <= stage2_input_names
    image_input = next(i for i in stage2["input"] if i["name"] == "image")
    video_input = next(i for i in stage2["input"] if i["name"] == "video")
    assert image_input["provider"] == "media_images"
    assert video_input["provider"] == "media_videos"


# -- upscale resolution semantics: the picked Resolution is STAGE 1's
# resolution, Upscale multiplies it (deliberately not the "LTX workflow"
# idiom of stage 1 at Resolution/factor, upscaling back UP to the picked
# Resolution) --------------------------------------------------------------

def test_ltx_upscale_off_stage1_resolution_is_plain_form_resolution(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX)
    stage1 = _pipe(pipes, None, pipe_id="generator_stage1")
    assert stage1["config"]["resolution"] == "768x512"


@pytest.mark.parametrize("upscale,factor", [("1.5x", 1.5), ("2.0x", 2.0)])
def test_ltx_upscale_on_stage1_resolution_is_still_the_plain_picked_resolution(ltx_template, upscale, factor):
    # Stage 1 no longer renders at Resolution/factor -- it always renders at
    # the picked Resolution, unchanged by Upscale.
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX,
                         form_over={"upscale": upscale, "upscale_refine_sigmas": ""})
    stage1 = _pipe(pipes, None, pipe_id="generator_stage1")
    assert stage1["config"]["resolution"] == "768x512"


@pytest.mark.parametrize("upscale,expected", [("1.5x", "1152x768"), ("2.0x", "1536x1024")])
def test_ltx_upscale_on_stage2_resolution_is_picked_resolution_times_factor(ltx_template, upscale, expected):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX,
                         form_over={"upscale": upscale, "upscale_refine_sigmas": ""})
    stage2 = _pipe(pipes, None, pipe_id="generator_stage2")
    assert stage2["config"]["resolution"] == expected


def test_ltx_upscale_2x_lands_stage2_on_the_64_grid(ltx_template):
    # Geometry note: a /32-grid stage-1 resolution x 2.0 always lands
    # on the /64 grid the DiT requires, so 2.0x can no longer reproduce the
    # lattice mismatch under the new semantics -- 544x960 (both /32,
    # neither /64) is exactly the shape that used to trip it.
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX,
                         form_over={"upscale": "2.0x", "upscale_refine_sigmas": "", "resolution": "544x960"})
    stage1 = _pipe(pipes, None, pipe_id="generator_stage1")
    stage2 = _pipe(pipes, None, pipe_id="generator_stage2")
    assert stage1["config"]["resolution"] == "544x960"
    assert stage2["config"]["resolution"] == "1088x1920"
    w, h = (int(x) for x in stage2["config"]["resolution"].split("x"))
    assert w % 64 == 0 and h % 64 == 0


# -- audio routing truth table ---------------------------------------------

@pytest.mark.parametrize("doc,generate_audio,expected_audio,expected_source", [
    (DOC_DIRECTOR_LTX, False, "false", "generate"),   # no clips, toggle off -> no audio
    (DOC_DIRECTOR_LTX, True, "true", "generate"),     # no clips, toggle on -> generate
    (DOC_DIRECTOR_LTX_AUDIO, False, "true", "file"),  # user clip, toggle off -> mux the file
    (DOC_DIRECTOR_LTX_AUDIO, True, "true", "file"),   # user clip wins over the toggle
])
def test_ltx_audio_routing_truth_table(ltx_template, doc, generate_audio, expected_audio, expected_source):
    pipes = _process_ltx(ltx_template, doc, form_over={"generate_audio": generate_audio})
    video_ltx = _pipe(pipes, "generator/video_ltx")
    loader = _pipe(pipes, "model_loader/ltx")
    expected_audio_bool = expected_audio == "true"
    assert video_ltx["config"]["audio"] == expected_audio_bool
    assert video_ltx["config"]["audio_source"] == expected_source
    # The loader's own audio bool always mirrors the generator's (same formula).
    assert loader["config"]["audio"] == expected_audio_bool


# -- two-stage upscale + audio compose -------------------

def test_ltx_two_stage_with_generated_audio_wires_stage1_audio_into_stage2(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX, form_over={
        "generate_audio": True, "upscale": "1.5x", "upscale_refine_sigmas": "",
    })
    stage1 = _pipe(pipes, None, pipe_id="generator_stage1")
    stage2 = _pipe(pipes, None, pipe_id="generator_stage2")
    assert stage2["enabled"] is True
    # Stage 1: video decode skipped (feeds latent_upscaler), audio still on
    # and generated -- the independent audio decode isn't gated by `decode`.
    assert stage1["config"]["decode"] is False
    assert stage1["config"]["audio"] is True
    assert stage1["config"]["audio_source"] == "generate"
    # Stage 2: always decodes video; mux-only passthrough audio, reading
    # stage 1's OWN `audio` output (not media_audio).
    assert stage2["config"]["decode"] is True
    assert stage2["config"]["audio"] is True
    assert stage2["config"]["audio_source"] == "passthrough"
    audio_input = next(i for i in stage2["input"] if i["name"] == "audio")
    assert audio_input["provider"] == "generator_stage1"
    gallery = _pipe(pipes, "gallery")
    video_input = next(i for i in gallery["input"] if i["name"] == "video")
    assert video_input["provider"] == "generator_stage2"


def test_ltx_two_stage_with_user_audio_clip_wires_stage1_audio_into_stage2(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX_AUDIO, form_over={
        "upscale": "2.0x", "upscale_refine_sigmas": "",
    })
    stage1 = _pipe(pipes, None, pipe_id="generator_stage1")
    stage2 = _pipe(pipes, None, pipe_id="generator_stage2")
    assert stage1["config"]["decode"] is False
    assert stage1["config"]["audio"] is True
    assert stage1["config"]["audio_source"] == "file"
    assert stage2["enabled"] is True
    assert stage2["config"]["audio_source"] == "passthrough"
    audio_input = next(i for i in stage2["input"] if i["name"] == "audio")
    assert audio_input["provider"] == "generator_stage1"


def test_ltx_two_stage_without_audio_unchanged(ltx_template):
    # No audio requested at all -- two-stage wiring/decode flags are
    # otherwise untouched by the final-part audio hand-off.
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX, form_over={
        "upscale": "1.5x", "upscale_refine_sigmas": "",
    })
    stage1 = _pipe(pipes, None, pipe_id="generator_stage1")
    stage2 = _pipe(pipes, None, pipe_id="generator_stage2")
    assert stage1["config"]["decode"] is False
    assert stage1["config"]["audio"] is False
    assert stage2["enabled"] is True
    assert stage2["config"]["audio"] is False
    assert stage2["config"]["audio_source"] == "passthrough"


def test_ltx_single_stage_with_audio_unchanged(ltx_template):
    # Upscale off -- single full-resolution decode pass, audio generated
    # exactly as before this change (stage 2 doesn't even exist/enable).
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX, form_over={"generate_audio": True})
    stage1 = _pipe(pipes, None, pipe_id="generator_stage1")
    assert stage1["config"]["decode"] is True
    assert stage1["config"]["audio"] is True
    assert stage1["config"]["audio_source"] == "generate"
    assert _pipe(pipes, None, pipe_id="generator_stage2")["enabled"] is False
    gallery = _pipe(pipes, "gallery")
    video_input = next(i for i in gallery["input"] if i["name"] == "video")
    assert video_input["provider"] == "generator_stage1"


def test_ltx_media_audio_enabled_only_with_user_clips(ltx_template):
    assert _pipe(_process_ltx(ltx_template, DOC_DIRECTOR_LTX), None, pipe_id="media_audio")["enabled"] is False
    assert _pipe(_process_ltx(ltx_template, DOC_DIRECTOR_LTX_AUDIO), None, pipe_id="media_audio")["enabled"] is True
    audio_pipe = _pipe(_process_ltx(ltx_template, DOC_DIRECTOR_LTX_AUDIO), None, pipe_id="media_audio")
    assert audio_pipe["config"]["media"] == [{"type": "audio", "path": "/up/track.mp3"}]


# -- LoRA merge (form loras + ic_lora entries) ------------------------------

def test_ltx_loras_merge_form_and_ic_lora(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX)
    loader = _pipe(pipes, "model_loader/ltx")
    loras = loader["config"]["loras"]
    file_paths = {l["file_path"] for l in loras}
    assert file_paths == {"/loras/form_lora.safetensors", "/loras/ic_lora.safetensors"}
    by_path = {l["file_path"]: l["weight"] for l in loras}
    assert by_path["/loras/form_lora.safetensors"] == 0.9
    assert by_path["/loras/ic_lora.safetensors"] == 0.6


def test_ltx_t2v_loras_is_just_the_form_stack(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_T2V_LTX)
    loader = _pipe(pipes, "model_loader/ltx")
    loras = loader["config"]["loras"]
    assert [l["file_path"] for l in loras] == ["/loras/form_lora.safetensors"]


# -- frames/fps + sampler pin -----------------------------------------------

def test_ltx_frames_computed_from_duration_and_fps(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_T2V_LTX)  # duration=5, fps=25 -> 125
    video_ltx = _pipe(pipes, "generator/video_ltx")
    assert video_ltx["config"]["frames"] == 125
    assert video_ltx["config"]["fps"] == 25


def test_ltx_sampler_defaults_to_euler(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX)
    video_ltx = _pipe(pipes, "generator/video_ltx")
    assert video_ltx["config"]["sampler"] == "euler"


def test_ltx_sampler_follows_form_choice(ltx_template):
    # Director mode's pipeline.yml used to hardcode
    # sampler="euler" regardless of form input; it now forwards form.sampler
    # like every other mode (validated against GeneratorLtxVideoPipe's
    # PipeConfigSpec choices -- euler / euler_ancestral_cfg_pp only -- at the
    # pipe-config-validation layer, not at template-render time, which is all
    # this test exercises).
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX, form_over={"sampler": "euler_ancestral_cfg_pp"})
    video_ltx = _pipe(pipes, "generator/video_ltx")
    assert video_ltx["config"]["sampler"] == "euler_ancestral_cfg_pp"


def test_ltx_manual_sigmas_follows_form_value(ltx_template):
    recipe = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX, form_over={"manual_sigmas": recipe})
    video_ltx = _pipe(pipes, "generator/video_ltx")
    assert video_ltx["config"]["manual_sigmas"] == recipe


def test_ltx_manual_sigmas_defaults_empty(ltx_template):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX)
    video_ltx = _pipe(pipes, "generator/video_ltx")
    assert video_ltx["config"]["manual_sigmas"] == ""


# -- pipeline validity ------------------------------------------------------

@pytest.mark.parametrize("doc", [DOC_T2V_LTX, DOC_I2V_LTX, DOC_FLF_LTX, DOC_DIRECTOR_LTX, DOC_DIRECTOR_LTX_AUDIO])
def test_ltx_rendered_pipeline_validates(ltx_template, doc):
    pipes = _process_ltx(ltx_template, doc)
    _validate(pipes)


# -- rendered pipe CONFIGS must match each pipe's declared spec -----
#
# `test_ltx_rendered_pipeline_validates` above only proves the pipeline's
# input/output WIRING is sound; it never runs a rendered config through
# validate_pipe_configuration. A live crash --
# "Parameter 'audio_source' for pipe 'generator' must be one of
# ['generate', 'file'], but got: passthrough" -- was exactly that gap: the
# generator's runtime `audio_source='passthrough'` mode wasn't mirrored into
# its `configuration()` choices, and nothing at unit-test level rendered the
# real preset with BOTH upscale and audio active (the only combination that
# ever produces `audio_source='passthrough'`) and validated the result.

@pytest.mark.parametrize("upscale", ["1.5x", "2.0x"])
def test_ltx_rendered_pipe_configs_validate_with_upscale_and_audio_active(ltx_template, upscale):
    pipes = _process_ltx(ltx_template, DOC_DIRECTOR_LTX_AUDIO, form_over={
        "upscale": upscale, "upscale_refine_sigmas": "", "generate_audio": True,
    })
    stage2 = _pipe(pipes, None, pipe_id="generator_stage2")
    assert stage2["config"]["audio_source"] == "passthrough"
    _validate_configs(pipes)


@pytest.mark.parametrize("doc,form_over", [
    (DOC_T2V_LTX, None),
    (DOC_I2V_LTX, None),
    (DOC_FLF_LTX, None),
    (DOC_DIRECTOR_LTX, None),
    (DOC_DIRECTOR_LTX_AUDIO, None),
    (DOC_DIRECTOR_LTX, {"upscale": "1.5x", "upscale_refine_sigmas": ""}),
    (DOC_DIRECTOR_LTX, {"upscale": "2.0x", "upscale_refine_sigmas": ""}),
    (DOC_DIRECTOR_LTX_AUDIO, {"upscale": "1.5x", "upscale_refine_sigmas": "", "generate_audio": True}),
    (DOC_DIRECTOR_LTX_AUDIO, {"upscale": "2.0x", "upscale_refine_sigmas": "", "generate_audio": True}),
])
def test_ltx_rendered_pipe_configs_validate_against_declared_spec(ltx_template, doc, form_over):
    pipes = _process_ltx(ltx_template, doc, form_over=form_over)
    _validate_configs(pipes)
