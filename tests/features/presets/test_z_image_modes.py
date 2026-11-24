from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if "marketplace/ZImage" in Path(p.path).as_posix()), None)
    if found is None:
        pytest.fail("marketplace/ZImage preset not present")
    return found


_MODELS = {
    "diffusion_model": "/models/zimage_dit.safetensors",
    "text_encoder": "/models/zimage_te.safetensors",
    "vae": "/models/zimage_vae.safetensors",
}


def _form(mode, over=None):
    form = dict(_MODELS)
    if mode in ("enhance", "img2img", "upscale", "refine_faces"):
        form["source_image"] = "/uploads/source.png"
    if mode == "refine_faces":
        form["face_model"] = "/models/face_landmarker.task"
    if mode == "control":
        form["guide_image"] = "/uploads/guide.png"
        form["control_model"] = "/models/fun_control.safetensors"
    if over:
        form.update(over)
    return form


def _process(template, mode, over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = bound_form_data(template, mode, _form(mode, over))
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": form_data})


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def _enabled(pipes, name):
    return bool(_pipe(pipes, name)["enabled"])


def _providers(pipes, consumer):
    return {i["name"]: i["provider"] for i in _pipe(pipes, consumer)["input"]}


def _generator(pipes):
    return _pipe(pipes, "generator/z_image")["config"]


def test_the_modes_are_declared(template):
    assert list(template.modes.keys()) == ["txt2img", "enhance", "inpaint", "control", "img2img", "upscale", "refine_faces"]


def test_the_new_modes_are_flagged_unvalidated(template):
    assert set(template.modes_unvalidated) == {"img2img", "upscale", "refine_faces"}


def test_the_new_modes_carry_the_matching_editor_modes(template):
    assert template.editor_modes["upscale"].editor.from_mode == "upscale"
    assert template.editor_modes["fix_faces"].editor.from_mode == "refine_faces"


def test_txt2img_is_untouched(template):
    pipes = _process(template, "txt2img", {"resolution": "1024x1024"})
    cfg = _generator(pipes)
    assert cfg.get("mode") is None
    assert "control_model" not in _pipe(pipes, "model_loader/z_image")["config"]


@pytest.mark.parametrize("scale,factor", [("1_5x", 1.5), ("2x", 2.0), ("3x", 3.0)])
def test_enhance_upscales_by_the_chosen_factor(template, scale, factor):
    cfg = _generator(_process(template, "enhance", {"upscale_by": scale}))
    assert cfg["mode"] == "img2img"
    assert float(cfg["img2img_scale"]) == factor
    assert "resolution" not in cfg


@pytest.mark.parametrize("detail,denoise", [("subtle", 0.12), ("balanced", 0.2), ("strong", 0.35)])
def test_enhance_detail_sets_how_much_is_redrawn(template, detail, denoise):
    cfg = _generator(_process(template, "enhance", {"enhance_detail": detail}))
    assert float(cfg["denoise"]) == denoise


def test_enhance_shows_the_original_beside_the_result(template):
    pipes = _process(template, "enhance")
    providers = _providers(pipes, "compare_enhance")
    assert providers == {"before_image": "media_loader", "after_image": "generator"}
    assert _providers(pipes, "generator")["image"] == "media_loader"
    assert _providers(pipes, "gallery")["image"] == "compare_enhance"
    assert not _enabled(pipes, "face_detailer")
    assert not _enabled(pipes, "compare_faces")
    assert not _enabled(pipes, "gallery_before_faces")


def test_the_face_pass_chains_after_the_upscale(template):
    pipes = _process(template, "enhance", {"face_detailer_enabled": True})
    assert _enabled(pipes, "face_detailer")
    assert _providers(pipes, "face_detailer")["image"] == "compare_enhance"
    assert _providers(pipes, "face_detailer")["model"] == "model_loader/z_image"
    assert _providers(pipes, "compare_faces") == {"before_image": "compare_enhance", "after_image": "face_detailer"}
    assert _providers(pipes, "gallery")["image"] == "compare_faces"
    assert not _enabled(pipes, "gallery_before_faces")


def test_the_face_pass_can_keep_the_image_before_it(template):
    pipes = _process(template, "enhance", {"face_detailer_enabled": True, "face_detailer_keep_base": True})
    assert _enabled(pipes, "gallery_before_faces")
    assert _providers(pipes, "gallery_before_faces")["image"] == "compare_enhance"


def test_the_face_pass_inherits_the_run_sampling(template):
    pipes = _process(template, "enhance", {"face_detailer_enabled": True, "face_detailer_denoise": 0.25})
    cfg = _pipe(pipes, "face_detailer")["config"]
    assert float(cfg["denoise"]) == 0.25
    assert float(cfg["guidance"]) == float(_generator(pipes)["guidance"])
    assert cfg["sampler"] == _generator(pipes)["sampler"]


@pytest.mark.parametrize("mode", ["txt2img", "enhance"])
def test_only_structure_loads_the_fun_controlnet(template, mode):
    over = {"resolution": "1024x1024"} if mode == "txt2img" else None
    assert "control_model" not in _pipe(_process(template, mode, over), "model_loader/z_image")["config"]


def test_structure_loads_the_fun_controlnet_with_the_dit(template):
    cfg = _pipe(_process(template, "control"), "model_loader/z_image")["config"]
    assert cfg["control_model"]["file_path"] == "/models/fun_control.safetensors"


@pytest.mark.parametrize("guide", ["openpose", "depth", "canny", "scribble"])
def test_each_guide_runs_its_preprocessor_on_the_picture(template, guide):
    pipes = _process(template, "control", {"guide": guide})
    pre = _pipe(pipes, "controlnet_preprocessor")
    assert pre["enabled"]
    assert pre["config"]["preprocessors"][0]["type"] == guide
    assert pre["config"]["strict"] is True
    assert pre["input"][0]["provider"] == "guide_loader"
    assert _providers(pipes, "generator/z_image")["control_image"] == "controlnet_preprocessor"
    assert _pipe(pipes, "guide_loader")["config"]["media"][0]["path"] == "/uploads/guide.png"


def test_structure_defaults_to_pose(template):
    pipes = _process(template, "control")
    assert _pipe(pipes, "controlnet_preprocessor")["config"]["preprocessors"][0]["type"] == "openpose"


def test_a_ready_made_map_skips_the_preprocessor(template):
    pipes = _process(template, "control", {"guide_extract": False, "guide": "depth"})
    assert not _enabled(pipes, "controlnet_preprocessor")
    assert _providers(pipes, "generator/z_image")["control_image"] == "guide_loader"
    assert not _enabled(pipes, "guide_gallery")


def test_structure_generator_runs_in_control_mode_with_the_distilled_settings(template):
    cfg = _generator(_process(template, "control", {"control_strength": 0.7, "control_start": 0.1, "control_end": 0.9}))
    assert cfg["mode"] == "control"
    assert float(cfg["control_strength"]) == 0.7
    assert float(cfg["control_start"]) == 0.1
    assert float(cfg["control_end"]) == 0.9
    assert int(cfg["steps"]) == 8
    assert float(cfg["guidance"]) == 1.0
    assert cfg["resolution"] == "1024x1024"


def test_structure_strength_defaults_inside_the_recommended_band(template):
    assert 0.65 <= float(_generator(_process(template, "control"))["control_strength"]) <= 1.0


def test_the_guide_can_be_saved_beside_the_result(template):
    pipes = _process(template, "control", {"guide": "depth", "guide_save": True})
    guide = _pipe(pipes, "guide_gallery")
    assert _enabled(pipes, "guide_gallery")
    assert guide["input"][0]["provider"] == "controlnet_preprocessor"
    assert guide["config"]["label"] == "Guide: Depth"


def test_the_guide_is_not_saved_by_default(template):
    assert not _enabled(_process(template, "control"), "guide_gallery")


def test_structure_needs_a_picture_and_the_controlnet(template):
    with pytest.raises(FormBindingError) as refused:
        bind_form(template, "control", form_name=None, raw_form_data=dict(_MODELS), user_id=None, storage_dir=None)
    assert {"guide_image", "control_model"} <= set(refused.value.field_errors)


def test_the_param_emitter_lists_the_controlnet_and_the_guide(template):
    pipes = _process(template, "control", {"guide": "canny"})
    parameters = [list(p) for p in _pipe(pipes, "param_emitter")["config"]["parameters"] if p]
    assert ["model", "/models/fun_control.safetensors"] in parameters
    assert ["guide", "canny"] in parameters


def test_a_rendered_structure_pipeline_loads_the_patched_dit(template, monkeypatch):
    from src.pipelines.contracts import PipeInput
    import src.pipelines.pipes._shared.generation.three_component_loader as loader_module
    import src.pipelines.pipes.model_loader.z_image.main as loader_main
    from src.pipelines.pipes.model_loader.z_image.main import ModelLoaderZImagePipe

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
    monkeypatch.setattr(loader_main, "load_text_encoder", lambda *a, **kw: SimpleNamespace(name="te"))
    config = _pipe(_process(template, "control"), "model_loader/z_image")["config"]
    ModelLoaderZImagePipe(config=config).process(PipeInput(input={"MODELS": _Models()}), lambda _o: None)

    dit_key, dit_fp = next((k, f) for k, f in acquired if k.startswith("native/dit/"))
    assert dit_key == "native/dit//models/zimage_dit.safetensors+control=/models/fun_control.safetensors"
    assert dit_fp.endswith("|control=/models/fun_control.safetensors")
    assert ("/models/zimage_dit.safetensors", "diffusion_model",
            {"model_patch": "/models/fun_control.safetensors"}) in loads


def test_the_recommended_controlnets_are_union_2_1_files_with_hashes(template):
    import yaml

    form = yaml.safe_load((Path("content/presets/marketplace/ZImage/modes/control/tabs/guide.yml")).read_text(encoding="utf-8"))
    field = next(c for s in form["fields"] for c in s.get("children", []) if isinstance(c, dict) and c.get("name") == "control_model")
    recommendations = field["configuration"]["recommendations"]
    assert recommendations
    for item in recommendations:
        assert "Union-2.1" in item["name"]
        assert "Controlnet-Union-2.1/resolve/main/" in item["link"]
        assert len(item["sha256"]) == 64


def test_img2img_runs_the_generator_from_the_picture_at_the_chosen_strength(template):
    pipes = _process(template, "img2img", {"denoise": 0.4})
    cfg = _generator(pipes)
    assert cfg["mode"] == "img2img"
    assert float(cfg["denoise"]) == 0.4
    assert _providers(pipes, "generator")["image"] == "media_loader"
    assert _pipe(pipes, "media_loader")["config"]["media"][0]["path"] == "/uploads/source.png"
    assert _providers(pipes, "gallery")["image"] == "generator"


def test_upscale_tiles_the_refine_at_the_chosen_scale_and_detail(template):
    pipes = _process(template, "upscale", {"upscale_factor": 3.0, "refine_amount": "strong", "tile_size": 768})
    cfg = _pipe(pipes, "tiled_refiner")["config"]
    assert float(cfg["scale"]) == 3.0
    assert float(cfg["denoise"]) == 0.45
    assert int(cfg["tile_size"]) == 768
    providers = _providers(pipes, "tiled_refiner")
    assert providers["model"] == "model_loader/z_image"
    assert providers["image"] == "media_loader"
    assert _providers(pipes, "gallery")["image"] == "tiled_refiner"


def test_refine_faces_repaints_faces_with_the_chosen_strength(template):
    pipes = _process(template, "refine_faces", {"fix_strength": 0.5})
    cfg = _pipe(pipes, "face_detailer")["config"]
    assert float(cfg["denoise"]) == 0.5
    assert cfg["face_model"] == "/models/face_landmarker.task"
    assert not cfg["detect_hands"]
    assert _providers(pipes, "face_detailer")["image"] == "media_loader"
    assert _providers(pipes, "gallery")["image"] == "face_detailer"


def test_refine_faces_can_take_hands_along(template):
    over = {"face_detailer_hands": True, "face_detailer_hand_model": "/models/hand_landmarker.task"}
    cfg = _pipe(_process(template, "refine_faces", over), "face_detailer")["config"]
    assert cfg["detect_hands"] is True
    assert cfg["hand_model"] == "/models/hand_landmarker.task"


def test_the_enhance_face_pass_can_take_hands_along(template):
    over = {
        "face_detailer_enabled": True,
        "face_detailer_hands": True,
        "face_detailer_hand_model": "/models/hand_landmarker.task",
    }
    cfg = _pipe(_process(template, "enhance", over), "face_detailer")["config"]
    assert cfg["detect_hands"] is True
    assert cfg["hand_model"] == "/models/hand_landmarker.task"
