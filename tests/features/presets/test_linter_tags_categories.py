from src.features.presets.linter import PresetLinter


def _write_preset(tmp_path, preset_id, form_yaml):
    preset_dir = tmp_path / "presets/native/Foo/std"
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
    (mode_dir / "form.yml").write_text(form_yaml)
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    return preset_dir


class TestLintTagsCategoriesShape:
    def test_valid_inline_categories_are_clean(self, tmp_path):
        _write_preset(
            tmp_path, "01AAAAAAAAAAAAAAAAAAAAAAAAA",
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories:\n"
                "        - {key: colour, tags: [red, blue]}\n"
            ),
        )
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "categories" in i.message]
        assert issues == []

    def test_duplicate_category_keys_is_an_error(self, tmp_path):
        _write_preset(
            tmp_path, "01BBBBBBBBBBBBBBBBBBBBBBBBB",
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories:\n"
                "        - {key: colour, tags: [red]}\n"
                "        - {key: colour, tags: [blue]}\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "categories" in i.message and "duplicate category key" in i.message
            for i in issues
        )

    def test_non_string_tag_is_an_error(self, tmp_path):
        _write_preset(
            tmp_path, "01CCCCCCCCCCCCCCCCCCCCCCCCC",
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories:\n"
                "        - {key: colour, tags: [1]}\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "must be a list of strings" in i.message
            for i in issues
        )

    def test_config_indirection_typo_is_caught_by_the_generic_ref_check(self, tmp_path):
        _write_preset(
            tmp_path, "01DDDDDDDDDDDDDDDDDDDDDDDDD",
            (
                "fields:\n  - name: style\n    type: tags\n"
                "    configuration:\n      categories: \"@config:style_categories\"\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "@config:style_categories" in i.message and "declares no configuration" in i.message
            for i in issues
        )
