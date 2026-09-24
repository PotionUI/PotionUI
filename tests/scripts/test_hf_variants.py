import urllib.request

import pytest
import yaml

from scripts import hf_variants

REPO = "Comfy-Org/Krea-2"

META = {"author": "Comfy-Org", "gated": False, "cardData": {"license": "other"}}

TREE = [
    {"type": "directory", "path": "diffusion_models"},
    {"type": "file", "path": "diffusion_models/krea2_turbo_bf16.safetensors", "size": 26283332608, "lfs": {"oid": "78bb", "size": 26283332608}},
    {"type": "file", "path": "diffusion_models/krea2_turbo_fp8_scaled.safetensors", "size": 13141730784, "lfs": {"oid": "eb4d", "size": 13141730784}},
    {"type": "file", "path": "diffusion_models/krea2_turbo_int8_convrot.safetensors", "size": 13492686496, "lfs": {"oid": "8e4e", "size": 13492686496}},
    {"type": "file", "path": "diffusion_models/krea2_turbo_mxfp8.safetensors", "size": 13532318080, "lfs": {"oid": "4c09", "size": 13532318080}},
    {"type": "file", "path": "diffusion_models/krea2_turbo_nvfp4.safetensors", "size": 7673668448, "lfs": {"oid": "6152", "size": 7673668448}},
    {"type": "file", "path": "diffusion_models/krea2_raw_bf16.safetensors", "size": 26283332608, "lfs": {"oid": "f99b", "size": 26283332608}},
    {"type": "file", "path": "text_encoders/qwen3vl_4b_bf16.safetensors", "size": 8875719384, "lfs": {"oid": "36f3", "size": 8875719384}},
]


def _fake_fetch(meta=META, tree=TREE):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/tree/main?recursive=1"):
            return tree
        return meta

    fetch.calls = calls
    return fetch


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access in a test")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def test_collects_matching_variants_in_quality_order():
    fetch = _fake_fetch()
    result = hf_variants.collect_variants(
        REPO,
        ["diffusion_models/krea2_turbo_*"],
        exclude=["*mxfp8*"],
        default_id="krea2_turbo_fp8_scaled",
        fetch=fetch,
    )

    assert fetch.calls == [
        "https://huggingface.co/api/models/Comfy-Org/Krea-2",
        "https://huggingface.co/api/models/Comfy-Org/Krea-2/tree/main?recursive=1",
    ]
    variants = result["variants"]
    assert [v["precision"] for v in variants] == ["bf16", "fp8", "int8", "nvfp4"]
    fp8 = variants[1]
    assert fp8 == {
        "id": "krea2_turbo_fp8_scaled",
        "label": "Balanced",
        "precision": "fp8",
        "filename": "krea2_turbo_fp8_scaled.safetensors",
        "size_bytes": 13141730784,
        "checksum": {"algorithm": "sha256", "value": "eb4d"},
        "provider_hint": {
            "source": "huggingface",
            "model_id": REPO,
            "version_id": "main@diffusion_models/krea2_turbo_fp8_scaled.safetensors",
        },
        "uploader": "Comfy-Org",
        "source_url": "https://huggingface.co/Comfy-Org/Krea-2",
        "default": True,
    }
    assert sum(1 for v in variants if v.get("default")) == 1
    assert result["license"] == "other"


def test_unknown_precision_files_are_skipped_not_guessed():
    result = hf_variants.collect_variants(REPO, ["diffusion_models/krea2_turbo_*"], fetch=_fake_fetch())
    assert result["skipped"] == ["diffusion_models/krea2_turbo_mxfp8.safetensors"]
    assert all("mxfp8" not in v["filename"] for v in result["variants"])


def test_gated_repo_marks_every_variant_gated_with_a_licence_link():
    meta = {"author": "krea", "gated": "manual", "cardData": {"license": "other"}}
    result = hf_variants.collect_variants("krea/Krea-2-Turbo", ["*.safetensors"], fetch=_fake_fetch(meta=meta))
    assert result["variants"]
    assert all(v["gated"] is True for v in result["variants"])
    assert all(v["license_url"] == "https://huggingface.co/krea/Krea-2-Turbo" for v in result["variants"])


@pytest.mark.parametrize(
    "filename,precision",
    [
        ("krea2_turbo_fp8_scaled.safetensors", "fp8"),
        ("qwen3vl_4b_bf16.safetensors", "bf16"),
        ("krea2_turbo_int8_convrot.safetensors", "int8"),
        ("krea2_turbo_nvfp4.safetensors", "nvfp4"),
        ("model-fp16.safetensors", "fp16"),
        ("krea2_turbo_mxfp8.safetensors", None),
        ("qwen_image_vae.safetensors", None),
    ],
)
def test_precision_of(filename, precision):
    assert hf_variants.precision_of(filename) == precision


def test_main_prints_yaml_that_the_recipe_schema_accepts(capsys):
    from src.features.recipes.schema import validate_recipe_dict

    code = hf_variants.main(
        [REPO, "text_encoders/*", "--default", "qwen3vl_4b_bf16"],
        fetch=_fake_fetch(),
    )

    assert code == 0
    printed = yaml.safe_load(capsys.readouterr().out)
    slot = {"id": "te", "kind": "text_encoder", "model_type": "text_encoder", "variants": printed["variants"]}
    recipe = {
        "schema_version": 1,
        "id": "demo",
        "version": 1,
        "name": "Demo",
        "engine": "native",
        "category": "image",
        "artifacts": [slot],
        "presets": [{"preset_id": "P"}],
        "steps": [{"key": "b", "kind": "backend.ensure", "title": "B", "params": {"engine": "native"}}],
    }
    assert validate_recipe_dict(recipe) == []


def test_main_reports_no_matches(capsys):
    assert hf_variants.main([REPO, "nothing/*"], fetch=_fake_fetch()) == 1
    assert "No files matched" in capsys.readouterr().err


def test_main_reports_a_fetch_error(capsys):
    def broken(url):
        raise OSError("offline")

    assert hf_variants.main([REPO, "*"], fetch=broken) == 1
    assert "offline" in capsys.readouterr().err
