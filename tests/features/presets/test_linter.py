"""Tests for src.features.presets.linter.PresetLinter."""

import pytest

from src.features.presets.linter import PresetLinter


def _write_preset(tmp_path, rel_dir, preset_id, modes, extra_yaml="", with_tests_yml=True):
    preset_dir = tmp_path / rel_dir
    preset_dir.mkdir(parents=True, exist_ok=True)
    modes_yaml = "\n".join(f"  - {m}" for m in modes)
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{preset_id}"
name: "Test Preset"
version: "1.0.0"
category: "image"
engine: "native"
{extra_yaml}
modes:
{modes_yaml}
"""
    )
    for mode in modes:
        mode_dir = preset_dir / "modes" / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
    # A clean, case-less tests.yml so tests unrelated to the tests.yml lint
    # rules (TestLintTestsYml below) don't have to account for the
    # "no tests.yml" informational warning every other lint check would
    # otherwise pick up. Tests that specifically want that warning pass
    # with_tests_yml=False.
    if with_tests_yml:
        (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    return preset_dir


class TestPresetLinter:
    def test_clean_preset_has_no_issues(self, tmp_path):
        _write_preset(tmp_path, "presets/native/Foo/std", "01AAAAAAAAAAAAAAAAAAAAAAAAA", ["txt2img"])
        issues = PresetLinter([str(tmp_path)]).lint()
        assert issues == []

    def test_invalid_manifest_reported_as_error(self, tmp_path):
        preset_dir = tmp_path / "presets/broken"
        preset_dir.mkdir(parents=True)
        (preset_dir / "preset.yml").write_text("id: bad\nname: X\nversion: 1.0.0\n")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" for i in issues)

    def test_missing_mode_directory_is_error(self, tmp_path):
        preset_dir = tmp_path / "presets/native/Foo/std"
        preset_dir.mkdir(parents=True)
        (preset_dir / "preset.yml").write_text(
            """schema: 1
id: "01BBBBBBBBBBBBBBBBBBBBBBBBB"
name: "Test"
version: "1.0.0"
category: "image"
engine: "native"
modes:
  - txt2img
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "modes/txt2img" in i.message for i in issues)

    def test_orphaned_mode_directory_is_warning(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "presets/native/Foo/std", "01CCCCCCCCCCCCCCCCCCCCCCCCC", ["txt2img"]
        )
        orphan_dir = preset_dir / "modes" / "img2img"
        orphan_dir.mkdir(parents=True)
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "warning" and "img2img" in i.message for i in issues)

    def test_duplicate_id_across_presets_is_error(self, tmp_path):
        _write_preset(tmp_path, "presets/native/A/std", "01DDDDDDDDDDDDDDDDDDDDDDDDD", ["txt2img"])
        _write_preset(tmp_path, "presets/native/B/std", "01DDDDDDDDDDDDDDDDDDDDDDDDD", ["txt2img"])
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "duplicate id" in i.message for i in issues)

    def test_missing_option_file_reference_is_warning(self, tmp_path):
        preset_dir = _write_preset(
            tmp_path, "presets/native/Foo/std", "01EEEEEEEEEEEEEEEEEEEEEEEEE", ["txt2img"]
        )
        form_dir = preset_dir / "modes" / "txt2img"
        form_dir.mkdir(parents=True, exist_ok=True)
        (form_dir / "form.yml").write_text(
            """name: custom
fields:
  - name: resolution
    type: resolution
    configuration:
      files:
        - { path: "files/form/does_not_exist.yml", group: "X" }
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "warning" and "does_not_exist.yml" in i.message for i in issues)

    def test_nonexistent_path_is_skipped_silently(self):
        issues = PresetLinter(["/no/such/path"]).lint()
        assert issues == []


class TestLintCameraShotFields:
    """`camera_shot` vocabulary/category keys must be in the canonical taxonomy."""

    @staticmethod
    def _write_form(preset_dir, mode, form_yaml):
        form_file = preset_dir / "modes" / mode / "form.yml"
        form_file.parent.mkdir(parents=True, exist_ok=True)
        form_file.write_text(form_yaml)

    def test_valid_camera_shot_config_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01CAMERAOKAAAAAAAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: camera
    type: camera_shot
    label: Camera
    configuration:
      categories: [angle, distance, orientation]
      vocabulary:
        overhead: "from the ceiling"
        over_shoulder: "over-the-shoulder shot"
""")
        camera_issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "camera_shot" in i.message]
        assert camera_issues == []

    def test_unknown_vocabulary_key_is_warning(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01CAMERABADAAAAAAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: camera
    type: camera_shot
    label: Camera
    configuration:
      vocabulary:
        over_the_shoulder: "typo of the real key"
""")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning" and "unknown shot key 'over_the_shoulder'" in i.message
            for i in issues
        )

    def test_unknown_category_is_warning(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01CAMERACATBADAAAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: camera
    type: camera_shot
    label: Camera
    configuration:
      categories: [angle, bogus]
""")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning" and "unknown category 'bogus'" in i.message
            for i in issues
        )


class TestLintFieldConfigKeys:
    """A field's `configuration:` keys must be declared in its type's
    `FieldConfigSpec` list. See tests/features/fields/test_alert.py for the
    field-mapping side of the alert `type`/`message` authoring bug that
    motivated this check."""

    @staticmethod
    def _write_form(preset_dir, mode, form_yaml):
        form_file = preset_dir / "modes" / mode / "form.yml"
        form_file.parent.mkdir(parents=True, exist_ok=True)
        form_file.write_text(form_yaml)

    def test_declared_keys_are_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01FIELDCFGOKAAAAAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: strength
    type: slider
    label: Strength
    configuration:
      min: 0
      max: 10
      step: 1
""")
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "FieldConfigSpec" in i.message]
        assert issues == []

    def test_undeclared_key_is_warning(self, tmp_path):
        """Bite-check: a deliberately misauthored key (`description:` nested
        inside `configuration:` instead of at the field's top level, the most
        common real-world instance the audit for this check turned up) must be
        caught."""
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01FIELDCFGBADAAAAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: strength
    type: slider
    label: Strength
    configuration:
      min: 0
      max: 10
      description: "This belongs at the field's top level, not here"
""")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning"
            and "type 'slider'" in i.message
            and "['description']" in i.message
            for i in issues
        )

    def test_fixing_the_key_clears_the_warning(self, tmp_path):
        """Second half of the bite-check: moving `description:` to the field's
        top level (its correct location) makes the warning go away."""
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01FIELDCFGFIXAAAAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: strength
    type: slider
    label: Strength
    description: "This belongs here"
    configuration:
      min: 0
      max: 10
""")
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "FieldConfigSpec" in i.message]
        assert issues == []

    def test_alert_variant_key_is_clean(self, tmp_path):
        """The alert fix itself: `configuration.variant` (not `type`) and
        `configuration.content` (not `message`) are the declared keys."""
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01FIELDCFGALERTOKAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - type: alert
    configuration:
      variant: warning
      content: "Heads up"
""")
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "FieldConfigSpec" in i.message]
        assert issues == []

    def test_alert_type_key_is_warning(self, tmp_path):
        """The exact shape of the original alert bug: `configuration.type`
        (the pre-fix, wrong key name) must be caught by this check too."""
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01FIELDCFGALERTBADAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - type: alert
    configuration:
      type: warning
      content: "Heads up"
""")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning" and "type 'alert'" in i.message and "'type'" in i.message
            for i in issues
        )

    def test_unknown_field_type_is_skipped(self, tmp_path):
        """A type with no backend schema class (unregistered/plugin-only, or a
        typo) has no declared contract to check against - never flagged."""
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01FIELDCFGUNKNOWNAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: mystery
    type: some_plugin_field_type
    configuration:
      anything: goes
""")
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "FieldConfigSpec" in i.message]
        assert issues == []

    def test_no_backend_class_type_is_skipped(self, tmp_path):
        """`textbox`/`string`/`number`/`integer` fall through to `DefaultField`
        (no schema class, no declared spec at all) - also never flagged."""
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01FIELDCFGNOCLASSAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: note
    type: textbox
    configuration:
      placeholder: "whatever you like"
""")
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "FieldConfigSpec" in i.message]
        assert issues == []


class TestLintAlertFieldConfig:
    """`alert` fields must use the declared `variant`/`content` keys, not the
    pre-fix `type`/`message` shape `alert.py` never reads. Scoped to `alert`
    alone (unlike `TestLintFieldConfigKeys`'s general, warning-severity check)
    so it can be error-severity: the tree is verified clean of this shape."""

    @staticmethod
    def _write_form(preset_dir, mode, form_yaml):
        form_file = preset_dir / "modes" / mode / "form.yml"
        form_file.parent.mkdir(parents=True, exist_ok=True)
        form_file.write_text(form_yaml)

    def test_variant_and_content_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01ALERTLINTOKAAAAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - type: alert
    configuration:
      variant: warning
      content: "Heads up"
""")
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "alert field" in i.message]
        assert issues == []

    def test_content_only_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01ALERTLINTCONTENTAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - type: alert
    configuration:
      content: "Heads up"
""")
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "alert field" in i.message]
        assert issues == []

    def test_missing_variant_and_content_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01ALERTLINTEMPTYAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - type: alert
    configuration:
      title: "Notice"
""")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "neither 'variant' nor 'content'" in i.message
            for i in issues
        )

    def test_message_key_is_error(self, tmp_path):
        """The exact shape of the original bug: `message:` instead of
        `content:` - silently dropped by `alert.py`, so this must be a hard
        error, not a warning."""
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01ALERTLINTMSGAAAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: bad_alert
    type: alert
    configuration:
      variant: warning
      message: "Careful with this setting."
""")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "bad_alert" in i.message and "'message'" in i.message
            for i in issues
        )

    def test_inner_type_key_is_error(self, tmp_path):
        """The other half of the original bug: `configuration.type` colliding
        with the field's own outer `type: alert` discriminator."""
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01ALERTLINTTYPEAAAAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: bad_alert
    type: alert
    configuration:
      type: warning
      content: "Careful with this setting."
""")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "bad_alert" in i.message and "'type'" in i.message
            for i in issues
        )

    def test_original_bug_shape_is_error(self, tmp_path):
        """Bite-check in test form: the exact pre-fix shape (`type:` + `message:`,
        no `variant:`/`content:` at all) must trip both checks."""
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01ALERTLINTFULLBUGAAAAAAAAA", ["txt2img"])
        self._write_form(preset_dir, "txt2img", """name: custom
fields:
  - name: original_bug_alert
    type: alert
    configuration:
      type: warning
      message: "Careful with this setting."
""")
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "original_bug_alert" in i.message]
        assert len(issues) == 2
        assert all(i.level == "error" for i in issues)


class TestLintMediaRefs:
    """`media:` cross-checks the schema deliberately cannot make (see linter docstring)."""

    MEDIA = """
media:
  cover: "public/cover.png"
  gallery:
    - src: "public/examples/a.png"
      mode: "txt2img"
"""

    @staticmethod
    def _write_image(path, size=(32, 32)):
        from PIL import Image
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, "red").save(path)

    def test_media_files_present_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "media_ok", ["txt2img"], extra_yaml=self.MEDIA)
        self._write_image(preset_dir / "public" / "cover.png")
        self._write_image(preset_dir / "public" / "examples" / "a.png")

        assert PresetLinter([str(tmp_path)]).lint() == []

    def test_missing_cover_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "media_nocover", ["txt2img"], extra_yaml=self.MEDIA)
        self._write_image(preset_dir / "public" / "examples" / "a.png")

        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "media.cover" in i.message and "cover.png" in i.message
                   for i in issues)

    def test_missing_gallery_src_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "media_nogal", ["txt2img"], extra_yaml=self.MEDIA)
        self._write_image(preset_dir / "public" / "cover.png")

        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "media.gallery[0]" in i.message for i in issues)

    def test_gallery_mode_not_declared_is_warning(self, tmp_path):
        media = self.MEDIA.replace('mode: "txt2img"', 'mode: "img2img"')
        preset_dir = _write_preset(tmp_path, "p", "media_badmode", ["txt2img"], extra_yaml=media)
        self._write_image(preset_dir / "public" / "cover.png")
        self._write_image(preset_dir / "public" / "examples" / "a.png")

        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "warning" and "img2img" in i.message for i in issues)
        # A mode mismatch must never make the preset unloadable.
        assert not any(i.level == "error" for i in issues)

    def test_oversized_dimensions_is_warning(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "media_big", ["txt2img"], extra_yaml=self.MEDIA)
        self._write_image(preset_dir / "public" / "cover.png", size=(5000, 10))
        self._write_image(preset_dir / "public" / "examples" / "a.png")

        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "warning" and "longest side" in i.message for i in issues)

    def test_preset_without_media_is_unaffected(self, tmp_path):
        _write_preset(tmp_path, "p", "no_media", ["txt2img"])
        assert PresetLinter([str(tmp_path)]).lint() == []


class TestLintEngineMatchesPipes:
    def test_comfyui_engine_without_comfyui_pipe_is_error(self, tmp_path):
        preset_dir = tmp_path / "presets/comfyui/Foo/std"
        preset_dir.mkdir(parents=True)
        (preset_dir / "preset.yml").write_text(
            """schema: 1
id: "01FFFFFFFFFFFFFFFFFFFFFFFFF"
name: "Test"
version: "1.0.0"
category: "image"
engine: "comfyui"
modes:
  - txt2img
"""
        )
        mode_dir = preset_dir / "modes" / "txt2img"
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text(
            "pipeline:\n  - name: generator/sdxl\n"
        )

        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "no pipe named 'comfyui'" in i.message
            for i in issues
        )

    def test_native_engine_with_comfyui_pipe_is_error(self, tmp_path):
        preset_dir = tmp_path / "presets/native/Foo/std"
        preset_dir.mkdir(parents=True)
        (preset_dir / "preset.yml").write_text(
            """schema: 1
id: "01GGGGGGGGGGGGGGGGGGGGGGGGG"
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
        (mode_dir / "pipeline.yml").write_text(
            "pipeline:\n  - name: comfyui\n"
        )

        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "declares a 'comfyui' pipe" in i.message
            for i in issues
        )


class TestLintNagMirror:
    def test_nag_scale_mirrored_on_prompt_encoder_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01NAGOKAAAAAAAAAAAAAAAAAAAA", ["txt2img"])
        (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text(
            """pipeline:
  - name: prompt_encoder
    configuration:
      nag_scale: 1.3
  - name: generator/txt2vid_wan22
    configuration:
      nag_scale: 1.3
"""
        )
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "nag_scale" in i.message]
        assert issues == []

    def test_nag_scale_without_prompt_encoder_mirror_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01NAGBADAAAAAAAAAAAAAAAAAAA", ["txt2img"])
        (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text(
            """pipeline:
  - name: prompt_encoder
    configuration:
      guidance_scale: 1.0
  - name: generator/txt2vid_wan22
    configuration:
      nag_scale: 1.3
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "nag_scale" in i.message and "generator/txt2vid_wan22" in i.message
            for i in issues
        )

    def test_no_prompt_encoder_pipe_at_all_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01NAGNOENCAAAAAAAAAAAAAAAAA", ["txt2img"])
        (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text(
            """pipeline:
  - name: generator/video_ltx
    configuration:
      nag_scale: 1.3
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "nag_scale" in i.message for i in issues)


class TestLintSlgRiflexInert:
    def test_wan_generator_with_slg_and_riflex_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01SLGOKAAAAAAAAAAAAAAAAAAAA", ["txt2img"])
        (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text(
            """pipeline:
  - name: generator/txt2vid_wan22
    configuration:
      slg_scale: 3.0
      slg_layers: "9"
      riflex: true
"""
        )
        issues = [i for i in PresetLinter([str(tmp_path)]).lint() if "silently inert" in i.message]
        assert issues == []

    def test_non_wan_generator_with_slg_is_warning(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01SLGBADAAAAAAAAAAAAAAAAAAA", ["txt2img"])
        (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text(
            """pipeline:
  - name: generator/video_ltx
    configuration:
      slg_scale: 3.0
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning" and "slg_scale" in i.message and "silently inert" in i.message
            for i in issues
        )

    def test_non_wan_generator_with_riflex_is_warning(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01RIFLEXBADAAAAAAAAAAAAAAAA", ["txt2img"])
        (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text(
            """pipeline:
  - name: generator/krea2
    configuration:
      riflex: true
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning" and "riflex" in i.message and "silently inert" in i.message
            for i in issues
        )


class TestLintSpeedProfiles:
    """`speed_profiles:` (roadmap 3.6): unknown-key + unreferenced-block warnings."""

    def _write(self, tmp_path, preset_id, speed_profiles_yaml, pipeline_yaml="pipeline: []\n",
               form_yaml=None):
        preset_dir = tmp_path / "presets/native/Foo/std"
        preset_dir.mkdir(parents=True)
        (preset_dir / "preset.yml").write_text(
            f"""schema: 1
id: "{preset_id}"
name: "Test"
version: "1.0.0"
category: "image"
engine: "native"
{speed_profiles_yaml}
modes:
  - txt2img
"""
        )
        mode_dir = preset_dir / "modes" / "txt2img"
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text(pipeline_yaml)
        if form_yaml is not None:
            (mode_dir / "form.yml").write_text(form_yaml)
        return preset_dir

    def test_no_speed_profiles_is_silent(self, tmp_path):
        self._write(tmp_path, "01HHHHHHHHHHHHHHHHHHHHHHHHH", "")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("speed_profiles" in i.message for i in issues)

    def test_unknown_key_is_a_warning(self, tmp_path):
        self._write(
            tmp_path, "01IIIIIIIIIIIIIIIIIIIIIIIII",
            "speed_profiles:\n  draft:\n    steps: 6\n    made_up_key: 123\n",
            pipeline_yaml="pipeline:\n  - name: generator\n    configuration:\n      x: \"{{ get_speed_profile('draft') }}\"\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning" and "made_up_key" in i.message and "speed_profiles.draft" in i.message
            for i in issues
        )

    def test_extra_bag_key_is_not_flagged(self, tmp_path):
        self._write(
            tmp_path, "01JJJJJJJJJJJJJJJJJJJJJJJJJ",
            "speed_profiles:\n  draft:\n    steps: 6\n    extra:\n      anything: true\n",
            pipeline_yaml="pipeline:\n  - name: generator\n    configuration:\n      x: \"{{ get_speed_profile('draft') }}\"\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("unknown key" in i.message for i in issues)

    def test_declared_but_unreferenced_is_a_warning(self, tmp_path):
        self._write(
            tmp_path, "01KKKKKKKKKKKKKKKKKKKKKKKKK",
            "speed_profiles:\n  draft:\n    steps: 6\n",
            pipeline_yaml="pipeline:\n  - name: generator\n    configuration:\n      steps: 20\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning" and "no form field or pipeline.yml" in i.message
            for i in issues
        )

    def test_referenced_via_get_speed_profile_call_is_not_flagged(self, tmp_path):
        self._write(
            tmp_path, "01LLLLLLLLLLLLLLLLLLLLLLLLL",
            "speed_profiles:\n  draft:\n    steps: 6\n",
            pipeline_yaml=(
                "pipeline:\n  - name: generator\n    configuration:\n"
                "      steps: \"{{ get_speed_profile('draft')['steps'] }}\"\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("no form field or pipeline.yml" in i.message for i in issues)

    def test_referenced_via_literal_profile_name_in_form_is_not_flagged(self, tmp_path):
        self._write(
            tmp_path, "01MMMMMMMMMMMMMMMMMMMMMMMMM",
            "speed_profiles:\n  draft:\n    steps: 6\n  standard:\n    steps: 28\n",
            form_yaml=(
                "name: custom\n"
                "fields:\n"
                "  - name: speed_profile\n"
                "    type: select\n"
                "    configuration:\n"
                "      options:\n"
                "        - { value: \"draft\", label: \"Draft\" }\n"
                "        - { value: \"standard\", label: \"Standard\" }\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("no form field or pipeline.yml" in i.message for i in issues)

    def test_referenced_via_direct_jinja_dot_access_is_not_flagged(self, tmp_path):
        # P9 regression: preset.speed_profiles.draft (a direct Jinja lookup on
        # the manifest object, no get_speed_profile() call and no quoted
        # profile-name literal) must still be recognized as a reference.
        self._write(
            tmp_path, "01NNNNNNNNNNNNNNNNNNNNNNNNN",
            "speed_profiles:\n  draft:\n    steps: 6\n",
            pipeline_yaml=(
                "pipeline:\n  - name: generator\n    configuration:\n"
                "      steps: \"{{ preset.speed_profiles.draft.steps }}\"\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("no form field or pipeline.yml" in i.message for i in issues)

    def test_referenced_via_direct_jinja_subscript_access_is_not_flagged(self, tmp_path):
        self._write(
            tmp_path, "01OOOOOOOOOOOOOOOOOOOOOOOOO",
            "speed_profiles:\n  draft:\n    steps: 6\n",
            pipeline_yaml=(
                "pipeline:\n  - name: generator\n    configuration:\n"
                "      steps: \"{{ preset.speed_profiles['draft'].steps }}\"\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("no form field or pipeline.yml" in i.message for i in issues)


class TestLintConfigurationRefs:
    """`"@config:<key>"` indirection (e.g. a `model` field's `filter_tags:`) must
    reference a key preset.yml's `configuration:` block actually declares."""

    def _write(self, tmp_path, preset_id, configuration_yaml, form_yaml):
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

    def test_declared_key_is_clean(self, tmp_path):
        self._write(
            tmp_path, "01PPPPPPPPPPPPPPPPPPPPPPPPP",
            "configuration:\n  checkpoint_tags:\n    type: model_tags\n",
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "      filter_tags: \"@config:checkpoint_tags\"\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("@config:" in i.message for i in issues)

    def test_undeclared_key_is_error(self, tmp_path):
        self._write(
            tmp_path, "01QQQQQQQQQQQQQQQQQQQQQQQQQ",
            "",
            (
                "fields:\n  - name: checkpoint\n    type: model\n"
                "    configuration:\n      model_type: checkpoint\n"
                "      filter_tags: \"@config:checkpoint_tags\"\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "@config:checkpoint_tags" in i.message and "no configuration entry" in i.message
            for i in issues
        )

    def test_no_config_refs_is_silent(self, tmp_path):
        self._write(
            tmp_path, "01RRRRRRRRRRRRRRRRRRRRRRRRR",
            "",
            "fields:\n  - name: checkpoint\n    type: model\n    configuration:\n      model_type: checkpoint\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("@config:" in i.message for i in issues)

    def test_undeclared_key_in_external_tab_file_is_error(self, tmp_path):
        """`@config:` refs usually live in external tabs/*.yml, not form.yml -
        the linter must scan every YAML under the form dir (regression: it
        originally only read form.yml and silently skipped tab files)."""
        preset_dir = self._write(
            tmp_path, "01SSSSSSSSSSSSSSSSSSSSSSSSS",
            "",
            (
                "fields:\n  - type: tabs\n    children:\n"
                "      - type: tab\n        label: LoRA\n"
                "        children: \"{{ paths.preset }}/modes/txt2img/tabs/lora.yml\"\n"
            ),
        )
        tabs_dir = preset_dir / "modes/txt2img/tabs"
        tabs_dir.mkdir(parents=True)
        (tabs_dir / "lora.yml").write_text(
            "fields:\n  - name: loras\n    type: lora_picker\n"
            "    configuration:\n      model_type: lora\n"
            "      filter_tags: \"@config:lora_tags\"\n"
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "@config:lora_tags" in i.message and "tabs/lora.yml" in i.message
            for i in issues
        )


class TestLintTestsYml:
    """`tests.yml` cross-checks (task #45): duplicate case names, sha256
    format, mode-not-declared, and form/models key collisions - the four
    rules the schema itself can't express (see PresetLinter._lint_tests_yml
    docstring for why each needs sibling-case or cross-file context)."""

    VALID_SHA = "a" * 64
    OTHER_VALID_SHA = "b" * 64

    def test_missing_tests_yml_is_informational_warning(self, tmp_path):
        _write_preset(tmp_path, "p", "no_tests_yml", ["txt2img"], with_tests_yml=False)
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "warning" and "no tests.yml" in i.message for i in issues)

    def test_present_but_empty_tests_yml_has_no_warning(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "empty_tests_yml", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert issues == []

    def test_malformed_yaml_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "bad_yaml", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text("schema: 1\ncases: [\n")  # unterminated flow seq
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "tests.yml" in i.message for i in issues)

    def test_schema_validation_error_is_reported(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "bad_case", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text(
            "schema: 1\ncases:\n  - name: no-seed\n    mode: txt2img\n"
        )  # seed is required
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "no-seed" in i.message and "seed" in i.message for i in issues)

    def test_duplicate_case_names_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "dup_names", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text(
            "schema: 1\ncases:\n"
            "  - name: fast-case\n    mode: txt2img\n    seed: 1\n"
            "  - name: fast-case\n    mode: txt2img\n    seed: 2\n"
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "duplicates the name" in i.message and "fast-case" in i.message
            for i in issues
        )

    def test_unique_case_names_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "unique_names", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text(
            "schema: 1\ncases:\n"
            "  - name: fast-case-a\n    mode: txt2img\n    seed: 1\n"
            "  - name: fast-case-b\n    mode: txt2img\n    seed: 2\n"
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("duplicates the name" in i.message for i in issues)

    def test_mode_not_declared_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "bad_mode", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text(
            "schema: 1\ncases:\n  - name: wrong-mode\n    mode: img2img\n    seed: 1\n"
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "wrong-mode" in i.message and "img2img" in i.message
            for i in issues
        )

    def test_declared_mode_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "good_mode", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text(
            "schema: 1\ncases:\n  - name: right-mode\n    mode: txt2img\n    seed: 1\n"
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert issues == []

    def test_form_key_colliding_with_models_key_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "collide", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text(
            f"""schema: 1
cases:
  - name: colliding-case
    mode: txt2img
    seed: 1
    form:
      diffusion_model: "some/literal/path.safetensors"
    models:
      diffusion_model:
        sha256: "{self.VALID_SHA}"
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "colliding-case" in i.message and "diffusion_model" in i.message
            for i in issues
        )

    def test_non_colliding_form_and_models_keys_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "no_collide", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text(
            f"""schema: 1
cases:
  - name: clean-case
    mode: txt2img
    seed: 1
    form:
      steps: 8
    models:
      diffusion_model:
        sha256: "{self.VALID_SHA}"
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert issues == []

    def test_malformed_sha256_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "bad_sha", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text(
            """schema: 1
cases:
  - name: bad-sha-case
    mode: txt2img
    seed: 1
    models:
      diffusion_model:
        sha256: "not-a-real-hash"
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "bad-sha-case" in i.message and "64 hex" in i.message
            for i in issues
        )

    def test_valid_sha256_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "good_sha", ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text(
            f"""schema: 1
cases:
  - name: good-sha-case
    mode: txt2img
    seed: 1
    models:
      diffusion_model:
        sha256: "{self.VALID_SHA}"
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert issues == []

    def test_placeholder_sha_without_needs_model_tag_is_warning(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "placeholder_untagged", ["txt2img"], with_tests_yml=False)
        placeholder = "0" * 64
        (preset_dir / "tests.yml").write_text(
            f"""schema: 1
cases:
  - name: placeholder-case
    mode: txt2img
    seed: 1
    models:
      diffusion_model:
        sha256: "{placeholder}"
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning" and "placeholder sha256" in i.message and "needs-model" in i.message
            for i in issues
        )

    def test_placeholder_sha_with_needs_model_tag_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "placeholder_tagged", ["txt2img"], with_tests_yml=False)
        placeholder = "0" * 64
        (preset_dir / "tests.yml").write_text(
            f"""schema: 1
cases:
  - name: placeholder-case
    mode: txt2img
    seed: 1
    tags: ["fast", "needs-model"]
    models:
      diffusion_model:
        sha256: "{placeholder}"
"""
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("placeholder sha256" in i.message for i in issues)


class TestLintPipelineTemplates:
    """Structured pipeline.yml checks for the post-rework template contract:
    - (a) a string `enabled:` must be an exact `{{ expr }}` (else it renders a
      string, never a bool, and the pipe never runs);
    - (c) a config-expansion `@loop` `items:` must be an exact `{{ expr }}`;
    - (d) deleted template context (`get_form(`, `@object:`, `setting(`,
      `input.*`, ...) is a hard build error under strict eval;
    - `{{ form.<name> }}` references must name a real field (in the mode's form
      tree, incl. external tabs and `@loop`-generated names) or carry a
      `| default(...)`."""

    def _write_mode(self, tmp_path, preset_id, pipeline_yaml, form_yaml=None, tabs=None):
        preset_dir = _write_preset(tmp_path, "p", preset_id, ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
        mode_dir = preset_dir / "modes" / "txt2img"
        (mode_dir / "pipeline.yml").write_text(pipeline_yaml)
        if form_yaml is not None:
            (mode_dir / "form.yml").write_text(form_yaml)
        for name, content in (tabs or {}).items():
            tab_path = mode_dir / "tabs" / name
            tab_path.parent.mkdir(parents=True, exist_ok=True)
            tab_path.write_text(content)
        return preset_dir

    # --- (a) enabled: exact-expression contract -------------------------------

    def test_string_enabled_not_exact_expression_is_error(self, tmp_path):
        self._write_mode(
            tmp_path, "enb_mixed",
            "pipeline:\n  - name: upscaler\n"
            "    enabled: \"{{ form.a }} and {{ form.b }}\"\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "enabled:" in i.message and "not an exact" in i.message
            for i in issues
        )

    def test_string_enabled_statement_tag_is_error(self, tmp_path):
        self._write_mode(
            tmp_path, "enb_stmt",
            "pipeline:\n  - name: upscaler\n"
            "    enabled: \"{% if form.x %}true{% else %}false{% endif %}\"\n",
            form_yaml="name: custom\nfields:\n  - {type: checkbox, name: x, default: false}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "enabled:" in i.message for i in issues)

    def test_exact_expression_enabled_is_clean(self, tmp_path):
        self._write_mode(
            tmp_path, "enb_ok",
            "pipeline:\n  - name: upscaler\n"
            "    enabled: \"{{ form.x | default(false) }}\"\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("enabled:" in i.message for i in issues)

    def test_yaml_bool_enabled_is_clean(self, tmp_path):
        self._write_mode(
            tmp_path, "enb_bool",
            "pipeline:\n  - name: upscaler\n    enabled: false\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("enabled:" in i.message for i in issues)

    # --- (c) @loop items exact-expression contract ----------------------------

    def test_loop_items_string_template_is_error(self, tmp_path):
        self._write_mode(
            tmp_path, "loop_bad",
            "pipeline:\n  - name: loader\n"
            "    configuration:\n"
            "      loras:\n"
            "        \"@loop\":\n"
            "          items: \"count is {{ form.n }}\"\n"
            "          template: {file_path: \"{{ item.model }}\"}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "@loop.items" in i.message for i in issues)

    def test_loop_items_literal_list_is_clean(self, tmp_path):
        # _resolve_loop_items accepts a native YAML list as-is - only a string
        # value must be an exact expression.
        self._write_mode(
            tmp_path, "loop_literal",
            "pipeline:\n  - name: loader\n"
            "    configuration:\n"
            "      loras:\n"
            "        \"@loop\":\n"
            "          items: [1, 2, 3]\n"
            "          template: {v: \"{{ item }}\"}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("@loop.items" in i.message for i in issues)

    def test_loop_items_exact_expression_is_clean(self, tmp_path):
        self._write_mode(
            tmp_path, "loop_ok",
            "pipeline:\n  - name: loader\n"
            "    configuration:\n"
            "      loras:\n"
            "        \"@loop\":\n"
            "          items: \"{{ form.loras | default([]) }}\"\n"
            "          template: {file_path: \"{{ item.model }}\"}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("@loop.items" in i.message for i in issues)

    # --- (d) deleted-context migration guard ----------------------------------

    @pytest.mark.parametrize("expr,token", [
        ("{{ get_form('custom', ['x']) }}", "get_form("),
        ("{{ value(input, ['form', 'x']) }}", "value("),
        ("{{ setting('SYSTEM', 'file_storage_directory') }}", "setting("),
        ("@object:input.form.x", "@object:"),
        ("@dict:preset.vars.x", "@dict:"),
        ("{{ input.form.x }}", "input."),
    ])
    def test_deleted_context_is_error(self, tmp_path, expr, token):
        self._write_mode(
            tmp_path, "del_ctx",
            f"pipeline:\n  - name: generator\n"
            f"    configuration:\n"
            f"      v: \"{expr}\"\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "deleted template context" in i.message and token in i.message
            for i in issues
        )

    def test_deleted_context_in_comment_is_not_flagged(self, tmp_path):
        # Comments are dropped by the YAML parse, so a historical note that
        # mentions get_form()/@object: must not trip the migration guard.
        self._write_mode(
            tmp_path, "del_comment",
            "pipeline:\n  - name: generator\n"
            "    # historical: this used to be @object:input.form.x via get_form(...)\n"
            "    configuration:\n"
            "      v: \"{{ form.x | default('') }}\"\n",
            form_yaml="name: custom\nfields:\n  - {type: string, name: x, default: ''}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("deleted template context" in i.message for i in issues)

    # --- form.<name> reference existence check --------------------------------

    def test_missing_form_field_without_default_is_warning(self, tmp_path):
        self._write_mode(
            tmp_path, "ref_missing",
            "pipeline:\n  - name: generator\n"
            "    configuration:\n"
            "      v: \"{{ form.nonexistent }}\"\n",
            form_yaml="name: custom\nfields:\n  - {type: string, name: real, default: ''}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "warning" and "form.nonexistent" in i.message and "no field named" in i.message
            for i in issues
        )

    def test_missing_form_field_with_default_is_clean(self, tmp_path):
        self._write_mode(
            tmp_path, "ref_defaulted",
            "pipeline:\n  - name: generator\n"
            "    configuration:\n"
            "      v: \"{{ form.nonexistent | default('x') }}\"\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("no field named" in i.message for i in issues)

    def test_existing_form_field_is_clean(self, tmp_path):
        self._write_mode(
            tmp_path, "ref_exists",
            "pipeline:\n  - name: generator\n"
            "    configuration:\n"
            "      v: \"{{ form.steps }}\"\n",
            form_yaml="name: custom\nfields:\n  - {type: number, name: steps, default: 30}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("no field named" in i.message for i in issues)

    def test_injected_key_is_known(self, tmp_path):
        self._write_mode(
            tmp_path, "ref_injected",
            "pipeline:\n  - name: generator\n"
            "    configuration:\n"
            "      v: \"{{ form.video_director.segments }}\"\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("no field named" in i.message for i in issues)

    def test_field_in_external_tab_is_known(self, tmp_path):
        self._write_mode(
            tmp_path, "ref_tab",
            "pipeline:\n  - name: generator\n"
            "    configuration:\n"
            "      v: \"{{ form.tab_field }}\"\n",
            form_yaml=(
                "name: custom\nfields:\n"
                "  - type: tab\n"
                "    children: \"{{ paths.preset }}/modes/txt2img/tabs/gen.yml\"\n"
            ),
            tabs={"gen.yml": "fields:\n  - {type: string, name: tab_field, default: ''}\n"},
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("no field named" in i.message for i in issues)

    def test_loop_generated_field_name_is_known(self, tmp_path):
        # A form `@loop` (count=2) generates controlnet_1_model / controlnet_2_model;
        # a pipeline reference to one of those must be recognised.
        self._write_mode(
            tmp_path, "ref_loop",
            "pipeline:\n  - name: generator\n"
            "    configuration:\n"
            "      v: \"{{ form.controlnet_2_model }}\"\n",
            form_yaml=(
                "name: custom\nfields:\n"
                "  - type: \"@loop\"\n"
                "    configuration:\n"
                "      count: 2\n"
                "      template:\n"
                "        type: accordion\n"
                "        children:\n"
                "          - name: \"controlnet_{{ loop.index }}_model\"\n"
                "            type: model\n"
            ),
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("no field named" in i.message for i in issues)


class TestLintFieldDefaults:
    """External tab/children fragments are schema-validated so a bad field
    `default:` (rework §4: quoted number/bool for a numeric/checkbox field, or
    Jinja in a default) surfaces as a lint error, not only a load failure."""

    def _write_with_tab(self, tmp_path, preset_id, tab_content):
        preset_dir = _write_preset(tmp_path, "p", preset_id, ["txt2img"], with_tests_yml=False)
        (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
        mode_dir = preset_dir / "modes" / "txt2img"
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
        (mode_dir / "form.yml").write_text(
            "name: custom\nfields:\n"
            "  - type: tab\n"
            "    children: \"{{ paths.preset }}/modes/txt2img/tabs/adv.yml\"\n"
        )
        tab = mode_dir / "tabs" / "adv.yml"
        tab.parent.mkdir(parents=True, exist_ok=True)
        tab.write_text(tab_content)
        return preset_dir

    def test_quoted_number_default_in_tab_is_error(self, tmp_path):
        self._write_with_tab(
            tmp_path, "tab_num",
            "fields:\n  - {type: slider, name: steps, default: \"30\"}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "steps" in i.message and "number" in i.message for i in issues)

    def test_quoted_bool_default_in_tab_is_error(self, tmp_path):
        self._write_with_tab(
            tmp_path, "tab_bool",
            "fields:\n  - {type: checkbox, name: flag, default: \"true\"}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "flag" in i.message and "bool" in i.message for i in issues)

    def test_jinja_default_in_tab_is_error(self, tmp_path):
        self._write_with_tab(
            tmp_path, "tab_jinja",
            "fields:\n  - {type: string, name: s, default: \"{{ form.x }}\"}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "Jinja is not rendered" in i.message for i in issues)

    def test_well_typed_defaults_in_tab_are_clean(self, tmp_path):
        self._write_with_tab(
            tmp_path, "tab_ok",
            "fields:\n"
            "  - {type: slider, name: steps, default: 30}\n"
            "  - {type: checkbox, name: flag, default: true}\n"
            "  - {type: string, name: s, default: hello}\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any(i.level == "error" for i in issues)


class TestLintFormVariants:
    """Form "variant" metadata cross-checks (see docs/presets.md "Variants"):
    multiple `default: true` forms in one mode, and `examples:` entries that
    don't exist on disk / aren't shaped like `public/...`."""

    @staticmethod
    def _write_form(preset_dir, mode, variant, form_yaml):
        # Flattened layout: the DEFAULT variant is modes/<mode>/form.yml;
        # additional variants live under modes/<mode>/variants/<name>/. The
        # first form written for a mode takes the default slot, the rest
        # become named variants.
        mode_dir = preset_dir / "modes" / mode
        if not (mode_dir / "form.yml").exists():
            form_dir = mode_dir
        else:
            form_dir = mode_dir / "variants" / variant
        form_dir.mkdir(parents=True, exist_ok=True)
        (form_dir / "form.yml").write_text(form_yaml)
        return form_dir

    def test_single_default_form_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "one_default", ["txt2img"])
        self._write_form(preset_dir, "txt2img", "custom", "name: custom\ndefault: true\nfields: []\n")
        self._write_form(preset_dir, "txt2img", "extra", "name: extra\nfields: []\n")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("multiple forms marked default" in i.message for i in issues)

    def test_multiple_default_forms_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "two_defaults", ["txt2img"])
        self._write_form(preset_dir, "txt2img", "custom", "name: custom\ndefault: true\nfields: []\n")
        self._write_form(preset_dir, "txt2img", "extra", "name: extra\ndefault: true\nfields: []\n")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "multiple forms marked default" in i.message for i in issues
        )

    def test_missing_examples_file_is_error(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "missing_example", ["txt2img"])
        self._write_form(
            preset_dir, "txt2img", "custom",
            "name: custom\nexamples:\n  - public/does-not-exist.png\nfields: []\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(
            i.level == "error" and "examples entry not found" in i.message for i in issues
        )

    def test_existing_examples_file_is_clean(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "p", "has_example", ["txt2img"])
        (preset_dir / "public").mkdir(parents=True, exist_ok=True)
        (preset_dir / "public" / "cover.png").write_bytes(b"\x89PNG\r\n")
        self._write_form(
            preset_dir, "txt2img", "custom",
            "name: custom\nexamples:\n  - public/cover.png\nfields: []\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("examples entry not found" in i.message for i in issues)

    def test_examples_not_under_public_is_error(self, tmp_path):
        # Schema-level rejection (shape: must live under public/) surfaced
        # through the general lint pass, same as a bad media.cover would be.
        preset_dir = _write_preset(tmp_path, "p", "example_outside_public", ["txt2img"])
        self._write_form(
            preset_dir, "txt2img", "custom",
            "name: custom\nexamples:\n  - assets/cover.png\nfields: []\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "must live under 'public/'" in i.message for i in issues)


class TestLintPresetModeContributions:
    """Cross-check for plugin `preset_modes:` contributions - the
    same collision rules `PresetTemplateLoader._apply_preset_mode_contributions`
    enforces at runtime, run here without booting the app. Uses `_write_preset`
    for the target and a local helper for the plugin's `modes_root`."""

    @staticmethod
    def _write_modes_root(plugin_dir, modes: dict):
        """`modes/<name>/pipeline.yml` per entry (name -> pipeline.yml content)."""
        for mode_name, pipeline_yaml in modes.items():
            mode_dir = plugin_dir / "contributed" / "modes" / mode_name
            mode_dir.mkdir(parents=True, exist_ok=True)
            (mode_dir / "pipeline.yml").write_text(pipeline_yaml)
        return plugin_dir / "contributed"

    @staticmethod
    def _manifest(plugin_id, plugin_dir, target):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=plugin_id, plugin_dir=plugin_dir,
            preset_modes=[{"target": target, "modes_root": "contributed"}],
        )

    def test_no_plugin_manifests_is_unaffected(self, tmp_path):
        _write_preset(tmp_path, "p", "target1", ["txt2img"])
        assert PresetLinter([str(tmp_path)]).lint() == []

    def test_valid_contribution_is_clean(self, tmp_path):
        _write_preset(tmp_path, "p", "target2", ["txt2img"])
        plugin_dir = tmp_path / "plugin"
        self._write_modes_root(plugin_dir, {"img2img": "pipeline: []\n"})
        manifest = self._manifest("some-plugin", plugin_dir, "target2")

        issues = PresetLinter([str(tmp_path / "p")], plugin_manifests=[manifest]).lint()
        assert issues == []

    def test_contribution_targeting_absent_preset_is_not_an_error(self, tmp_path):
        _write_preset(tmp_path, "p", "target3", ["txt2img"])
        plugin_dir = tmp_path / "plugin"
        self._write_modes_root(plugin_dir, {"img2img": "pipeline: []\n"})
        manifest = self._manifest("some-plugin", plugin_dir, "no-such-target")

        issues = PresetLinter([str(tmp_path / "p")], plugin_manifests=[manifest]).lint()
        assert issues == []

    def test_contribution_colliding_with_core_mode_is_error(self, tmp_path):
        _write_preset(tmp_path, "p", "target4", ["txt2img"])
        plugin_dir = tmp_path / "plugin"
        self._write_modes_root(plugin_dir, {"txt2img": "pipeline: []\n"})
        manifest = self._manifest("some-plugin", plugin_dir, "target4")

        issues = PresetLinter([str(tmp_path / "p")], plugin_manifests=[manifest]).lint()
        assert any(
            i.level == "error" and "collides with a core mode" in i.message and "some-plugin" in i.preset_path
            for i in issues
        )

    def test_two_plugins_colliding_first_by_plugin_id_wins(self, tmp_path):
        _write_preset(tmp_path, "p", "target5", ["txt2img"])
        plugin_a_dir = tmp_path / "plugin-a"
        self._write_modes_root(plugin_a_dir, {"extra": "pipeline: []\n"})
        plugin_b_dir = tmp_path / "plugin-b"
        self._write_modes_root(plugin_b_dir, {"extra": "pipeline: []\n"})
        manifest_a = self._manifest("plugin-a", plugin_a_dir, "target5")
        manifest_b = self._manifest("plugin-b", plugin_b_dir, "target5")

        # Passed out of alphabetical order - resolution must not depend on it.
        issues = PresetLinter([str(tmp_path / "p")], plugin_manifests=[manifest_b, manifest_a]).lint()
        assert any(
            i.level == "error" and "already contributed by plugin 'plugin-a'" in i.message
            for i in issues
        )
        assert not any("plugin-a" in i.preset_path and "already contributed" in i.message for i in issues)

    def test_missing_modes_directory_is_error(self, tmp_path):
        _write_preset(tmp_path, "p", "target6", ["txt2img"])
        plugin_dir = tmp_path / "plugin"
        (plugin_dir / "contributed").mkdir(parents=True)  # no modes/ subdir
        manifest = self._manifest("some-plugin", plugin_dir, "target6")

        issues = PresetLinter([str(tmp_path / "p")], plugin_manifests=[manifest]).lint()
        assert any(i.level == "error" and "has no modes/ directory" in i.message for i in issues)

    def test_missing_pipeline_in_contributed_mode_is_error(self, tmp_path):
        _write_preset(tmp_path, "p", "target7", ["txt2img"])
        plugin_dir = tmp_path / "plugin"
        (plugin_dir / "contributed" / "modes" / "img2img").mkdir(parents=True)  # empty, no pipeline.yml
        manifest = self._manifest("some-plugin", plugin_dir, "target7")

        issues = PresetLinter([str(tmp_path / "p")], plugin_manifests=[manifest]).lint()
        assert any(i.level == "error" and "pipeline.yml: file not found" in i.message for i in issues)

    def test_no_plugin_manifests_argument_defaults_to_no_cross_check(self, tmp_path):
        # Explicit-paths invocations that don't pass plugin_manifests (e.g. a
        # developer running `preset_lint.py <subtree>`) must not crash.
        _write_preset(tmp_path, "p", "target8", ["txt2img"])
        assert PresetLinter([str(tmp_path / "p")]).lint() == []
