from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.pipelines.catalog import PipeCatalog
from src.platform.security.user import AccountType
from src.platform.templating.processor import TemplateProcessor

PRESET_DIR = Path("content/presets/marketplace/ModelTools")
MODES = ["quantize", "merge_into_model", "merge_and_quantize", "merge_loras"]
LORA_ROWS = [
    {"model": "/m/loras/a.safetensors", "strength": 0.8},
    {"model": "/m/loras/b.safetensors", "strength": 1.0},
]


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if str(p.path).replace("\\", "/").endswith("marketplace/ModelTools")), None)
    assert found is not None
    return found


def processor(users=None):
    return PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
        user_resolver=(users or {}).get,
    )


def fields_of(mode):
    out = {}
    tabs = list((PRESET_DIR / "modes" / mode / "tabs").glob("*.yml"))
    if mode == "merge_and_quantize":
        tabs.append(PRESET_DIR / "modes/quantize/tabs/advanced.yml")
    for tab in tabs:
        for field in yaml.safe_load(tab.read_text())["fields"]:
            out[field["name"]] = field
    return out


def render(template, mode, form, user_id=None, users=None):
    data = {"prompts": [], "mode": mode, "form_data": form}
    pipes = processor(users).process(template, data, user_id=user_id)
    return next(p for p in pipes if p["name"] == "model_tools")["config"]


FORMS = {
    "quantize": {"model": "/m/diffusion_models/dit.safetensors", "keep_patterns": "blocks.0.*", "output_name": "x", "overwrite": True},
    "merge_into_model": {"model": "/m/diffusion_models/dit.safetensors", "loras": LORA_ROWS},
    "merge_and_quantize": {"model": "/m/diffusion_models/dit.safetensors", "loras": LORA_ROWS, "quant_format": "int8_convrot"},
    "merge_loras": {"loras": LORA_ROWS, "merge_method": "reduce", "target_rank": 16, "min_energy": 0.8, "lora_dtype": "BF16"},
}


def test_the_preset_is_a_promptless_utility_with_the_four_modes(template):
    assert template.category == "utility"
    assert template.engine == "native"
    assert list(template.modes) == MODES
    assert set(template.vars["promptless_modes"]) == set(MODES)


@pytest.mark.parametrize("mode", MODES)
def test_every_mode_runs_the_model_tools_pipe_in_its_own_mode(template, mode):
    config = render(template, mode, FORMS[mode])
    assert config["mode"] == mode


@pytest.mark.parametrize("mode", MODES)
def test_every_rendered_key_is_a_declared_pipe_option(template, mode):
    pipe = PipeCatalog("src/pipelines/pipes", "pipes/custom").get_pipe("model_tools")
    declared = {spec.name for spec in pipe.configuration()}
    assert set(render(template, mode, FORMS[mode])) <= declared


@pytest.mark.parametrize("mode", MODES)
def test_admin_status_comes_from_the_server_not_the_form(template, mode):
    admin = Mock(account_type=AccountType.ADMIN)
    user = Mock(account_type=AccountType.USER)
    users = {"a": admin, "u": user}
    assert render(template, mode, FORMS[mode], "a", users)["is_admin"] is True
    assert render(template, mode, FORMS[mode], "u", users)["is_admin"] is False
    assert render(template, mode, FORMS[mode], None, users)["is_admin"] is False
    spoofed = {**FORMS[mode], "is_admin": True, "runtime": {"user": {"is_admin": True}}}
    assert render(template, mode, spoofed, "u", users)["is_admin"] is False


def test_form_values_reach_the_pipe(template):
    quantize = render(template, "quantize", FORMS["quantize"])
    assert quantize["model"] == "/m/diffusion_models/dit.safetensors"
    assert quantize["keep_patterns"] == "blocks.0.*"
    assert quantize["output_name"] == "x"
    assert quantize["overwrite"] is True
    merged = render(template, "merge_loras", FORMS["merge_loras"])
    assert merged["loras"] == LORA_ROWS
    assert (merged["merge_method"], merged["target_rank"], merged["min_energy"], merged["lora_dtype"]) == ("reduce", 16, 0.8, "BF16")


def test_optional_fields_fall_back_to_safe_values(template):
    config = render(template, "quantize", {"model": "/m/diffusion_models/dit.safetensors"})
    assert config["output_name"] == ""
    assert config["overwrite"] is False
    assert config["keep_patterns"] == ""
    assert config["quant_format"] == "fp8_e4m3"


@pytest.mark.parametrize("mode", MODES)
def test_every_mode_has_an_output_name_and_an_overwrite_box(mode):
    fields = fields_of(mode)
    assert fields["output_name"]["default"] == ""
    assert fields["overwrite"]["type"] == "checkbox"
    assert fields["overwrite"]["default"] is False


@pytest.mark.parametrize("mode", MODES)
def test_the_name_pattern_rejects_separators_and_reserved_characters(mode):
    pattern = re.compile(fields_of(mode)["output_name"]["configuration"]["pattern"])
    for good in ["", "flux-fp8", "a+b", "v1.5 (final)", "naïve"]:
        assert pattern.search(good), good
    for bad in ["a/b", "a\\b", "x:y", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b"]:
        assert not pattern.search(bad), bad


def test_model_pickers_offer_the_dit_and_text_encoder_types_together():
    quantize = fields_of("quantize")["model"]["configuration"]
    assert quantize["model_types"] == ["diffusion_model", "unet", "checkpoint", "text_encoder"]
    assert quantize["unquantized_only"] is True
    for mode in ("merge_into_model", "merge_and_quantize"):
        config = fields_of(mode)["model"]["configuration"]
        assert config["model_types"] == ["diffusion_model", "unet", "checkpoint"]
        assert "unquantized_only" not in config


@pytest.mark.parametrize("mode", ["merge_into_model", "merge_and_quantize", "merge_loras"])
def test_lora_lists_reuse_the_picker_with_strengths_and_no_row_extras(mode):
    field = fields_of(mode)["loras"]
    assert field["type"] == "lora_picker"
    config = field["configuration"]
    assert config["model_type"] == "lora"
    assert config["max_items"] == 8
    assert "row_fields" not in config
    assert config["strength_min"] < 0 < config["strength_max"]


def test_only_the_quantize_modes_offer_keep_patterns():
    assert "keep_patterns" in fields_of("quantize")
    assert "keep_patterns" in fields_of("merge_and_quantize")
    assert "keep_patterns" not in fields_of("merge_into_model")
    assert "keep_patterns" not in fields_of("merge_loras")


@pytest.mark.parametrize("mode", ["quantize", "merge_and_quantize"])
def test_the_quantize_modes_pick_a_format_the_pipe_accepts(template, mode):
    from src.pipelines.pipes.model_tools.main import QUANT_FORMATS

    field = fields_of(mode)["quant_format"]
    assert field["type"] == "select"
    assert field["default"] == "fp8_e4m3"
    options = field["configuration"]["options"]
    assert [o["value"] for o in options] == QUANT_FORMATS
    assert all(o["label"] and o["sub_label"] for o in options)
    config = render(template, mode, {**FORMS[mode], "quant_format": "nvfp4"})
    assert config["quant_format"] == "nvfp4"


def test_the_bake_and_merge_modes_have_no_format():
    assert "quant_format" not in fields_of("merge_into_model")
    assert "quant_format" not in fields_of("merge_loras")


def test_merge_loras_exposes_method_rank_and_energy():
    fields = fields_of("merge_loras")
    assert [o["value"] for o in fields["merge_method"]["configuration"]["options"]] == ["concat", "reduce"]
    assert {"target_rank", "min_energy"} <= set(fields)


@pytest.mark.parametrize("mode", MODES)
def test_backend_injected_keys_do_not_break_pipe_validation(template, mode):
    from src.features.generation.engine import validate_pipe_configuration

    pipe = PipeCatalog("src/pipelines/pipes", "pipes/custom").get_pipe("model_tools")
    config = render(template, mode, FORMS[mode])
    for key, value in {"device": "cuda:0", "dtype": "bfloat16", "vram_limit_gb": 24}.items():
        config.setdefault(key, value)
    validated = validate_pipe_configuration(pipe, config)
    assert validated["mode"] == mode


@pytest.mark.parametrize("mode", MODES)
def test_the_preset_asks_for_admin_authority_and_takes_it_from_the_caller_not_the_owners_row(template, mode):
    data = {"prompts": [], "mode": mode, "form_data": FORMS[mode]}
    users = {"a": Mock(account_type=AccountType.ADMIN)}

    denied = processor(users).process(template, data, user_id="a", acts_as_admin=False)
    granted = processor(users).process(template, data, user_id="a", acts_as_admin=True)

    assert denied.used_admin_authority is True and granted.used_admin_authority is True
    assert next(p for p in denied if p["name"] == "model_tools")["config"]["is_admin"] is False
    assert next(p for p in granted if p["name"] == "model_tools")["config"]["is_admin"] is True
