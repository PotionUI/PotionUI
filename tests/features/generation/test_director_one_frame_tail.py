from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.features.forms.binding import bind_form
from src.features.generation.orchestrator import GenerationOrchestrator, _prepare_director_form_data
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

FIVE_B_SHA256 = "456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e"
A14B_I2V_SHA256 = "6122e79d55e0f235698d11d657f3b196c5273c830da00b2b013c5a048d5e6a42"


def _segment(seg_id, prompt, frames):
    return {
        "id": seg_id, "prompt": prompt, "negative_prompt": "", "start": None, "end": None,
        "frames": frames, "seed": None, "steps": None, "cfg": None, "loras": None,
    }


CHAIN = {
    "schema_version": 1, "mode": "director",
    "settings": {
        "fps": 24, "duration": None, "resolution": "", "seed": 7,
        "continuation": {"source": "tail_frames", "overlap_frames": 4, "stitch": True},
    },
    "segments": [_segment("seg-0", "a workshop", 49), _segment("seg-1", "the bottle glows", 49)],
    "media": [], "audio": [], "ic_lora": [],
}


@pytest.fixture(scope="module")
def wan_preset_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).name == "Wan"), None)
    if template is None:
        pytest.fail("marketplace Wan preset not present")
    return template


def _form(**over):
    form = {
        "video_director": CHAIN,
        "t2v_high_noise_model": "model:t2v",
        "i2v_high_noise_model": "model:i2v",
        "text_encoder": "model:te",
        "vae": "model:vae",
        "resolution": "832x480",
    }
    form.update(over)
    return form


def _sha_of(i2v_sha256):
    return {"model:t2v": FIVE_B_SHA256, "model:i2v": i2v_sha256}.get


def _prepare(template, tmp_path, form, i2v_sha256):
    settings = Mock()
    settings.get_file_storage_directory = Mock(return_value=str(tmp_path))
    bound = bind_form(template, "video", None, form, "user_1", storage_dir=str(tmp_path))
    return bound, _prepare_director_form_data(
        template, "video", bound.values, "user_1", settings, model_sha256=_sha_of(i2v_sha256),
    )


def _chain_config(template, prepared, bound):
    processor = PresetProcessor(template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock())
    builder = PipelineBuilder(preset_template_loader=Mock(), preset_processor=processor)
    pipes = builder.build_pipeline(
        preset_id=template, form_data=prepared, mode="video", form_name=bound.form_name, user_id="user_1",
    ).pipes
    return next(pipe for pipe in pipes if pipe["name"] == "generator/chain_video_wan22")["config"]


def test_a_5b_director_chain_carries_one_motion_latent_by_default(wan_preset_template, tmp_path):
    bound, prepared = _prepare(wan_preset_template, tmp_path, _form(), FIVE_B_SHA256)

    assert bound.values["svi_motion_latent_count"] == 2
    assert prepared["svi_motion_latent_count"] == 1
    assert prepared["video_director"]["settings"]["timing_profile"] == {"motion_latent_count": 1}
    assert int(_chain_config(wan_preset_template, prepared, bound)["motion_latent_count"]) == 1


def test_the_planner_of_a_5b_chain_plans_the_one_frame_tail_the_generator_runs(wan_preset_template, tmp_path):
    form = _form(video_director={**CHAIN, "render": {"scope": "shots", "shot_ids": ["seg-0", "seg-1"]}})
    _, five_b = _prepare(wan_preset_template, tmp_path, form, FIVE_B_SHA256)
    _, one_latent_14b = _prepare(wan_preset_template, tmp_path, {**form, "svi_motion_latent_count": 1}, A14B_I2V_SHA256)
    _, default_14b = _prepare(wan_preset_template, tmp_path, form, A14B_I2V_SHA256)

    assert five_b["video_director"] == one_latent_14b["video_director"]
    assert five_b["video_director"] != default_14b["video_director"]


def test_a_motion_latent_count_set_above_the_default_is_kept_for_the_5b_and_refused_by_the_generator(wan_preset_template, tmp_path):
    bound, prepared = _prepare(wan_preset_template, tmp_path, _form(svi_motion_latent_count=3), FIVE_B_SHA256)

    assert prepared["svi_motion_latent_count"] == 3
    assert prepared["video_director"]["settings"]["timing_profile"] == {"motion_latent_count": 3}
    assert int(_chain_config(wan_preset_template, prepared, bound)["motion_latent_count"]) == 3


def test_an_a14b_i2v_chain_keeps_the_form_default_of_two_motion_latents(wan_preset_template, tmp_path):
    bound, prepared = _prepare(wan_preset_template, tmp_path, _form(), A14B_I2V_SHA256)

    assert prepared["svi_motion_latent_count"] == 2
    assert prepared["video_director"]["settings"]["timing_profile"] == {"motion_latent_count": 2}
    assert int(_chain_config(wan_preset_template, prepared, bound)["motion_latent_count"]) == 2


def test_the_5b_in_the_t2v_set_only_leaves_the_default(wan_preset_template, tmp_path):
    _, prepared = _prepare(wan_preset_template, tmp_path, _form(), None)

    assert prepared["svi_motion_latent_count"] == 2


def test_the_wan_preset_names_the_5b_recommendation_of_the_i2v_picker_as_its_one_frame_tail_model(wan_preset_template):
    from src.features.forms.binding import form_field_index

    rule = wan_preset_template.vars["video_director"]["timing"]["one_frame_tail"]
    picker = form_field_index(wan_preset_template, "video")[rule["model_field"]]
    five_b = [item for item in picker.configuration["recommendations"] if "ti2v_5B" in item["name"]]

    assert rule["model_field"] == "i2v_high_noise_model"
    assert rule["sha256"] == [item["sha256"] for item in five_b] == [FIVE_B_SHA256]


def test_the_orchestrator_reads_the_sha256_of_a_model_reference_from_the_model_library():
    orchestrator = GenerationOrchestrator(
        pipeline_builder=Mock(), backend_registry=Mock(), connection_hub=Mock(), settings=Mock(),
        output_processor=Mock(), preset_template_loader=Mock(),
    )
    record = Mock(sha256=FIVE_B_SHA256.upper())

    with patch("src.features.models.repository.model_repo.get_by_id", return_value=record) as get_by_id:
        assert orchestrator._model_sha256("model:01ABC") == FIVE_B_SHA256
        assert orchestrator._model_sha256("/models/wan.safetensors") is None
        assert orchestrator._model_sha256(None) is None

    get_by_id.assert_called_once_with("01ABC", include_providers=False, include_tags=False)

    with patch("src.features.models.repository.model_repo.get_by_id", side_effect=RuntimeError("no database")):
        assert orchestrator._model_sha256("model:01ABC") is None
