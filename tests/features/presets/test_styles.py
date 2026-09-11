"""`styles.yml` (docs/presets.md "Styles"): schema validation
(`validate_styles_file`) plus `PresetTemplateLoader._load_styles`'s lenient
load - unlike `preset.yml`/`pipeline.yml`/`form.yml`, a broken `styles.yml`
never blocks the whole preset from loading (see the loader method's
docstring); `PresetLinter._lint_styles` (tests/features/presets/test_linter.py)
is what surfaces those problems without booting the app.
"""
from pathlib import Path

from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.schema import validate_styles_file


def _valid_style(**overrides):
    style = {
        "id": "retro-90s-cel",
        "name": "Retro 90s Anime Cel",
        "category": "Anime",
        "prepend": "old, anime screenshot, ",
        "append": ", 1990s (style), retro artstyle.",
        "negative": "3d, realistic, glossy",
        "example_prompt": "a cat sitting on a windowsill at sunset",
    }
    style.update(overrides)
    return style


class TestValidateStylesFile:
    def test_happy_path(self):
        parsed, errors = validate_styles_file({"styles": [_valid_style()]})
        assert errors == []
        assert parsed.styles[0].id == "retro-90s-cel"
        assert parsed.styles[0].description is None
        assert parsed.styles[0].preview is None

    def test_empty_styles_list_is_valid(self):
        parsed, errors = validate_styles_file({"styles": []})
        assert errors == []
        assert parsed.styles == []

    def test_missing_styles_key_defaults_to_empty(self):
        parsed, errors = validate_styles_file({})
        assert errors == []
        assert parsed.styles == []

    def test_bad_id_rejected(self):
        parsed, errors = validate_styles_file({"styles": [_valid_style(id="Retro 90s!")]})
        assert parsed is None
        assert errors
        assert any("does not match" in e for e in errors)

    def test_duplicate_id_rejected(self):
        parsed, errors = validate_styles_file(
            {"styles": [_valid_style(), _valid_style(name="Another name")]}
        )
        assert parsed is None
        assert errors
        assert any("duplicate style id" in e for e in errors)

    def test_missing_required_field_rejected(self):
        style = _valid_style()
        del style["example_prompt"]
        parsed, errors = validate_styles_file({"styles": [style]})
        assert parsed is None
        assert errors

    def test_unknown_field_rejected(self):
        parsed, errors = validate_styles_file({"styles": [_valid_style(extra="nope")]})
        assert parsed is None
        assert errors

    def test_preview_path_shape_validated(self):
        parsed, errors = validate_styles_file(
            {"styles": [_valid_style(preview="assets/retro.webp")]}
        )
        assert parsed is None
        assert errors

    def test_valid_preview_path_accepted(self):
        parsed, errors = validate_styles_file(
            {"styles": [_valid_style(preview="public/styles/retro-90s-cel.webp")]}
        )
        assert errors == []
        assert parsed.styles[0].preview == "public/styles/retro-90s-cel.webp"

    def test_missing_preview_block_defaults_to_empty(self):
        parsed, errors = validate_styles_file({"styles": [_valid_style()]})
        assert errors == []
        assert parsed.preview.prompt_prefix == ""
        assert parsed.preview.negative == ""

    def test_preview_block_loaded(self):
        parsed, errors = validate_styles_file({
            "preview": {"prompt_prefix": "masterpiece, best quality, ", "negative": "worst quality"},
            "styles": [_valid_style()],
        })
        assert errors == []
        assert parsed.preview.prompt_prefix == "masterpiece, best quality, "
        assert parsed.preview.negative == "worst quality"

    def test_preview_block_unknown_field_rejected(self):
        parsed, errors = validate_styles_file({
            "preview": {"prompt_prefix": "x", "extra": "nope"},
            "styles": [_valid_style()],
        })
        assert parsed is None
        assert errors


def _write_preset(preset_dir: Path, preset_id: str) -> None:
    preset_dir.mkdir(parents=True)
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{preset_id}"
name: "Test"
version: "1.0.0"
category: "image"
engine: "native"
modes:
  - txt2img
"""
    )
    mode_dir = preset_dir / "modes" / "txt2img"
    mode_dir.mkdir(parents=True)
    (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
    (mode_dir / "form.yml").write_text("fields: []\n")


class TestLoaderStyles:
    def test_missing_styles_yml_yields_empty_list(self, tmp_path):
        preset_dir = tmp_path / "presets/native/Foo/std"
        _write_preset(preset_dir, "01AAAAAAAAAAAAAAAAAAAAAAAAA")

        loader = PresetTemplateLoader(str(tmp_path))
        loader.load_presets()

        preset = loader.load_preset_by_id("01AAAAAAAAAAAAAAAAAAAAAAAAA")
        assert preset is not None
        assert preset.styles == []
        assert preset.styles_preview == {"prompt_prefix": "", "negative": ""}

    def test_preview_block_loads_onto_styles_preview(self, tmp_path):
        preset_dir = tmp_path / "presets/native/Foo/std"
        _write_preset(preset_dir, "01EEEEEEEEEEEEEEEEEEEEEEEEE")
        (preset_dir / "styles.yml").write_text(
            """preview:
  prompt_prefix: "masterpiece, best quality, "
  negative: "worst quality, low quality"

styles:
  - id: "retro-90s-cel"
    name: "Retro 90s Anime Cel"
    category: "Anime"
    prepend: "old, "
    append: ", retro."
    example_prompt: "a cat"
"""
        )

        loader = PresetTemplateLoader(str(tmp_path))
        loader.load_presets()

        preset = loader.load_preset_by_id("01EEEEEEEEEEEEEEEEEEEEEEEEE")
        assert preset.styles_preview == {
            "prompt_prefix": "masterpiece, best quality, ",
            "negative": "worst quality, low quality",
        }

    def test_malformed_styles_yml_defaults_styles_preview_too(self, tmp_path):
        preset_dir = tmp_path / "presets/native/Foo/std"
        _write_preset(preset_dir, "01FFFFFFFFFFFFFFFFFFFFFFFFF")
        (preset_dir / "styles.yml").write_text(
            "styles:\n  - id: \"Bad Id!\"\n    name: x\n"
        )

        loader = PresetTemplateLoader(str(tmp_path))
        loader.load_presets()

        preset = loader.load_preset_by_id("01FFFFFFFFFFFFFFFFFFFFFFFFF")
        assert preset.styles_preview == {"prompt_prefix": "", "negative": ""}

    def test_valid_styles_yml_loads_onto_template(self, tmp_path):
        preset_dir = tmp_path / "presets/native/Foo/std"
        _write_preset(preset_dir, "01BBBBBBBBBBBBBBBBBBBBBBBBB")
        (preset_dir / "styles.yml").write_text(
            """styles:
  - id: "retro-90s-cel"
    name: "Retro 90s Anime Cel"
    category: "Anime"
    prepend: "old, "
    append: ", retro."
    example_prompt: "a cat"
"""
        )

        loader = PresetTemplateLoader(str(tmp_path))
        loader.load_presets()

        preset = loader.load_preset_by_id("01BBBBBBBBBBBBBBBBBBBBBBBBB")
        assert preset.styles == [{
            "id": "retro-90s-cel",
            "name": "Retro 90s Anime Cel",
            "category": "Anime",
            "prepend": "old, ",
            "append": ", retro.",
            "example_prompt": "a cat",
        }]

    def test_malformed_styles_yml_is_non_fatal(self, tmp_path):
        """A broken styles.yml degrades to `styles: []`; it must never take
        the whole preset down with it (unlike preset.yml/form.yml/pipeline.yml)."""
        preset_dir = tmp_path / "presets/native/Foo/std"
        _write_preset(preset_dir, "01CCCCCCCCCCCCCCCCCCCCCCCCC")
        (preset_dir / "styles.yml").write_text(
            "styles:\n  - id: \"Bad Id!\"\n    name: x\n"
        )

        loader = PresetTemplateLoader(str(tmp_path))
        loader.load_presets()

        preset = loader.load_preset_by_id("01CCCCCCCCCCCCCCCCCCCCCCCCC")
        assert preset is not None
        assert preset.styles == []
        assert not loader.load_errors

    def test_plugin_root_preset_loads_styles_the_same_way(self, tmp_path):
        """Plugin-contributed preset roots (`plugin_preset_roots`) are scanned
        by the same `_load_preset_file` code path as core presets - no
        special-casing needed for styles.yml to load there too."""
        plugin_preset_dir = tmp_path / "plugins/my-plugin/presets/native/Foo/std"
        _write_preset(plugin_preset_dir, "01DDDDDDDDDDDDDDDDDDDDDDDDD")
        (plugin_preset_dir / "styles.yml").write_text(
            """styles:
  - id: "cel"
    name: "Cel"
    category: "Anime"
    prepend: ""
    append: ""
    example_prompt: "a cat"
"""
        )

        core_dir = tmp_path / "presets"
        core_dir.mkdir(parents=True, exist_ok=True)

        loader = PresetTemplateLoader([str(core_dir), str(plugin_preset_dir.parent)])
        loader.load_presets()

        preset = loader.load_preset_by_id("01DDDDDDDDDDDDDDDDDDDDDDDDD")
        assert preset is not None
        assert preset.styles[0]["id"] == "cel"
