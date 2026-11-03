from __future__ import annotations

import dataclasses
import functools
import re
from pathlib import Path

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.platform.filesystem.model_types import directory_to_type, type_to_directory

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / "content/presets/marketplace"
CATALOG = REPO / "src/std/models/preview_decoders.yml"

FIELD_FILES = {
    ("MiniMax-H3", "video"): None,
    ("MiniMax-H3-Fast", "video"): None,
    ("MiniMax-H3-VDN", "video"): None,
    ("LTX-2", "video"): "LTX-2/modes/video/tabs/generation.yml",
    ("LTX-2.5", "video"): "LTX-2.5/files/form/models_av.yml",
    ("LTX-2.5", "extend"): "LTX-2.5/files/form/models_av.yml",
    ("Wan", "video"): "Wan/modes/video/tabs/generation.yml",
    ("QwenImage", "txt2img"): "QwenImage/modes/txt2img/tabs/generation.yml",
    ("QwenImage", "img2img"): "QwenImage/modes/img2img/tabs/generation.yml",
    ("QwenImage", "edit"): "QwenImage/modes/edit/tabs/generation.yml",
}

GENERATORS = {
    "generator/video_minimax_h3", "generator/video_ltx", "generator/txt2vid_wan22",
    "generator/img2vid_wan22", "generator/chain_video_wan22", "generator/qwen",
}

EXPECTED_DECODERS = {
    "MiniMax-H3": {"taeh3"}, "MiniMax-H3-Fast": {"taeh3"}, "MiniMax-H3-VDN": {"taeh3"},
    "LTX-2": {"taeltx_2", "taeltx2_3"}, "LTX-2.5": {"taeltx2_3"},
    "Wan": {"taew2_1", "taew2_2"}, "QwenImage": {"taew2_1"},
}


@functools.cache
def _load(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _catalog():
    return {Path(entry["url"]).stem: entry for entry in _load(CATALOG)["models"]}


def _walk(node, name):
    if isinstance(node, dict):
        if node.get("name") == name and node.get("type") == "model":
            yield node
        for value in node.values():
            yield from _walk(value, name)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, name)


@functools.cache
def _loaded_presets():
    loader = PresetTemplateLoader([str(ROOT)])
    loader.load_presets()
    ids = {
        yaml.safe_load(path.read_text(encoding="utf-8"))["id"]: path.parent.name
        for path in ROOT.glob("*/preset.yml")
    }
    return {ids[preset.id]: preset for preset in loader.presets if preset.id in ids}


def _loaded_form(preset, mode):
    form = _loaded_presets()[preset].modes[mode].forms[0]
    return [dataclasses.asdict(field) for field in form.fields]


def _field(relative, preset=None, mode=None):
    tree = _loaded_form(preset, mode) if relative is None else _load(ROOT / relative)
    found = list(_walk(tree, "preview_decoder"))
    assert len(found) == 1, relative
    return found[0]


def test_the_model_type_lives_in_vae_approx():
    assert directory_to_type()["vae_approx"] == "preview_decoder"
    assert type_to_directory()["preview_decoder"] == "vae_approx"


def test_catalog_lists_five_verified_decoders():
    catalog = _catalog()
    assert set(catalog) == {"taeh3", "taeltx_2", "taeltx2_3", "taew2_1", "taew2_2"}
    for stem, entry in catalog.items():
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), stem
        assert entry["type"] == "preview_decoder"
        assert entry["file_path"] == "{{ path('preview_decoder', '" + stem + ".safetensors') }}"
        assert entry["url"].startswith("https://raw.githubusercontent.com/madebyollin/taehv/")
        assert entry["url"].endswith(f"/safetensors/{stem}.safetensors")
    assert len({entry["sha256"] for entry in catalog.values()}) == 5


@pytest.mark.parametrize("preset,mode", sorted(FIELD_FILES))
def test_every_covered_mode_has_an_optional_preview_decoder_model_field(preset, mode):
    field = _field(FIELD_FILES[(preset, mode)], preset, mode)
    assert field["required"] is False
    assert field["configuration"]["model_type"] == "preview_decoder"
    assert field.get("default") is None
    stems = {Path(rec["name"]).stem for rec in field["configuration"]["recommendations"]}
    assert stems == EXPECTED_DECODERS[preset]


@pytest.mark.parametrize("preset,mode", sorted(FIELD_FILES))
def test_field_recommendations_match_the_catalog(preset, mode):
    catalog = _catalog()
    for rec in _field(FIELD_FILES[(preset, mode)], preset, mode)["configuration"]["recommendations"]:
        entry = catalog[Path(rec["name"]).stem]
        assert rec["sha256"] == entry["sha256"]
        assert rec["link"] == entry["url"]


@pytest.mark.parametrize("preset,mode", sorted(FIELD_FILES))
def test_every_generator_of_a_covered_mode_receives_the_decoder(preset, mode):
    pipeline = _load(ROOT / preset / "modes" / mode / "pipeline.yml")["pipeline"]
    generators = [pipe for pipe in pipeline if pipe["name"] in GENERATORS]
    assert generators
    for pipe in generators:
        assert pipe["configuration"]["preview_decoder"] == "{{ form.preview_decoder | default('') }}", pipe["name"]


@pytest.mark.parametrize("preset", ["MiniMax-H3", "MiniMax-H3-Fast", "MiniMax-H3-VDN"])
def test_h3_hides_the_decoder_when_live_preview_is_off(preset):
    field = _field(FIELD_FILES[(preset, "video")], preset, "video")
    reactions = {r["when"]["equals"]: r["then"]["set_visibility"] for r in field["reactions"]}
    assert reactions == {False: False, True: True}


def test_generator_pipes_declare_the_config_key():
    from src.pipelines.pipes.generator.chain_video_wan22.main import GeneratorWanChainVideoPipe
    from src.pipelines.pipes.generator.img2vid_wan22.main import GeneratorWanImg2VidPipe
    from src.pipelines.pipes.generator.qwen.main import GeneratorQwenPipe
    from src.pipelines.pipes.generator.txt2vid_ltx.main import GeneratorLtxTxt2VidPipe
    from src.pipelines.pipes.generator.txt2vid_wan22.main import GeneratorWanTxt2VidPipe
    from src.pipelines.pipes.generator.video_ltx.main import GeneratorLtxVideoPipe
    from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe

    for pipe in (
        GeneratorMinimaxH3Pipe, GeneratorLtxVideoPipe, GeneratorLtxTxt2VidPipe, GeneratorWanTxt2VidPipe,
        GeneratorWanImg2VidPipe, GeneratorWanChainVideoPipe, GeneratorQwenPipe,
    ):
        spec = next(s for s in pipe.configuration() if s.name == "preview_decoder")
        assert spec.default == "" and spec.required is False, pipe.__name__


def test_native_step_hooks_hand_the_decoder_path_to_the_preview_hook(monkeypatch):
    from types import SimpleNamespace

    from src.pipelines.pipes._shared.generation.progress import native_step_hooks
    from src.platform.runtime.native import sampling

    seen = []
    monkeypatch.setattr(sampling, "make_preview_hook", lambda spec, emit, **kwargs: seen.append(kwargs))
    progress = SimpleNamespace(preview=lambda image: None)
    gen = SimpleNamespace(spec=object())
    native_step_hooks(gen, progress, lambda *a: None, decoder_path="/models/vae_approx/taew2_1.safetensors")
    native_step_hooks(gen, progress, lambda *a: None)
    native_step_hooks(gen, progress, lambda *a: None, preview=False, decoder_path="/x")
    assert seen == [{"decoder_path": "/models/vae_approx/taew2_1.safetensors"}, {"decoder_path": None}]
