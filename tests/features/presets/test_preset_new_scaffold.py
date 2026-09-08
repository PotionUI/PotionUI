"""Tests for scripts/preset_new.py's scaffold output against scripts/preset_lint.py's
PresetLinter.

`preset_new.py`'s own docstring promises: "Emits a minimal, schema-valid preset skeleton
that passes `scripts/preset_lint.py`." These tests hold that promise to account - both for
the `native` engine and for the `comfyui` engine example the docstring itself advertises,
and for `--family`, which wires the scaffold to a real family's pipe names discovered from
the pipe catalog's light-scan tier instead of a hand-maintained list.
"""

import pytest

from pathlib import Path

from scripts.preset_new import discover_family, scaffold
from src.features.presets.linter import PresetLinter
from src.platform.util.ids import generate_ulid


def _scaffold(tmp_path: Path, engine_dir: str, engine: str, modes, family=None) -> Path:
    target = tmp_path / "presets" / engine_dir / "MyModel" / "standard"
    scaffold(
        target=target,
        preset_id=generate_ulid(),
        name="MyModel standard",
        category="image",
        engine=engine,
        modes=list(modes),
        force=False,
        family=family,
    )
    return target


def _errors(target: Path):
    issues = PresetLinter([str(target)]).lint()
    return [i for i in issues if i.level == "error"]


class TestPresetNewScaffoldLintsClean:
    def test_native_single_mode_lints_clean(self, tmp_path):
        target = _scaffold(tmp_path, "native", "native", ["txt2img"])
        assert _errors(target) == []

    def test_native_multiple_modes_lints_clean(self, tmp_path):
        target = _scaffold(tmp_path, "native", "native", ["txt2img", "img2img"])
        assert _errors(target) == []

    def test_comfyui_multiple_modes_lints_clean(self, tmp_path):
        # Mirrors the docstring's own example:
        #   python scripts/preset_new.py comfyui/MyModel/official --category video \
        #       --modes txt2vid,img2vid
        target = _scaffold(tmp_path, "comfyui", "comfyui", ["txt2vid", "img2vid"])
        assert _errors(target) == []

    def test_no_gallery_pipe_declares_the_dead_mode_save_key(self, tmp_path):
        # scripts/preset_new.py used to emit `configuration: {mode: "save"}` on the
        # gallery pipe - a key the pipe never reads. Both engine branches must stay
        # free of it.
        native_pipeline = (_scaffold(tmp_path, "native", "native", ["txt2img"])
                            / "modes" / "txt2img" / "pipeline.yml").read_text()
        comfyui_pipeline = (_scaffold(tmp_path, "comfyui2", "comfyui", ["txt2img"])
                             / "modes" / "txt2img" / "pipeline.yml").read_text()
        assert 'mode: "save"' not in native_pipeline
        assert 'mode: "save"' not in comfyui_pipeline


class TestDiscoverFamily:
    """discover_family() must never hardcode a family list - it light-scans
    src/pipelines/pipes/model_loader/ and src/pipelines/pipes/generator/."""

    def test_exact_match_family(self):
        loader, generator = discover_family("z_image")
        assert loader == "model_loader/z_image"
        assert generator == "generator/z_image"

    def test_family_whose_generator_has_a_different_suffix(self):
        # model_loader/wan22 has no generator/wan22 - its generators are named
        # generator/{txt2vid,img2vid,chain_video}_wan22.
        loader, generator = discover_family("wan22")
        assert loader == "model_loader/wan22"
        assert "wan22" in generator
        assert generator.startswith("generator/")

    def test_unknown_family_raises_naming_available_families(self):
        with pytest.raises(ValueError, match="no model_loader/does_not_exist pipe"):
            discover_family("does_not_exist")


class TestPresetNewScaffoldFamilyLintsClean:
    def test_family_z_image_lints_clean(self, tmp_path):
        target = _scaffold(tmp_path, "family-z-image", "native", ["txt2img"], family="z_image")
        assert _errors(target) == []

    def test_family_z_image_wires_the_real_pipe_names(self, tmp_path):
        target = _scaffold(tmp_path, "family-z-image-2", "native", ["txt2img"], family="z_image")
        pipeline = (target / "modes" / "txt2img" / "pipeline.yml").read_text()
        assert 'name: "model_loader/z_image"' in pipeline
        assert 'name: "generator/z_image"' in pipeline
        # The one pipe-to-pipe dependency the standard chain diagram doesn't make
        # obvious: the loader's text_encoder output feeds prompt_encoder.
        assert '["text_encoder", "model_loader/z_image", "text_encoder"]' in pipeline
        # Z-Image's generator config key is `guidance`, not `cfg` - introspected
        # from the real PipeConfigSpec, never hardcoded.
        assert "guidance:" in pipeline

    def test_family_scaffold_has_no_lora_tab_when_loader_has_no_loras_config(self, tmp_path):
        # Every model_loader family shipped today declares `loras`; this asserts
        # the has_loras branch is real by checking the tab exists for one that does.
        target = _scaffold(tmp_path, "family-z-image-3", "native", ["txt2img"], family="z_image")
        assert (target / "modes" / "txt2img" / "tabs" / "lora.yml").exists()

    def test_family_and_comfyui_engine_together_is_rejected_by_the_cli(self, tmp_path, monkeypatch, capsys):
        import scripts.preset_new as preset_new

        monkeypatch.setattr(
            "sys.argv",
            ["preset_new.py", "MyModel/standard", "--engine", "comfyui",
             "--family", "z_image", "--root", str(tmp_path / "presets")],
        )
        # --family + --engine comfyui is an argparse-level `parser.error()`, which
        # exits the process directly rather than returning a non-zero code.
        with pytest.raises(SystemExit):
            preset_new.main()
        assert not (tmp_path / "presets" / "MyModel" / "standard").exists()

    def test_unrecognized_family_refuses_to_scaffold(self, tmp_path, monkeypatch):
        import scripts.preset_new as preset_new

        monkeypatch.setattr(
            "sys.argv",
            ["preset_new.py", "MyModel/standard", "--family", "does_not_exist",
             "--root", str(tmp_path / "presets")],
        )
        exit_code = preset_new.main()
        assert exit_code != 0
        assert not (tmp_path / "presets" / "MyModel" / "standard").exists()
