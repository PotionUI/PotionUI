"""`PresetTemplateLoader._validate_filter_tags_directives` - a `model`/
`lora_picker` field's `configuration.filter_tags`, and a reaction's
`then.set_filter_tags`, are validated against the preset's own declared
`configuration:` schema at LOAD time (docs/presets.md "`@config:<key>`
indirection in form fields"). An undeclared `@config:<key>`, or any other
`@`-prefixed value, is a load error - a bad reference never reaches a
running preset.

Distinct from `src/features/presets/configuration.py`'s `resolve_filter_tags`
(a pure, unconditional prefix match used at form-schema-serve time - by then
the value has already been validated here) and from
`PresetLinter._lint_configuration_refs` (the same cross-check, run without
booting the app, for `scripts/preset_lint.py`/the developer lint endpoint -
this loader check is the runtime backstop for a preset that was never linted
before shipping).

The plugin-contribution path (a mode contributed via `preset_modes:`,
checked against the TARGET preset's own declared `configuration:`) is
covered in test_plugin_preset_mode_contributions.py, next to the rest of
that mechanism's coverage.
"""

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


class TestFieldConfigurationFilterTags:
    """`configuration.filter_tags: "@config:<key>"` on a field."""

    def test_declared_key_loads_cleanly(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "01AAAAAAAAAAAAAAAAAAAAAAAAA",
            "configuration:\n  checkpoint_tags:\n    type: model_tags\n",
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "      filter_tags: \"@config:checkpoint_tags\"\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.load_errors == {}
        assert len(loader.presets) == 1

    def test_undeclared_key_is_a_load_error(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "01BBBBBBBBBBBBBBBBBBBBBBBBB",
            "",  # no configuration: block at all
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "      filter_tags: \"@config:checkpoint_tags\"\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []  # the preset failed to load
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "@config:checkpoint_tags" in error_text
        assert "checkpoint_tags" in error_text
        assert "checkpoint" in error_text  # the field name
        assert "declares no configuration entry" in error_text

    def test_key_declared_for_a_different_entry_is_still_undeclared(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "01CCCCCCCCCCCCCCCCCCCCCCCCC",
            "configuration:\n  lora_tags:\n    type: model_tags\n",
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "      filter_tags: \"@config:checkpoint_tags\"\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "checkpoint_tags" in error_text
        assert "['lora_tags']" in error_text  # names what IS declared

    def test_unrecognized_at_directive_is_a_load_error(self, tmp_path):
        """Only '@config:<key>' is valid here - '@seed' is a pipe-input:
        wiring sentinel resolved elsewhere, never a filter_tags value."""
        preset_dir = _write_preset(
            tmp_path, "01DDDDDDDDDDDDDDDDDDDDDDDDD",
            "",
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "      filter_tags: \"@seed\"\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "@seed" in error_text
        assert "not a recognized" in error_text

    def test_literal_tag_list_is_never_flagged(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "01EEEEEEEEEEEEEEEEEEEEEEEEE",
            "",
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "      filter_tags: [\"tag_1\", \"tag_2\"]\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.load_errors == {}
        assert len(loader.presets) == 1

    def test_no_filter_tags_at_all_is_never_flagged(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "01FFFFFFFFFFFFFFFFFFFFFFFFF",
            "",
            "fields:\n  - name: checkpoint\n    type: model\n    configuration:\n      model_type: checkpoint\n",
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.load_errors == {}
        assert len(loader.presets) == 1

    def test_field_inside_external_tab_fragment_is_checked_too(self, tmp_path):
        """`filter_tags` usually lives in an external tabs/*.yml fragment, not
        directly in form.yml - the check must walk the FULLY resolved field
        tree (after `_build_field_template` expands `children:`), not just
        form.yml's own inline fields."""
        preset_dir = _write_preset(
            tmp_path, "01GGGGGGGGGGGGGGGGGGGGGGGGG",
            "",  # no configuration: block
            (
                "fields:\n"
                "  - name: advanced\n"
                "    type: tab\n"
                "    children: \"{{ paths.preset }}/modes/txt2img/tabs/advanced.yml\"\n"
            ),
        )
        tabs_dir = preset_dir / "modes" / "txt2img" / "tabs"
        tabs_dir.mkdir(parents=True)
        (tabs_dir / "advanced.yml").write_text(
            "fields:\n  - name: checkpoint\n    type: model\n"
            "    configuration:\n      model_type: checkpoint\n"
            "      filter_tags: \"@config:checkpoint_tags\"\n"
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "@config:checkpoint_tags" in error_text


class TestReactionSetFilterTags:
    """`reactions[].then.set_filter_tags: "@config:<key>"` on a field."""

    def test_declared_key_loads_cleanly(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "01HHHHHHHHHHHHHHHHHHHHHHHHH",
            "configuration:\n  fast_tags:\n    type: model_tags\n",
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "    reactions:\n"
                "      - when: {field: speed_profile, equals: fast}\n"
                "        then: {set_filter_tags: \"@config:fast_tags\"}\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.load_errors == {}
        assert len(loader.presets) == 1

    def test_undeclared_key_is_a_load_error(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "01IIIIIIIIIIIIIIIIIIIIIIIII",
            "",
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "    reactions:\n"
                "      - when: {field: speed_profile, equals: fast}\n"
                "        then: {set_filter_tags: \"@config:fast_tags\"}\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.presets == []
        error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
        assert "@config:fast_tags" in error_text
        assert "reactions" in error_text
        assert "set_filter_tags" in error_text

    def test_literal_tag_list_reaction_is_never_flagged(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "01JJJJJJJJJJJJJJJJJJJJJJJJJ",
            "",
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "    reactions:\n"
                "      - when: {field: speed_profile, equals: fast}\n"
                "        then: {set_filter_tags: [\"tag_1\"]}\n"
            ),
        )
        loader = PresetTemplateLoader([str(tmp_path)])
        loader.load_presets()
        assert loader.load_errors == {}
        assert len(loader.presets) == 1
