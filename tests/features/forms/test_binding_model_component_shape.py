from __future__ import annotations

from pathlib import Path

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.models.form_refs import make_model_ref
from src.features.presets import PresetTemplateLoader

MODEL_FIELDS = ("diffusion_model", "text_encoder", "vae")


@pytest.fixture(scope="module")
def qwen21():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    matches = [p for p in loader.presets if Path(p.path).name == "QwenImage-2.1"]
    assert len(matches) == 1
    return matches[0]


def _wire(**models):
    data = {"source_image": "uploads/photo.png"}
    data.update(models)
    return data


def _component(path):
    return {"modelPath": path, "tagFilters": []}


def test_an_empty_model_in_component_shape_is_refused_like_an_empty_string(qwen21):
    models = {name: make_model_ref(f"M-{name}") for name in MODEL_FIELDS}
    models["vae"] = _component("")

    with pytest.raises(FormBindingError) as refused:
        bind_form(qwen21, "edit", None, _wire(**models), "user_1")

    assert set(refused.value.field_errors) == {"vae"}


def test_a_model_in_component_shape_binds_to_its_path(qwen21):
    ref = make_model_ref("M-vae")
    models = {name: make_model_ref(f"M-{name}") for name in MODEL_FIELDS}
    models["vae"] = _component(ref)

    bound = bind_form(qwen21, "edit", None, _wire(**models), "user_1")

    assert bound.values["vae"] == ref
