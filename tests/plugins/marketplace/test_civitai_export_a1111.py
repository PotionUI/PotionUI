"""Tests for the civitai-provider plugin's A1111 `parameters` builder and PNG
injection.

Loaded by explicit file spec (not `sys.path` + `import backend.a1111`)
because several plugins use a top-level `backend` package name; importing
this one via `sys.path` insertion risks resolving to another plugin's
`backend` package depending on import order. `a1111.py` has no relative
imports of its own, so it needs no parent-package scaffolding.
"""

import importlib.util
import io
from pathlib import Path

import pytest
from PIL import Image

_module_path = (
    Path(__file__).resolve().parents[3]
    / "content"
    / "plugins"
    / "marketplace"
    / "civitai-provider"
    / "backend"
    / "a1111.py"
)
_spec = importlib.util.spec_from_file_location("civitai_provider_a1111", _module_path)
a1111 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(a1111)

build_a1111_parameters = a1111.build_a1111_parameters
inject_a1111_parameters = a1111.inject_a1111_parameters


CHECKPOINT = {
    "model_type": "diffusion_model",
    "name": "Krea-2",
    "filename": "krea2.safetensors",
    "sha256": "abcdef0123456789fedcba9876543210",
    "is_directory": False,
}

LORA_A = {
    "model_type": "lora",
    "name": "Detail Booster",
    "filename": "detail_booster.safetensors",
    "sha256": "1122334455667788990011223344556677889900",
    "is_directory": False,
}

LORA_B = {
    "model_type": "lora",
    "name": "Style Ink",
    "filename": "style_ink.safetensors",
    "sha256": "aabbccddeeff00112233445566778899aabbccdd",
    "is_directory": False,
}

FULL_PARAMETERS = {
    "positive_prompt": "a red fox in a snowy forest",
    "negative_prompt": "blurry, low quality",
    "steps": 30,
    "sampler": "euler",
    "cfg": 4.5,
    "seed": 123456789,
    "resolution": "1024x1024",
}


class TestBuildA1111Parameters:
    def test_full_realistic_generation(self):
        result = build_a1111_parameters(FULL_PARAMETERS, [CHECKPOINT, LORA_A, LORA_B])

        assert result == (
            "a red fox in a snowy forest\n"
            "Negative prompt: blurry, low quality\n"
            "Steps: 30, Sampler: euler, CFG scale: 4.5, Seed: 123456789, "
            "Size: 1024x1024, Model hash: abcdef0123, Model: Krea-2, "
            'Lora hashes: "Detail Booster: 1122334455, Style Ink: aabbccddee"'
        )

    def test_missing_negative_prompt_omits_line(self):
        params = {**FULL_PARAMETERS, "negative_prompt": None}
        result = build_a1111_parameters(params, [CHECKPOINT])
        lines = result.split("\n")
        assert not any(line.startswith("Negative prompt:") for line in lines)

    def test_missing_optional_keys_omitted_gracefully(self):
        params = {"positive_prompt": "just a prompt"}
        result = build_a1111_parameters(params, [])
        assert result == "just a prompt"

    def test_no_positive_prompt_still_produces_a_line(self):
        result = build_a1111_parameters({"steps": 10}, [])
        assert result == "\nSteps: 10"

    def test_scalar_cfg_zero_is_kept_not_treated_as_missing(self):
        params = {"positive_prompt": "p", "cfg": 0.0}
        result = build_a1111_parameters(params, [])
        assert "CFG scale: 0.0" in result

    def test_resolution_as_width_height_pair(self):
        params = {"positive_prompt": "p", "resolution": [896, 1152]}
        result = build_a1111_parameters(params, [])
        assert "Size: 896x1152" in result

    def test_directory_backed_checkpoint_omits_hash_keeps_name(self):
        directory_checkpoint = {**CHECKPOINT, "is_directory": True}
        result = build_a1111_parameters({"positive_prompt": "p"}, [directory_checkpoint])
        assert "Model hash:" not in result
        assert "Model: Krea-2" in result

    def test_directory_backed_lora_is_omitted_entirely(self):
        directory_lora = {**LORA_A, "is_directory": True}
        result = build_a1111_parameters({"positive_prompt": "p"}, [directory_lora])
        assert "Lora hashes" not in result

    def test_no_models_omits_model_and_lora_keys(self):
        result = build_a1111_parameters(FULL_PARAMETERS, [])
        assert "Model" not in result
        assert "Lora hashes" not in result

    def test_supporting_models_skipped(self):
        """VAE/text-encoder/embedding rows contribute neither Model nor Lora hashes."""
        supporting = [
            {"model_type": "vae", "name": "vae", "sha256": "1" * 40},
            {"model_type": "clip", "name": "clip", "sha256": "2" * 40},
            {"model_type": "embedding", "name": "emb", "sha256": "3" * 40},
        ]
        result = build_a1111_parameters({"positive_prompt": "p"}, supporting)
        assert "Model" not in result
        assert "Lora hashes" not in result

    def test_single_lora_hash_format(self):
        result = build_a1111_parameters({"positive_prompt": "p"}, [LORA_A])
        assert 'Lora hashes: "Detail Booster: 1122334455"' in result


class TestInjectA1111Parameters:
    def _make_png_bytes(self) -> bytes:
        image = Image.new("RGB", (4, 4), color=(255, 0, 0))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    def test_round_trip_preserves_parameters_text(self):
        original = self._make_png_bytes()
        text = "a prompt\nSteps: 20, Sampler: euler"

        exported = inject_a1111_parameters(original, text)

        with Image.open(io.BytesIO(exported)) as reopened:
            assert reopened.text["parameters"] == text

    def test_round_trip_preserves_image_pixels(self):
        original = self._make_png_bytes()
        exported = inject_a1111_parameters(original, "some text")

        with Image.open(io.BytesIO(original)) as before, Image.open(io.BytesIO(exported)) as after:
            assert before.convert("RGB").tobytes() == after.convert("RGB").tobytes()

    @pytest.mark.parametrize("text", ["", "unicode: héllo wörld 日本語"])
    def test_round_trip_handles_edge_case_text(self, text):
        original = self._make_png_bytes()
        exported = inject_a1111_parameters(original, text)

        with Image.open(io.BytesIO(exported)) as reopened:
            assert reopened.text["parameters"] == text
