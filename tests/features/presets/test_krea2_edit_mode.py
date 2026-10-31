from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.preset_render import build_fixture_form_data
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

GENERAL_V2_SHA256 = "fb5315477444c8df6dea2ad347a852bd90e97ab0f9109f4100a39714a7a09356"
IDENTITY_V2_SHA256 = "c53843f9451963a6cb3c22dc4975205056b93a6575fb9347dd2bc6e055ef9877"


@pytest.fixture(scope="module")
def krea2():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/Krea2")), None)
    if template is None:
        pytest.fail("marketplace/Krea2 preset not present")
    return template


def _pipes(template, form_over=None):
    form_data = build_fixture_form_data(template, "edit")
    if form_over:
        form_data.update(form_over)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    return processor.process(template, {"prompts": [], "mode": "edit", "form_data": form_data})


def _by_id(pipes, pipe_id):
    return next(p for p in pipes if p.get("id") == pipe_id)


def _wired(pipe):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in pipe["input"]}


def _generator(pipes):
    return next(p for p in pipes if p["name"] == "generator/krea2_edit")


def _fields(fields):
    for field in fields:
        yield field
        if isinstance(field.children, list):
            yield from _fields(field.children)


def test_edit_is_a_core_mode(krea2):
    assert "edit" in krea2.modes
    assert krea2.modes["edit"].source_plugin is None


def test_edit_wires_sources_masks_and_the_edit_generator(krea2):
    pipes = _pipes(krea2)
    generator = _generator(pipes)
    wired = _wired(generator)
    assert ("image", "media_loader", "image") in wired
    assert ("image_b", "media_loader_b", "image") in wired
    assert ("mask", "mask_loader", "image") in wired
    assert ("mask_b", "mask_loader_b", "image") in wired
    assert ("conditioning", "prompt_encoder", "conditioning") in wired


def test_edit_lora_is_applied_ahead_of_the_extra_loras(krea2):
    extra = [{"model": "/m/style.safetensors", "strength": 0.5}]
    pipes = _pipes(krea2, {"edit_lora": "/m/edit.safetensors", "edit_lora_strength": 0.9, "loras": extra})
    loras = next(p for p in pipes if p["name"] == "model_loader/krea2")["config"]["loras"]
    assert [(entry["file_path"], entry["weight"]) for entry in loras] == [
        ("/m/edit.safetensors", 0.9),
        ("/m/style.safetensors", 0.5),
    ]


def test_edit_task_picks_the_automatic_reference_dials(krea2):
    edit = _generator(_pipes(krea2, {"edit_task": "edit"}))["config"]
    person = _generator(_pipes(krea2, {"edit_task": "person"}))["config"]
    assert (edit["ref_boost"], edit["ref_position"]) == (4.0, "aligned")
    assert (person["ref_boost"], person["ref_position"]) == (2.0, "beside")


def test_explicit_reference_dials_override_the_task_defaults(krea2):
    config = _generator(_pipes(krea2, {"edit_task": "person", "ref_boost": 1.5, "ref_position": "aligned"}))["config"]
    assert config["ref_boost"] == 1.5
    assert config["ref_position"] == "aligned"


def test_scene_placement_and_fidelity_pass_through(krea2):
    config = _generator(_pipes(krea2, {"ref_position_a": "beside", "ref_boost_a": 1.5}))["config"]
    assert config["ref_position_a"] == "beside"
    assert config["ref_boost_a"] == 1.5


def test_vision_grounding_is_on_by_default_and_feeds_the_encoder(krea2):
    pipes = _pipes(krea2)
    assert next(p for p in pipes if p["name"] == "model_loader/krea2")["config"]["vision"] is True
    encoder = next(p for p in pipes if p["name"] == "prompt_encoder")
    assert ("image", "media_loader", "image") in _wired(encoder)


def test_vision_grounding_off_detaches_the_image_from_the_encoder(krea2):
    pipes = _pipes(krea2, {"vision_grounded_encode": False})
    assert next(p for p in pipes if p["name"] == "model_loader/krea2")["config"]["vision"] is False
    encoder = next(p for p in pipes if p["name"] == "prompt_encoder")
    assert ("image", "media_loader", "image") not in _wired(encoder)


def test_cfg_above_one_reaches_the_encoder_and_the_generator(krea2):
    pipes = _pipes(krea2, {"cfg": 3.0})
    assert next(p for p in pipes if p["name"] == "prompt_encoder")["config"]["guidance_scale"] == 3.0
    assert _generator(pipes)["config"]["guidance"] == 3.0


def test_edit_lora_recommendations_carry_pinned_hashes(krea2):
    fields = {f.name: f for f in _fields(krea2.modes["edit"].forms[0].fields) if f.name}
    pinned = {rec["name"]: rec["sha256"] for rec in fields["edit_lora"].configuration["recommendations"]}
    assert pinned["krea2_general_edit_v2.safetensors"] == GENERAL_V2_SHA256
    assert pinned["krea2_identity_edit_v2.safetensors"] == IDENTITY_V2_SHA256
    assert all(len(sha) == 64 for sha in pinned.values())


def test_edit_lora_is_required(krea2):
    fields = {f.name: f for f in _fields(krea2.modes["edit"].forms[0].fields) if f.name}
    assert fields["edit_lora"].required is True
