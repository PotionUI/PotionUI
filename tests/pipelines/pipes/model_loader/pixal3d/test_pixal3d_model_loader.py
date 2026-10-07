from __future__ import annotations

import pytest

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.model_loader.pixal3d import main as pixal3d_main
from src.pipelines.pipes.model_loader.pixal3d.bundle import Pixal3DModelBundle
from src.pipelines.pipes.model_loader.pixal3d.main import ModelLoaderPixal3DPipe
from src.pipelines.pipes.model_loader.trellis2 import main as trellis2_main
from src.platform.runtime.native.arch.pixal3d.image_to_mesh import Pixal3DComponents

DIT = "/m/pixal3d_bf16.safetensors"
MULTIVIEW_DIT = "/m/pixal3d_multiview_bf16.safetensors"
SHAPE_VAE = "/m/trellis_2_shape_vae_bf16.safetensors"
TEXTURE_VAE = "/m/trellis_2_texture_vae_bf16.safetensors"
ENCODER = "/m/dino_v3_vit_l_naf.safetensors"


class _FakeModels:
    def __init__(self):
        self.calls = []
        self._entries = {}

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        entry = self._entries.get(key)
        if entry is not None and entry[0] == fingerprint:
            return entry[1]
        value = loader()
        self._entries[key] = (fingerprint, value)
        return value

    def keys(self):
        return [key for key, _ in self.calls]


class _FakeComponent:
    def __init__(self, label):
        self.label = label

    def to(self, device):
        return self


@pytest.fixture(autouse=True)
def _fake_loaders(monkeypatch):
    built = []

    def _record(label):
        def _build(*args, **kwargs):
            built.append((label, args, kwargs))
            return _FakeComponent(label)

        return _build

    for name, label in (
        ("load_dino_conditioner", "dino"),
        ("load_ss_vae_decoder", "ss_vae"),
        ("load_shape_slat_decoder", "shape_decoder"),
        ("load_tex_slat_decoder", "tex_decoder"),
        ("load_ss_flow", "trellis2_ss_flow"),
        ("load_shape_slat_flow", "trellis2_shape_flow"),
        ("load_tex_slat_flow", "trellis2_tex_flow"),
    ):
        monkeypatch.setattr(trellis2_main.trellis2_load, name, _record(label))

    for name, label in (
        ("load_pixal3d_naf", "naf"),
        ("load_pixal3d_ss_flow", "ss_flow"),
        ("load_pixal3d_shape_flow", "shape_flow"),
        ("load_pixal3d_tex_flow", "tex_flow"),
    ):
        monkeypatch.setattr(pixal3d_main.pixal3d_load, name, _record(label))

    monkeypatch.setattr(trellis2_main, "_load_matting", lambda path: _FakeComponent("matting"))
    monkeypatch.setattr(trellis2_main, "prefix_size_gb", lambda path, prefix: 1.5)
    monkeypatch.setattr(pixal3d_main, "prefix_size_gb", lambda path, prefix: 1.5)
    monkeypatch.setattr(trellis2_main, "file_size_gb", lambda path: 0.75)
    return built


def _config(**over):
    config = ModelLoaderPixal3DPipe.get_default_config()
    config.update({
        "diffusion_model": {"file_path": DIT, "name": "pixal3d"},
        "shape_vae": {"file_path": SHAPE_VAE, "name": "shape-vae"},
        "texture_vae": {"file_path": TEXTURE_VAE, "name": "texture-vae"},
        "image_encoder": {"file_path": ENCODER, "name": "dino-naf"},
    })
    config.update(over)
    return config


def _process(models=None, **over):
    pipe = ModelLoaderPixal3DPipe(_config(**over))
    models = models if models is not None else _FakeModels()
    out = pipe.process(PipeInput(input={"MODELS": models}), lambda output: None)
    return models, out.output["model"]


def _specs():
    return {spec.name: spec for spec in ModelLoaderPixal3DPipe.configuration()}


def test_every_component_is_acquired_under_its_own_key():
    models, _ = _process()
    assert sorted(models.keys()) == sorted([
        f"native/trellis2/dino/{ENCODER}",
        f"native/trellis2/ss_vae/{SHAPE_VAE}",
        f"native/trellis2/shape_decoder/{SHAPE_VAE}",
        f"native/trellis2/tex_decoder/{TEXTURE_VAE}",
        f"native/pixal3d/naf/{ENCODER}",
        f"native/pixal3d/ss_flow/{DIT}",
        f"native/pixal3d/shape_flow_512/{DIT}",
        f"native/pixal3d/shape_flow_1024/{DIT}",
        f"native/pixal3d/tex_flow_1024/{DIT}",
    ])


def test_no_trellis2_flow_key_is_acquired():
    models, _ = _process()
    assert not [key for key in models.keys() if key.startswith("native/trellis2/") and "flow" in key]


def test_the_pixal3d_flow_loaders_run_and_the_trellis2_ones_never_do(_fake_loaders):
    _process()
    labels = [label for label, _, _ in _fake_loaders]
    assert labels.count("ss_flow") == 1
    assert labels.count("shape_flow") == 2
    assert labels.count("tex_flow") == 1
    assert labels.count("naf") == 1
    assert not {"trellis2_ss_flow", "trellis2_shape_flow", "trellis2_tex_flow"} & set(labels)
    shape_tiers = sorted(args[1] for label, args, _ in _fake_loaders if label == "shape_flow")
    assert shape_tiers == ["1024", "512"]


def test_a_multiview_file_is_refused_in_single_mode_before_anything_loads():
    pipe = ModelLoaderPixal3DPipe(_config(
        diffusion_model={"file_path": MULTIVIEW_DIT, "name": "mv"}, bundle_mode="single"))
    models = _FakeModels()
    with pytest.raises(ValueError, match="multi-view"):
        pipe.process(PipeInput(input={"MODELS": models}), lambda output: None)
    assert models.calls == []


def test_a_single_view_file_is_refused_in_multiview_mode_before_anything_loads():
    pipe = ModelLoaderPixal3DPipe(_config(bundle_mode="multiview"))
    models = _FakeModels()
    with pytest.raises(ValueError, match="multiview"):
        pipe.process(PipeInput(input={"MODELS": models}), lambda output: None)
    assert models.calls == []


@pytest.mark.parametrize("mode, dit", [("single", DIT), ("multiview", MULTIVIEW_DIT)])
def test_a_bundle_matching_the_mode_loads(mode, dit):
    models, bundle = _process(diffusion_model={"file_path": dit, "name": "p3d"}, bundle_mode=mode)
    assert bundle.variant == mode
    assert f"native/pixal3d/ss_flow/{dit}" in models.keys()


def test_an_unknown_bundle_mode_is_refused():
    pipe = ModelLoaderPixal3DPipe(_config(bundle_mode="stereo"))
    with pytest.raises(ValueError, match="bundle mode"):
        pipe.process(PipeInput(input={"MODELS": _FakeModels()}), lambda output: None)


def test_the_512_tier_is_refused_before_anything_loads():
    pipe = ModelLoaderPixal3DPipe(_config(resolution_tier="512"))
    models = _FakeModels()
    with pytest.raises(ValueError, match="resolution tier"):
        pipe.process(PipeInput(input={"MODELS": models}), lambda output: None)
    assert models.calls == []


def test_the_default_tier_is_1536():
    assert ModelLoaderPixal3DPipe.get_default_config()["resolution_tier"] == "1536"
    _, bundle = _process()
    assert bundle.tier == "1536"


def test_configuration_offers_only_the_supported_tiers_and_bundle_modes():
    specs = _specs()
    assert specs["resolution_tier"].choices == ["1024", "1536"]
    assert specs["resolution_tier"].default == "1536"
    assert specs["bundle_mode"].choices == ["single", "multiview"]
    assert specs["bundle_mode"].default == "single"


def test_the_bundle_carries_tier_device_variant_and_naf():
    _, bundle = _process(resolution_tier="1024", device="cpu", bundle_mode="single")
    assert isinstance(bundle, Pixal3DModelBundle)
    assert bundle.tier == "1024"
    assert bundle.device == "cpu"
    assert bundle.variant == "single"
    assert bundle.naf.module.label == "naf"


def test_the_bundle_resolves_into_pixal3d_components_with_naf():
    _, bundle = _process()
    components = bundle.components()
    assert isinstance(components, Pixal3DComponents)
    assert components.naf.label == "naf"
    assert components.ss_flow.label == "ss_flow"
    assert components.conditioner.label == "dino"
    assert components.shape_flow_hr.label == "shape_flow"


def test_an_evicted_naf_is_named_rather_than_surfacing_as_none():
    _, bundle = _process()
    bundle.naf = None
    with pytest.raises(ValueError, match="NAF upsampler was evicted"):
        bundle.components()


def test_describe_models_reports_the_pixal3d_types():
    types = {model.type for model in ModelLoaderPixal3DPipe(_config()).describe_models()}
    assert {"pixal3d_dit", "pixal3d_image_encoder"} <= types
    assert "trellis2_dit" not in types
    assert "trellis2_image_encoder" not in types


MOGE = "/m/moge_2_vitl_normal_fp16.safetensors"


@pytest.fixture
def fake_estimator(monkeypatch):
    loaded = []

    def _load(path):
        loaded.append(path)
        return _FakeComponent("moge")

    monkeypatch.setattr(pixal3d_main, "_load_camera_estimator", _load)
    monkeypatch.setattr(pixal3d_main, "file_size_gb", lambda path: 0.66)
    return loaded


def test_the_camera_estimator_is_optional_and_unset_by_default():
    spec = _specs()["camera_estimator"]
    assert spec.required is False
    assert ModelLoaderPixal3DPipe.get_default_config()["camera_estimator"] is None


def test_a_selected_camera_estimator_is_acquired_under_its_own_key(fake_estimator):
    models, bundle = _process(camera_estimator={"file_path": MOGE, "name": "moge"})
    assert f"native/moge/{MOGE}" in models.keys()
    assert fake_estimator == [MOGE]
    assert bundle.camera_estimator_selected is True
    assert bundle.components().camera_estimator.label == "moge"


@pytest.mark.parametrize("value", [None, {"file_path": "", "name": ""}])
def test_without_a_camera_estimator_nothing_extra_loads(fake_estimator, value):
    models, bundle = _process(camera_estimator=value)
    assert not [key for key in models.keys() if key.startswith("native/moge/")]
    assert fake_estimator == []
    assert bundle.camera_estimator_selected is False
    assert bundle.components().camera_estimator is None


def test_an_evicted_camera_estimator_is_named_rather_than_read_as_unselected(fake_estimator):
    _, bundle = _process(camera_estimator={"file_path": MOGE, "name": "moge"})
    bundle.camera_estimator = None
    with pytest.raises(ValueError, match="camera estimator was evicted"):
        bundle.components()


def test_the_camera_estimator_is_budgeted_at_its_fp32_size(fake_estimator):
    pipe = ModelLoaderPixal3DPipe(_config(camera_estimator={"file_path": MOGE, "name": "moge"}))
    plan = pipe._plan(pipe._weight_paths(), "1536", None)
    [entry] = [entry for entry in plan if entry[1] == f"native/moge/{MOGE}"]
    assert entry[0] == "camera estimator"
    assert entry[3] == pytest.approx(1.32)


def test_describe_models_names_the_camera_estimator():
    described = ModelLoaderPixal3DPipe(_config(camera_estimator={"file_path": MOGE, "name": "moge"})).describe_models()
    assert ("moge", "geometry_estimation") in [(model.name, model.type) for model in described]


def test_a_camera_estimator_that_fails_to_load_is_named(monkeypatch):
    def _refuse(path):
        raise ValueError("other.safetensors is not a MoGe-2 checkpoint")

    monkeypatch.setattr(pixal3d_main.moge_load, "load_moge2", _refuse)
    with pytest.raises(ValueError, match="camera estimator could not be loaded: other.safetensors"):
        pixal3d_main._load_camera_estimator("/m/other.safetensors")
