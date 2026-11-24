import pytest
import yaml

from tests.features.presets._sdxl_modes import (
    SDXL_ROOT,
    is_enabled,
    load_sdxl,
    mode_form,
    pipe,
    provider,
    render,
    wired,
)

MODES = ["txt2img", "control"]
TAB = "reference"
SHA256 = {
    "ip-adapter-plus_sdxl_vit-h.safetensors": "3f5062b8400c94b7159665b21ba5c62acdcd7682262743d7f2aefedef00e6581",
    "ip-adapter_sdxl_vit-h.safetensors": "ebf05d918348aec7abb02a5e9ecef77e0aaea6914a5c4ea13f50d45eb1681831",
    "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors": "6ca9667da1ca9e0b0f75e46bb030f7e011f44f86cbfb8d5a36590fcd7507b030",
}


@pytest.fixture(scope="module")
def sdxl():
    return load_sdxl()


def _reference_tab():
    return yaml.safe_load((SDXL_ROOT / "modes" / "txt2img" / "tabs" / f"{TAB}.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("mode", MODES)
def test_the_reference_tab_is_offered_in_the_modes_that_use_the_adapter(mode):
    anchors = []

    def walk(items):
        for item in items or []:
            if item.get("type") == "tab":
                anchors.append(item["anchor"])
            walk(item.get("children") if isinstance(item.get("children"), list) else None)

    walk(mode_form(mode)["fields"])
    assert "reference" in anchors


@pytest.mark.parametrize("mode", MODES)
def test_enabled_reference_reaches_the_generator(sdxl, mode):
    pipes = render(sdxl, mode, ip_adapter_enabled=True, ip_adapter_scale=0.45)
    loader = pipe(pipes, "reference_loader")
    assert is_enabled(loader)
    assert loader["config"]["media"][0]["type"] == "image"
    generator = pipe(pipes, "generator")
    assert provider(generator, "ip_image") == "reference_loader"
    settings = generator["config"]["ip_adapter"]
    assert settings["adapter"].endswith("ip_adapter_model.safetensors")
    assert settings["image_encoder"].endswith("ip_adapter_encoder.safetensors")
    assert settings["scale"] == 0.45


@pytest.mark.parametrize("mode", MODES)
def test_a_disabled_reference_sends_the_generator_no_adapter(sdxl, mode):
    pipes = render(sdxl, mode, ip_adapter_enabled=False)
    assert not is_enabled(pipe(pipes, "reference_loader"))
    assert pipe(pipes, "generator")["config"]["ip_adapter"]["adapter"] == ""
    assert pipe(pipes, "generator")["config"]["ip_adapter"]["image_encoder"] == ""


def test_the_txt2img_controlnet_media_loader_is_not_taken_over_by_the_reference_loader(sdxl):
    pipes = render(sdxl, "txt2img", ip_adapter_enabled=True, enable_controlnet=True, controlnet_auto_preprocess=False)
    assert provider(pipe(pipes, "generator"), "control_image") == "media_loader"
    assert ("ip_image", "reference_loader", "image") in wired(pipe(pipes, "generator"))


def test_the_reference_is_an_experimental_gate_that_starts_off():
    (gate,) = _reference_tab()["fields"]
    assert gate["type"] == "gate"
    assert gate["name"] == "ip_adapter_enabled"
    assert gate["default"] is False
    assert gate["configuration"]["experimental"] is True
    assert {child["name"] for child in gate["children"]} == {
        "ip_adapter_image", "ip_adapter_scale", "ip_adapter_model", "ip_adapter_encoder",
    }


def test_adapters_and_encoder_are_offered_with_verified_hashes_and_links():
    fields = {child["name"]: child for child in _reference_tab()["fields"][0]["children"]}
    for name in ("ip_adapter_model", "ip_adapter_encoder"):
        assert fields[name]["configuration"]["model_type"] == "model_patch"
    recs = fields["ip_adapter_model"]["configuration"]["recommendations"] + fields["ip_adapter_encoder"]["configuration"]["recommendations"]
    assert {rec["name"]: rec["sha256"] for rec in recs} == SHA256
    assert all(rec["link"].startswith("https://huggingface.co/h94/IP-Adapter/resolve/main/") for rec in recs)


def test_the_default_strength_is_the_documented_midpoint():
    fields = {child["name"]: child for child in _reference_tab()["fields"][0]["children"]}
    assert fields["ip_adapter_scale"]["default"] == 0.6
    assert fields["ip_adapter_scale"]["configuration"]["max"] >= 1.0


@pytest.mark.parametrize("mode", ["restyle", "inpaint", "upscale", "refine_faces"])
def test_other_modes_gain_no_reference_inputs(mode):
    text = "".join(path.read_text(encoding="utf-8") for path in (SDXL_ROOT / "modes" / mode).rglob("*.yml"))
    assert "ip_adapter" not in text
