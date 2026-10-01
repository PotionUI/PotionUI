from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

PRESET_DIR = Path("content/presets/marketplace/QwenImage-2.1")


@pytest.fixture(scope="module")
def qwen_image21_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "marketplace/QwenImage-2.1" in str(p.path)), None)
    if template is None:
        pytest.skip("marketplace/QwenImage-2.1 preset not present")
    return template


def _process(qwen_image21_template, form_over: dict | None = None, mode: str = "txt2img"):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/qwen21_dit.safetensors",
        "text_encoder": "/models/qwen21_te.safetensors",
        "vae": "/models/qwen21_vae.safetensors",
        "resolution": "1024x1024",
    }
    if mode == "edit":
        form_data["source_image"] = ["/uploads/source.png"]
    if mode == "control":
        form_data["control_model"] = "/models/fun_control.safetensors"
        form_data["source_image"] = "/uploads/room.png"
    if form_over:
        form_data.update(form_over)
    bound = bind_form(
        qwen_image21_template, mode, form_name=None, raw_form_data=form_data,
        user_id=None, storage_dir=None,
    )
    generation_data = {"prompts": [], "mode": mode, "form_data": dict(bound.values)}
    return processor.process(qwen_image21_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def test_model_loader_and_generator_pipe_names_are_the_2_1_family(qwen_image21_template):
    pipes = _process(qwen_image21_template)
    assert _pipe(pipes, "model_loader/qwen_image21") is not None
    assert _pipe(pipes, "generator/qwen_image21") is not None


def test_blank_shift_form_field_is_omitted_from_generator_config(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template), "generator/qwen_image21")["config"]
    assert cfg.get("shift") in (None, "")


def test_shift_override_reaches_the_generator(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template, {"shift": 1.5}), "generator/qwen_image21")["config"]
    assert float(cfg["shift"]) == 1.5


def test_default_speed_profile_reaches_steps_and_guidance(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template), "generator/qwen_image21")["config"]
    assert int(cfg["steps"]) == 40
    assert float(cfg["guidance"]) == 4.0


def test_txt2img_edit_and_control_modes_are_declared(qwen_image21_template):
    assert list(qwen_image21_template.modes.keys()) == ["txt2img", "edit", "control"]


def test_edit_mode_loader_wires_vision_true(qwen_image21_template):
    pipes = _process(qwen_image21_template, mode="edit")
    cfg = _pipe(pipes, "model_loader/qwen_image21")["config"]
    assert cfg["vision"] is True


def test_edit_mode_generator_config(qwen_image21_template):
    pipes = _process(qwen_image21_template, mode="edit")
    cfg = _pipe(pipes, "generator/qwen_image21")["config"]
    assert cfg["mode"] == "edit"


def test_edit_mode_media_loader_and_prompt_encoder_share_the_source_image(qwen_image21_template):
    pipes = _process(qwen_image21_template, mode="edit")
    media = _pipe(pipes, "media_loader")["config"]["media"]
    assert len(media) == 1
    assert media[0]["path"] == "/uploads/source.png"


def test_step_cache_defaults_off_in_txt2img(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template), "generator/qwen_image21")["config"]
    step_cache = cfg["step_cache"]
    assert float(step_cache["rel_threshold"]) == 0.0
    assert int(step_cache["warmup_steps"]) == 4
    assert int(step_cache["max_consecutive_skips"]) == 3


def test_step_cache_form_values_reach_the_generator_in_txt2img(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template, {
        "step_cache_threshold": 0.1,
        "step_cache_warmup_steps": 6,
        "step_cache_max_skips": 2,
    }), "generator/qwen_image21")["config"]
    step_cache = cfg["step_cache"]
    assert float(step_cache["rel_threshold"]) == 0.1
    assert int(step_cache["warmup_steps"]) == 6
    assert int(step_cache["max_consecutive_skips"]) == 2


def test_step_cache_form_values_reach_the_generator_in_edit(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template, {
        "step_cache_threshold": 0.12,
        "step_cache_warmup_steps": 5,
        "step_cache_max_skips": 1,
    }, mode="edit"), "generator/qwen_image21")["config"]
    step_cache = cfg["step_cache"]
    assert float(step_cache["rel_threshold"]) == 0.12
    assert int(step_cache["warmup_steps"]) == 5
    assert int(step_cache["max_consecutive_skips"]) == 1


def _enabled(pipes, name):
    return bool(_pipe(pipes, name)["enabled"])


def _input_source(pipes, consumer, param):
    return next(i["provider"] for i in _pipe(pipes, consumer)["input"] if i["name"] == param)


def test_control_mode_defaults_to_the_distilled_settings(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template, mode="control"), "generator/qwen_image21")["config"]
    assert cfg["mode"] == "control"
    assert int(cfg["steps"]) == 40
    assert float(cfg["guidance"]) == 1.0
    assert float(cfg["control_strength"]) == 1.0


def test_control_mode_loads_the_fun_controlnet_with_the_dit(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template, mode="control"), "model_loader/qwen_image21")["config"]
    assert cfg["control_model"]["file_path"] == "/models/fun_control.safetensors"


@pytest.mark.parametrize("mode", ["txt2img", "edit"])
def test_other_modes_never_load_the_fun_controlnet(qwen_image21_template, mode):
    cfg = _pipe(_process(qwen_image21_template, mode=mode), "model_loader/qwen_image21")["config"]
    assert "control_model" not in cfg


def _control_case(template, **form):
    pipes = _process(template, form, mode="control")
    providers = {i["name"]: i["provider"] for i in _pipe(pipes, "generator/qwen_image21")["input"]}
    enabled = {
        name: _enabled(pipes, name)
        for name in ("source_loader", "inpaint_loader", "mask_loader", "guide_loader", "controlnet_preprocessor")
    }
    return pipes, providers, enabled


def test_a_guide_alone_reads_the_main_image_through_the_preprocessor(qwen_image21_template):
    pipes, providers, enabled = _control_case(qwen_image21_template, guide="openpose", control_strength=0.7)
    assert enabled == {"source_loader": True, "inpaint_loader": False, "mask_loader": False,
                       "guide_loader": False, "controlnet_preprocessor": True}
    pre = _pipe(pipes, "controlnet_preprocessor")
    assert pre["config"]["strict"] is True
    assert pre["config"]["preprocessors"][0]["type"] == "openpose"
    assert pre["input"][0]["provider"] == "source_loader"
    assert providers["control_image"] == "controlnet_preprocessor"
    assert providers["image"] == "source_loader"
    assert _pipe(pipes, "source_loader")["config"]["media"][0]["path"] == "/uploads/room.png"
    assert float(_pipe(pipes, "generator/qwen_image21")["config"]["control_strength"]) == 0.7


def test_inpaint_alone_loads_the_image_and_its_mask_without_a_guide(qwen_image21_template):
    pipes, providers, enabled = _control_case(
        qwen_image21_template, guide="none", source_image_inpaint_mask="/uploads/room_mask.png")
    assert enabled == {"source_loader": True, "inpaint_loader": True, "mask_loader": True,
                       "guide_loader": False, "controlnet_preprocessor": False}
    assert _pipe(pipes, "inpaint_loader")["config"]["media"][0]["path"] == "/uploads/room.png"
    assert _pipe(pipes, "mask_loader")["config"]["media"][0]["path"] == "/uploads/room_mask.png"
    assert providers["inpaint_image"] == "inpaint_loader"
    assert providers["inpaint_mask"] == "mask_loader"


def test_inpaint_with_a_guide_from_the_same_image(qwen_image21_template):
    pipes, providers, enabled = _control_case(
        qwen_image21_template, guide="depth", source_image_inpaint_mask="/uploads/room_mask.png")
    assert enabled == {"source_loader": True, "inpaint_loader": True, "mask_loader": True,
                       "guide_loader": False, "controlnet_preprocessor": True}
    assert _pipe(pipes, "controlnet_preprocessor")["input"][0]["provider"] == "source_loader"


def test_a_guide_from_a_different_image(qwen_image21_template):
    pipes, providers, enabled = _control_case(
        qwen_image21_template, guide="canny", guide_from_other=True, guide_image="/uploads/pose.png")
    assert enabled == {"source_loader": True, "inpaint_loader": False, "mask_loader": False,
                       "guide_loader": True, "controlnet_preprocessor": True}
    assert _pipe(pipes, "guide_loader")["config"]["media"][0]["path"] == "/uploads/pose.png"
    assert _pipe(pipes, "controlnet_preprocessor")["input"][0]["provider"] == "guide_loader"
    assert providers["image"] == "source_loader"


@pytest.mark.parametrize("other,provider", [(False, "source_loader"), (True, "guide_loader")])
def test_a_ready_made_map_skips_preprocessing(qwen_image21_template, other, provider):
    pipes, providers, enabled = _control_case(
        qwen_image21_template, guide="as_is", guide_from_other=other, guide_image="/uploads/depth.png")
    assert enabled["controlnet_preprocessor"] is False
    assert providers["control_image"] == provider


def _bind_control(template, **form):
    form_data = {
        "diffusion_model": "/models/qwen21_dit.safetensors",
        "text_encoder": "/models/qwen21_te.safetensors",
        "vae": "/models/qwen21_vae.safetensors",
        "control_model": "/models/fun_control.safetensors",
        "resolution": "1024x1024",
        **form,
    }
    return bind_form(template, "control", form_name=None, raw_form_data=form_data, user_id=None, storage_dir=None)


def test_no_guide_without_a_mask_is_refused_plainly(qwen_image21_template):
    with pytest.raises(FormBindingError) as refused:
        _bind_control(qwen_image21_template, source_image="/uploads/room.png", guide="none")
    assert refused.value.field_errors["source_image"] == [
        "Paint the area to repaint on the image, or choose a Guide."]


def test_a_guide_from_a_different_image_needs_that_image(qwen_image21_template):
    with pytest.raises(FormBindingError) as refused:
        _bind_control(qwen_image21_template, source_image="/uploads/room.png", guide_from_other=True)
    assert refused.value.field_errors["guide_image"] == [
        "Add the image to take the guide from, or turn off Take the guide from a different image."]


def test_the_main_image_is_required(qwen_image21_template):
    with pytest.raises(FormBindingError) as refused:
        _bind_control(qwen_image21_template)
    assert "source_image" in refused.value.field_errors


def test_the_guide_image_is_optional_while_guiding_from_the_main_image(qwen_image21_template):
    bound = _bind_control(qwen_image21_template, source_image="/uploads/room.png")
    assert bound.values["guide"] == "canny"
    assert bound.values["guide_from_other"] is False


def test_the_control_window_reaches_the_generator(qwen_image21_template):
    pipes = _process(qwen_image21_template, {"guide": "canny", "control_start": 0.1, "control_end": 0.8},
                     mode="control")
    cfg = _pipe(pipes, "generator/qwen_image21")["config"]
    assert float(cfg["control_start"]) == 0.1
    assert float(cfg["control_end"]) == 0.8


def test_inpaint_without_a_guide_ignores_a_leftover_window(qwen_image21_template):
    pipes = _process(qwen_image21_template, {
        "guide": "none", "control_start": 0.3, "control_end": 0.6,
        "source_image_inpaint_mask": "/uploads/room_mask.png",
    }, mode="control")
    cfg = _pipe(pipes, "generator/qwen_image21")["config"]
    assert float(cfg["control_start"]) == 0.0
    assert float(cfg["control_end"]) == 1.0


@pytest.mark.parametrize("guide", ["canny", "none"])
def test_the_fun_controlnet_model_must_be_picked(qwen_image21_template, guide):
    form = {"source_image": "/uploads/room.png", "guide": guide, "control_model": ""}
    if guide == "none":
        form["source_image_inpaint_mask"] = "/uploads/room_mask.png"
    with pytest.raises(FormBindingError) as refused:
        _bind_control(qwen_image21_template, **form)
    assert "control_model" in refused.value.field_errors


def test_a_rendered_control_pipeline_loads_the_patched_dit(qwen_image21_template, monkeypatch):
    from types import SimpleNamespace

    from src.pipelines.contracts import PipeInput
    import src.pipelines.pipes.model_loader.qwen_image21.main as loader_module
    from src.pipelines.pipes.model_loader.qwen_image21.main import ModelLoaderQwenImage21Pipe

    loads = []
    acquired = []

    class _Loader:
        def __init__(self, *args, **kwargs):
            pass

        def load(self, path, kind, **kwargs):
            loads.append((path, kind, kwargs))
            return SimpleNamespace(module=object(), spec=None, estimated_vram_gb=1.0)

    class _Models:
        def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
            acquired.append((key, fingerprint))
            return loader()

        def retain(self, key, fingerprint):
            return True

    monkeypatch.setattr(loader_module, "NativeEngineLoader", _Loader)
    pipes = _process(qwen_image21_template, {"guide": "openpose"}, mode="control")
    config = _pipe(pipes, "model_loader/qwen_image21")["config"]
    ModelLoaderQwenImage21Pipe(config=config).process(PipeInput(input={"MODELS": _Models()}), lambda _o: None)

    dit_key, dit_fp = next((k, f) for k, f in acquired if k.startswith("native/dit/"))
    assert dit_key == "native/dit//models/qwen21_dit.safetensors+control=/models/fun_control.safetensors"
    assert dit_fp.endswith("|control=/models/fun_control.safetensors")
    assert ("/models/qwen21_dit.safetensors", "diffusion_model",
            {"model_patch": "/models/fun_control.safetensors"}) in loads
