from __future__ import annotations

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets import PresetTemplateLoader

VALID_ABC = "X:1\nT:Test\nM:4/4\nL:1/8\nK:C\n\"C\"E2 G2 c2 G2 |]\n"
ABC_MESSAGE = "ABC needs an X: (reference number) and a K: (key) header line"


@pytest.fixture(scope="module")
def yue2_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if str(p.path).rstrip("/").endswith("YuE2")), None)
    if template is None:
        pytest.skip("YuE2 preset not present")
    return template


def _bind(template, **form):
    form_data = {
        "style": "folk, acoustic guitar",
        "model": "/models/yue2_3b_bf16.safetensors",
        "vae": "/models/yue2_vae_fp32.safetensors",
        **form,
    }
    return bind_form(template, "song", form_name=None, raw_form_data=form_data, user_id=None, storage_dir=None)


def test_header_less_abc_is_rejected_with_a_field_error(yue2_template):
    with pytest.raises(FormBindingError) as excinfo:
        _bind(yue2_template, cot="full", abc="C D E F | G A B c |")
    assert excinfo.value.field_errors == {"abc": [ABC_MESSAGE]}


@pytest.mark.parametrize("abc", [
    "K:C\nC D E F |",
    "X:1\nT:No key\nC D E F |",
    "T:X: inline only\nK:C\n",
])
def test_abc_missing_either_header_line_is_rejected(yue2_template, abc):
    with pytest.raises(FormBindingError) as excinfo:
        _bind(yue2_template, cot="melody", abc=abc)
    assert "abc" in excinfo.value.field_errors


def test_abc_with_both_headers_binds(yue2_template):
    bound = _bind(yue2_template, cot="full", abc=VALID_ABC)
    assert bound.values["abc"] == VALID_ABC


def test_empty_abc_binds(yue2_template):
    bound = _bind(yue2_template, cot="full", abc="")
    assert bound.values["abc"] == ""


def test_hidden_abc_is_not_checked_when_cot_is_off(yue2_template):
    bound = _bind(yue2_template, cot="off", abc="leftover text")
    assert bound.values["cot"] == "off"


def test_abc_field_renders_as_tall_mono_textarea(yue2_template):
    from src.features.forms.binding import _expand_form_fields, _flatten_fields

    mode = yue2_template.modes["song"]
    index = {}
    _flatten_fields(_expand_form_fields(mode.forms[0].fields, yue2_template), index)
    config = index["abc"].configuration
    assert index["abc"].type == "string"
    assert config["input_type"] == "textarea"
    assert config["mono"] is True
    assert config["rows"] == 12
    assert config["placeholder"].startswith("X:1\n")
