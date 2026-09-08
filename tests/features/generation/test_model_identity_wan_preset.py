"""
`resolve_model_identity` against a REAL rendering of the marketplace Wan
preset's `video` mode (`content/presets/marketplace/Wan/modes/video/pipeline.yml`),
driven through the real `PipelineBuilder` - the same class
`GenerationOrchestrator._resolve_model_key` calls at enqueue time - rather than
hand-built pipe dicts (see `tests/features/generation/test_model_identity.py`
for those).

Mirrors the fixture shape of `tests/features/presets/test_video_director_pipeline.py`
(hand-built Video Director documents matching `normalize_video_director`'s
output, run through `derive_segment_routing` for the `needs_t2v_set`/
`needs_i2v_set` flags the pipeline's `model_loader/wan22` pipes gate on), but
goes through `PipelineBuilder.build_pipeline` end to end instead of calling
`PresetProcessor.process` directly - the `PipelineBuilder`-over-a-shared-
instance idiom from `tests/pipelines/test_preview_execution_invariance.py`.
"""

from __future__ import annotations

import copy
from unittest.mock import Mock

import pytest

from src.features.generation.model_identity import resolve_model_identity
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.models.records import Model
from src.features.forms.binding import bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.features.video_director.normalize import derive_segment_routing
from src.platform.templating.processor import TemplateProcessor


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


# A single i2v segment: only the i2v `model_loader/wan22` pipe is enabled.
DOC_I2V = {
    "schema_version": 1, "mode": "i2v",
    "settings": _settings(),
    "segments": [_segment()],
    "media": [_media("first", "seg-0", "/storage/uploads/start.png")],
    "audio": [], "ic_lora": [],
}

# A director chain that opens on a fresh t2v shot (segment 0, prompt-only, no
# media) and continues as chain (i2v-set, per `wan_model_set_for`) - it needs
# BOTH loader sets enabled, unlike DOC_I2V above.
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
def wan_pipeline_builder():
    """A real `PipelineBuilder` (real `PresetTemplateLoader` + real
    `PresetProcessor`) over the actual marketplace Wan preset - no model
    index or database involved, since rendering only produces the `config`
    dicts `resolve_model_identity` is handed; the fake `lookup_model` in each
    test stands in for the model index."""
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if p.path.rstrip("/").endswith("/Wan")), None)
    if template is None:
        pytest.skip("marketplace Wan preset not present")

    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    builder = PipelineBuilder(preset_template_loader=Mock(), preset_processor=processor)
    return builder, template


def _ref(model_id: str) -> str:
    return f"model:{model_id}"


def _build_pipes(
    wan_pipeline_builder,
    doc,
    *,
    t2v_high="t2v-high",
    t2v_low="t2v-low",
    i2v_high="i2v-high",
    i2v_low="i2v-low",
):
    builder, template = wan_pipeline_builder
    doc = copy.deepcopy(doc)
    doc.update(derive_segment_routing(doc["segments"], doc["media"]))
    form_data = {
        "video_director": doc,
        "t2v_high_noise_model": _ref(t2v_high),
        "t2v_low_noise_model": _ref(t2v_low),
        "i2v_high_noise_model": _ref(i2v_high),
        "i2v_low_noise_model": _ref(i2v_low),
        "text_encoder": _ref("shared-te"),
        "vae": _ref("shared-vae"),
        "resolution": "832x480",
    }
    bound = bind_form(template, "video", None, form_data, user_id=None, storage_dir=None)
    built = builder.build_pipeline(
        preset_id=template, form_data=dict(bound.values), mode="video", form_name=None, user_id=None,
    )
    return built.pipes


_CHECKPOINT_IDS = {"t2v-high", "t2v-low", "t2v-high-alt", "i2v-high", "i2v-low", "i2v-low-alt"}


def _lookup(model_id):
    if model_id in _CHECKPOINT_IDS:
        return Model(id=model_id, model_type="checkpoint", sha256=f"sha-{model_id}")
    if model_id == "shared-te":
        return Model(id=model_id, model_type="text_encoder", sha256="sha-te")
    if model_id == "shared-vae":
        return Model(id=model_id, model_type="vae", sha256="sha-vae")
    return None


class TestWanPresetThroughTheRealPipelineBuilder:
    def test_i2v_only_ignores_an_unused_t2v_selection(self, wan_pipeline_builder):
        """`needs_t2v_set` is False for a pure i2v segment, so the disabled
        `model_loader/wan22` t2v pipe's own selection - unused, but still
        present in the form - must not affect the key."""
        pipes_a = _build_pipes(wan_pipeline_builder, DOC_I2V, t2v_high="t2v-high")
        pipes_b = _build_pipes(wan_pipeline_builder, DOC_I2V, t2v_high="t2v-high-alt")

        key_a = resolve_model_identity(pipes_a, _lookup)
        key_b = resolve_model_identity(pipes_b, _lookup)

        assert key_a is not None
        assert key_a == key_b

    def test_needing_both_sets_differs_from_i2v_only(self, wan_pipeline_builder):
        pipes_i2v_only = _build_pipes(wan_pipeline_builder, DOC_I2V)
        pipes_both = _build_pipes(wan_pipeline_builder, DOC_CHAIN_MIXED)

        key_i2v_only = resolve_model_identity(pipes_i2v_only, _lookup)
        key_both = resolve_model_identity(pipes_both, _lookup)

        assert key_i2v_only is not None
        assert key_both is not None
        assert key_i2v_only != key_both

    def test_different_i2v_low_expert_changes_the_key(self, wan_pipeline_builder):
        pipes_a = _build_pipes(wan_pipeline_builder, DOC_I2V, i2v_low="i2v-low")
        pipes_b = _build_pipes(wan_pipeline_builder, DOC_I2V, i2v_low="i2v-low-alt")

        key_a = resolve_model_identity(pipes_a, _lookup)
        key_b = resolve_model_identity(pipes_b, _lookup)

        assert key_a is not None
        assert key_b is not None
        assert key_a != key_b
