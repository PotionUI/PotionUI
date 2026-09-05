"""`GenerationOrchestrator.preview_memory`'s active model set against a REAL
rendering of the marketplace Wan preset's `video` mode, through the REAL
`bind_form` and `PipelineBuilder` (never mocked) - unlike
`test_memory_preview_operation.py`'s fake-collaborator tests (kept there for
the zero-enqueue assertions), this proves the preview's active loader set
actually reflects a REAL Video Director document's `needs_t2v_set`/
`needs_i2v_set` flags (`normalize_video_director` -> `derive_segment_routing`,
the same ones `content/presets/marketplace/Wan/modes/video/pipeline.yml`
gates its two `model_loader/wan22` pipes on), not the raw wire document a
preview would otherwise see if it skipped Director preparation.

Mirrors the real-preset idiom of `test_model_identity_wan_preset.py`, but
drives the document through `normalize_video_director` for real (via
`GenerationOrchestrator.preview_memory` -> `_prepare_director_form_data`)
rather than hand-stamping `derive_segment_routing` onto an already-shaped
document.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from src.features.backends.backend_config import NativeBackendConfig
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
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
         "strength": 1.0, "media": {"relative_path": path}}
    m.update(over)
    return m


# A single fresh t2v opener, no media - `derive_segment_routing` resolves
# this to sub_type "t2v" (see derive_segment_sub_type's rule 4), so only the
# t2v `model_loader/wan22` pipe should be active.
DOC_T2V_ONLY = {
    "schema_version": 1, "mode": "director",
    "settings": _settings(duration=None, continuation={"source": "tail_frames", "overlap_frames": 4, "stitch": True}),
    "segments": [_segment("seg-0", "establishing shot", frames=81)],
    "media": [], "audio": [], "ic_lora": [],
}

# A single i2v segment (a leading start image) - resolves to sub_type "i2v",
# so only the i2v `model_loader/wan22` pipe should be active.
DOC_I2V_ONLY = {
    "schema_version": 1, "mode": "director",
    "settings": _settings(duration=None, continuation={"source": "tail_frames", "overlap_frames": 4, "stitch": True}),
    "segments": [_segment("seg-0", "a cat walks in", frames=81)],
    "media": [_media("first", "seg-0", "image.png")], "audio": [], "ic_lora": [],
}

# A chain that opens on a fresh t2v shot and continues (sub_type "chain",
# which draws from the i2v checkpoint set per `wan_model_set_for`) - needs
# BOTH loader pairs active, unlike either single-segment document above.
DOC_CHAIN_MIXED = {
    "schema_version": 1, "mode": "director",
    "settings": _settings(duration=None, continuation={"source": "tail_frames", "overlap_frames": 4, "stitch": True}),
    "segments": [
        _segment("seg-0", "establishing shot", frames=81),
        _segment("seg-1", "the story continues", frames=81),
    ],
    "media": [], "audio": [], "ic_lora": [],
}


@pytest.fixture(scope="module")
def wan_preset_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if p.path.rstrip("/").endswith("/Wan")), None)
    if template is None:
        pytest.skip("marketplace Wan preset not present")
    return template


def _orchestrator(wan_preset_template, storage_dir: str) -> GenerationOrchestrator:
    """A real `bind_form`/`PipelineBuilder`/`_prepare_director_form_data` path
    over the real Wan preset; everything else (backend selection, GPU) is
    faked - this test is about the ACTIVE MODEL SET, not backend routing."""
    preset_template_loader = Mock()
    preset_template_loader.load_preset_by_id = Mock(return_value=wan_preset_template)

    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    pipeline_builder = PipelineBuilder(preset_template_loader=Mock(), preset_processor=processor)

    backend = Mock()
    backend.backend_id = "native_1"
    backend.name = "Local"
    backend.engine = "native"
    backend.execution_device = "this_host_gpu"
    backend.config = NativeBackendConfig(id="native_1", name="Local", device="cpu", dtype="float32", gpu_max_vram=24)
    backend.start_generation = AsyncMock()

    backend_registry = Mock()
    backend_registry.select_backend_for_generation = Mock(return_value=backend)

    settings = Mock()
    settings.get_file_storage_directory = Mock(return_value=storage_dir)

    return GenerationOrchestrator(
        pipeline_builder=pipeline_builder,
        backend_registry=backend_registry,
        connection_hub=Mock(),
        settings=settings,
        output_processor=Mock(),
        preset_template_loader=preset_template_loader,
    )


def _ref(model_id: str) -> str:
    return f"model:{model_id}"


def _form_data(video_director_doc):
    return {
        "video_director": video_director_doc,
        "t2v_high_noise_model": _ref("t2v-high"),
        "t2v_low_noise_model": _ref("t2v-low"),
        "i2v_high_noise_model": _ref("i2v-high"),
        "i2v_low_noise_model": _ref("i2v-low"),
        "text_encoder": _ref("shared-te"),
        "vae": _ref("shared-vae"),
        "resolution": "832x480",
    }


def _make_request(video_director_doc):
    request = Mock()
    request.preset_id = "wan"
    request.form_data = _form_data(video_director_doc)
    request.mode = "video"
    request.form_name = None
    request.backend_id = None
    return request


def _model_ids(pipes):
    from src.features.generation.memory_advisory import active_model_ids
    return set(active_model_ids(pipes))


class TestWanDirectorAwarePreview:
    @pytest.mark.asyncio
    async def test_i2v_only_document_never_activates_the_unused_t2v_pair(self, wan_preset_template, tmp_path):
        orchestrator = _orchestrator(wan_preset_template, str(tmp_path))
        (tmp_path / "image.png").write_bytes(b"fake-image")

        preset_template_loader = orchestrator.preset_template_loader
        built = orchestrator.pipeline_builder.build_pipeline
        from src.features.generation.orchestrator import _prepare_director_form_data
        from src.features.forms.binding import bind_form

        bound = bind_form(wan_preset_template, "video", None, _form_data(DOC_I2V_ONLY), "user_1", storage_dir=str(tmp_path))
        prepared = _prepare_director_form_data(wan_preset_template, "video", bound.values, "user_1", orchestrator.settings)
        pipes = built(preset_id=wan_preset_template, form_data=prepared, mode="video", form_name=bound.form_name, user_id="user_1").pipes

        ids = _model_ids(pipes)
        assert {"i2v-high", "i2v-low"} <= ids
        assert "t2v-high" not in ids and "t2v-low" not in ids

    @pytest.mark.asyncio
    async def test_chain_document_needing_both_sets_activates_both_loader_pairs(self, wan_preset_template, tmp_path):
        from src.features.generation.orchestrator import _prepare_director_form_data
        from src.features.forms.binding import bind_form

        bound = bind_form(wan_preset_template, "video", None, _form_data(DOC_CHAIN_MIXED), "user_1", storage_dir=str(tmp_path))
        orchestrator = _orchestrator(wan_preset_template, str(tmp_path))
        prepared = _prepare_director_form_data(wan_preset_template, "video", bound.values, "user_1", orchestrator.settings)
        pipes = orchestrator.pipeline_builder.build_pipeline(
            preset_id=wan_preset_template, form_data=prepared, mode="video", form_name=bound.form_name, user_id="user_1",
        ).pipes

        ids = _model_ids(pipes)
        assert {"t2v-high", "t2v-low", "i2v-high", "i2v-low"} <= ids

    @pytest.mark.asyncio
    async def test_switching_the_shot_from_i2v_only_to_chain_changes_the_active_set(self, wan_preset_template, tmp_path):
        """A shot/document change alone - same form otherwise - must change
        which checkpoints are active, exactly as it would for a real
        generation of either document."""
        (tmp_path / "image.png").write_bytes(b"fake-image")
        from src.features.generation.orchestrator import _prepare_director_form_data
        from src.features.forms.binding import bind_form

        orchestrator = _orchestrator(wan_preset_template, str(tmp_path))

        def _active_ids(doc):
            bound = bind_form(wan_preset_template, "video", None, _form_data(doc), "user_1", storage_dir=str(tmp_path))
            prepared = _prepare_director_form_data(wan_preset_template, "video", bound.values, "user_1", orchestrator.settings)
            pipes = orchestrator.pipeline_builder.build_pipeline(
                preset_id=wan_preset_template, form_data=prepared, mode="video", form_name=bound.form_name, user_id="user_1",
            ).pipes
            return _model_ids(pipes)

        i2v_only_ids = _active_ids(DOC_I2V_ONLY)
        chain_ids = _active_ids(DOC_CHAIN_MIXED)

        assert i2v_only_ids != chain_ids
        assert "t2v-high" not in i2v_only_ids and "t2v-high" in chain_ids

    @pytest.mark.asyncio
    async def test_preview_memory_end_to_end_reports_the_same_active_set(self, wan_preset_template, tmp_path):
        """The full `preview_memory` operation (real bind_form + Director
        preparation + PipelineBuilder inside it) must report the SAME
        checkpoint-based coverage the direct pipeline build above does -
        never falling back to "active model set unresolved" for a
        perfectly valid document."""
        orchestrator = _orchestrator(wan_preset_template, str(tmp_path))

        result = await orchestrator.preview_memory(_make_request(DOC_CHAIN_MIXED), "user_1")

        assert result["coverage"]["active_set_resolved"] is True
        known_and_unknown = {k["ref"] for k in result["coverage"]["known"]} | set(result["coverage"]["unknown"])
        assert {"t2v-high", "t2v-low", "i2v-high", "i2v-low"} <= known_and_unknown

    @pytest.mark.asyncio
    async def test_invalid_director_document_reports_unresolved_not_no_references(self, wan_preset_template, tmp_path):
        """A Director document malformed enough to fail its own validation
        must surface as `active_set_resolved: False` (and never raise out of
        `preview_memory`) - the UI reads this as "the request could not be
        resolved", never "no model references", even though the form still
        carries plenty of `model:<id>` refs."""
        orchestrator = _orchestrator(wan_preset_template, str(tmp_path))
        broken_doc = {"schema_version": 999, "mode": "director"}  # missing required keys

        result = await orchestrator.preview_memory(_make_request(broken_doc), "user_1")

        assert result["coverage"]["active_set_resolved"] is False
        assert any("could not be resolved" in note for note in result["coverage"]["uncertainty"])
