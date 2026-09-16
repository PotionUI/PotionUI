from pathlib import Path

from src.features.presets.loader import PresetTemplateLoader


def _write_preset(tmp_path: Path, preset_id: str, configuration_yaml: str, form_yaml: str) -> Path:
    preset_dir = tmp_path / "presets/native/Foo/std"
    preset_dir.mkdir(parents=True)
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{preset_id}"
name: "Test"
version: "1.0.0"
category: "image"
engine: "native"
{configuration_yaml}
modes:
  - txt2img
"""
    )
    mode_dir = preset_dir / "modes" / "txt2img"
    mode_dir.mkdir(parents=True)
    (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
    (mode_dir / "form.yml").write_text(form_yaml)
    return preset_dir


class TestTagsCategoriesConfigIndirection:
    def test_declared_key_loads_cleanly(self, tmp_path):
        _write_preset(
            tmp_path, "01AAAAAAAAAAAAAAAAAAAAAAAAA",
            "configuration:\n  style_categories:\n    type: tag_categories\n",
            (
                "fields:\n  - name: style\n    type: tags\n    required: true\n"
                "    configuration:\n      categories: \"@config:style_categories\"\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.load_errors == {}
        assert len(loader.presets) == 1

    def test_undeclared_key_is_a_load_error(self, tmp_path):
        _write_preset(
            tmp_path, "01BBBBBBBBBBBBBBBBBBBBBBBBB",
            "",
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories: \"@config:style_categories\"\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "@config:style_categories" in error_text
        assert "declares no configuration entry" in error_text


class TestInlineCategoriesShape:
    def test_valid_inline_categories_load_cleanly(self, tmp_path):
        _write_preset(
            tmp_path, "01CCCCCCCCCCCCCCCCCCCCCCCCC",
            "",
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories:\n"
                "        - {key: colour, tags: [red, blue]}\n"
                "        - {key: more, multi: true, tags: []}\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.load_errors == {}
        assert len(loader.presets) == 1

    def test_duplicate_category_keys_is_a_load_error(self, tmp_path):
        _write_preset(
            tmp_path, "01DDDDDDDDDDDDDDDDDDDDDDDDD",
            "",
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories:\n"
                "        - {key: colour, tags: [red]}\n"
                "        - {key: colour, tags: [blue]}\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "duplicate category key" in error_text

    def test_non_string_tags_entry_is_a_load_error(self, tmp_path):
        _write_preset(
            tmp_path, "01EEEEEEEEEEEEEEEEEEEEEEEEE",
            "",
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories:\n"
                "        - {key: colour, tags: [1, 2]}\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "'tags' must be a list of strings" in error_text

    def test_missing_categories_entirely_is_a_load_error(self, tmp_path):
        _write_preset(
            tmp_path, "01FFFFFFFFFFFFFFFFFFFFFFFFF",
            "",
            "fields:\n  - name: style\n    type: tags\n    configuration: {}\n",
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "requires configuration.categories" in error_text


class TestConfigurationEntryDefaultShape:
    def test_bad_default_shape_is_a_load_error(self, tmp_path):
        _write_preset(
            tmp_path, "01GGGGGGGGGGGGGGGGGGGGGGGGG",
            (
                "configuration:\n  style_categories:\n    type: tag_categories\n"
                "    default:\n      - {key: colour, tags: [1]}\n"
            ),
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories: \"@config:style_categories\"\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "'tags' must be a list of strings" in error_text

    def test_valid_default_shape_loads_cleanly(self, tmp_path):
        _write_preset(
            tmp_path, "01HHHHHHHHHHHHHHHHHHHHHHHHH",
            (
                "configuration:\n  style_categories:\n    type: tag_categories\n"
                "    default:\n      - {key: colour, tags: [red]}\n"
            ),
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories: \"@config:style_categories\"\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.load_errors == {}
        assert len(loader.presets) == 1
