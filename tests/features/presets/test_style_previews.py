"""Tests for the pure style-preview helpers `scripts/preset_styles_render.py`
builds on: prompt assembly, the downscale + WebP encode, `styles.yml`'s
`preview:` field fill, and style selection."""
from pathlib import Path

import pytest
from PIL import Image

from src.features.presets.style_previews import (
    build_style_prompt,
    downscale_and_save_webp,
    form_has_field,
    preview_rel_path,
    select_styles,
    set_style_preview,
    write_webp,
)
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _preset(with_quantity_field=True) -> PresetTemplate:
    fields = [FieldTemplate(type="seed", name="seed")]
    if with_quantity_field:
        fields.append(FieldTemplate(type="slider", name="quantity"))
    form = FormTemplate(name="custom", fields=fields, default=True)
    mode = ModeTemplate(forms=[form], pipes=[])
    return PresetTemplate(id="anima", name="Anima", version="1.0.0", path="/tmp/anima", modes={"txt2img": mode})


def _style(**overrides):
    style = {
        "id": "retro-90s-cel",
        "name": "Retro 90s Anime Cel",
        "category": "Anime",
        "prepend": "old, ",
        "append": ", retro.",
        "negative": "3d",
        "example_prompt": "a cat",
    }
    style.update(overrides)
    return style


_NO_DEFAULTS = {"prompt_prefix": "", "negative": "", "example_prompt": ""}


class TestBuildStylePrompt:
    def test_wraps_example_prompt(self):
        prompt, negative = build_style_prompt(_style(), _NO_DEFAULTS)
        assert prompt == "old, a cat, retro."
        assert negative == "3d"

    def test_missing_prepend_append_negative_default_empty(self):
        style = {"id": "x", "example_prompt": "a cat"}
        prompt, negative = build_style_prompt(style, _NO_DEFAULTS)
        assert prompt == "a cat"
        assert negative == ""

    def test_prompt_prefix_goes_before_prepend(self):
        defaults = {"prompt_prefix": "masterpiece, best quality, ", "negative": ""}
        prompt, _ = build_style_prompt(_style(), defaults)
        assert prompt == "masterpiece, best quality, old, a cat, retro."

    def test_negatives_join_defaults_first_then_style(self):
        defaults = {"prompt_prefix": "", "negative": "worst quality, low quality"}
        _, negative = build_style_prompt(_style(), defaults)
        assert negative == "worst quality, low quality, 3d"

    def test_empty_default_negative_omits_join(self):
        defaults = {"prompt_prefix": "", "negative": ""}
        _, negative = build_style_prompt(_style(), defaults)
        assert negative == "3d"

    def test_empty_style_negative_omits_join(self):
        style = _style(negative=None)
        defaults = {"prompt_prefix": "", "negative": "worst quality"}
        _, negative = build_style_prompt(style, defaults)
        assert negative == "worst quality"

    def test_both_negatives_empty_yields_empty_string(self):
        style = _style(negative=None)
        _, negative = build_style_prompt(style, _NO_DEFAULTS)
        assert negative == ""

    def test_own_example_prompt_wins_over_preview_default(self):
        defaults = {"prompt_prefix": "", "negative": "", "example_prompt": "a glowing potion in tall grass"}
        prompt, _ = build_style_prompt(_style(example_prompt="a cat"), defaults)
        assert prompt == "old, a cat, retro."

    def test_falls_back_to_preview_default_when_style_has_none(self):
        style = _style(example_prompt=None)
        defaults = {"prompt_prefix": "", "negative": "", "example_prompt": "a glowing potion in tall grass"}
        prompt, _ = build_style_prompt(style, defaults)
        assert prompt == "old, a glowing potion in tall grass, retro."

    def test_falls_back_to_preview_default_when_style_example_prompt_absent(self):
        style = {k: v for k, v in _style().items() if k != "example_prompt"}
        defaults = {"prompt_prefix": "", "negative": "", "example_prompt": "a glowing potion in tall grass"}
        prompt, _ = build_style_prompt(style, defaults)
        assert prompt == "old, a glowing potion in tall grass, retro."

    def test_both_example_prompts_empty_yields_no_scene_text(self):
        style = _style(example_prompt=None)
        prompt, _ = build_style_prompt(style, _NO_DEFAULTS)
        assert prompt == "old, , retro."


class TestPreviewRelPath:
    def test_builds_canonical_path(self):
        assert preview_rel_path("retro-90s-cel") == "public/styles/retro-90s-cel.webp"


def _preset_with_nested_tab_field(field_name: str) -> PresetTemplate:
    """A field buried the way Anima's real form nests it: tabs -> tab ->
    section -> row -> field (see modes/txt2img/tabs/generation.yml, loaded
    onto the form's top-level `fields:` as external `children:` fragments -
    `PresetTemplateLoader._load_external_children_file`)."""
    target = FieldTemplate(type="slider", name=field_name)
    row = FieldTemplate(type="row", children=[FieldTemplate(type="seed", name="seed"), target])
    section = FieldTemplate(type="section", children=[row])
    tab = FieldTemplate(type="tab", children=[section])
    tabs = FieldTemplate(type="tabs", children=[tab])
    form = FormTemplate(name="custom", fields=[tabs], default=True)
    mode = ModeTemplate(forms=[form], pipes=[])
    return PresetTemplate(id="anima", name="Anima", version="1.0.0", path="/tmp/anima", modes={"txt2img": mode})


class TestFormHasField:
    def test_true_when_field_present(self):
        preset = _preset(with_quantity_field=True)
        assert form_has_field(preset, "txt2img", "quantity") is True

    def test_false_when_field_absent(self):
        preset = _preset(with_quantity_field=False)
        assert form_has_field(preset, "txt2img", "quantity") is False

    def test_false_for_unknown_mode(self):
        preset = _preset()
        assert form_has_field(preset, "img2img", "quantity") is False

    def test_finds_field_nested_under_a_tab(self):
        """Regression: `quantity`/`steps`/`resolution` on a real preset live
        several levels under a tab (tabs -> tab -> section -> row), not as a
        direct child of the form - the lookup must recurse that deep."""
        preset = _preset_with_nested_tab_field("steps")
        assert form_has_field(preset, "txt2img", "steps") is True

    def test_false_for_field_not_present_even_nested(self):
        preset = _preset_with_nested_tab_field("steps")
        assert form_has_field(preset, "txt2img", "resolution") is False


class TestSelectStyles:
    def test_all_styles_when_none_requested(self):
        styles = [_style(id="a", example_prompt="x"), _style(id="b", example_prompt="y")]
        assert [s["id"] for s in select_styles(styles, None)] == ["a", "b"]

    def test_filters_to_requested_ids(self):
        styles = [_style(id="a", example_prompt="x"), _style(id="b", example_prompt="y")]
        assert [s["id"] for s in select_styles(styles, ["b"])] == ["b"]

    def test_unknown_id_raises(self):
        styles = [_style(id="a", example_prompt="x")]
        with pytest.raises(ValueError, match="Unknown style id"):
            select_styles(styles, ["bogus"])

    def test_empty_styles_raises(self):
        with pytest.raises(ValueError, match="no styles.yml"):
            select_styles([], None)


class TestDownscaleAndSaveWebp:
    def test_downscales_preserving_aspect_ratio(self, tmp_path):
        img = Image.new("RGB", (1000, 500), "blue")
        dest = tmp_path / "out.webp"

        downscale_and_save_webp(img, dest, 200)

        assert dest.exists()
        with Image.open(dest) as saved:
            assert saved.format == "WEBP"
            assert saved.size == (200, 100)

    def test_never_upscales(self, tmp_path):
        img = Image.new("RGB", (100, 50), "blue")
        dest = tmp_path / "out.webp"

        downscale_and_save_webp(img, dest, 200)

        with Image.open(dest) as saved:
            assert saved.size == (100, 50)

    def test_saved_file_carries_no_metadata(self, tmp_path):
        """The source may carry EXIF/ICC/XMP in `.info` (e.g. round-tripped
        through `.convert()`/`.resize()`, which copy it) - `.save()` is never
        called with `exif=`/`icc_profile=`/`xmp=` kwargs, so none of it
        should reach the written file."""
        img = Image.new("RGB", (100, 50), "blue")
        img.info["exif"] = b"Exif\x00\x00fake-exif-payload"
        img.info["icc_profile"] = b"fake-icc-profile"
        img.info["xmp"] = b"<x:xmpmeta>fake</x:xmpmeta>"
        dest = tmp_path / "out.webp"

        downscale_and_save_webp(img, dest, 200)

        with Image.open(dest) as saved:
            assert "exif" not in saved.info
            assert "icc_profile" not in saved.info
            assert "xmp" not in saved.info


class TestWriteWebp:
    def test_reads_source_file_and_downscales(self, tmp_path):
        source = tmp_path / "source.png"
        Image.new("RGB", (1000, 500), "red").save(source)
        dest = tmp_path / "nested" / "out.webp"

        write_webp(source, dest, 200)

        with Image.open(dest) as saved:
            assert saved.format == "WEBP"
            assert saved.size == (200, 100)


class TestSetStylePreview:
    def test_inserts_preview_after_the_matching_entry(self, tmp_path):
        styles_path = tmp_path / "styles.yml"
        styles_path.write_text(
            """styles:
  - id: "a"
    name: "A"
    example_prompt: "x"
  - id: "b"
    name: "B"
    example_prompt: "y"
"""
        )

        set_style_preview(styles_path, "a", "public/styles/a.webp")

        text = styles_path.read_text()
        assert 'preview: "public/styles/a.webp"' in text
        assert text.count('id: "b"') == 1
        assert "b.webp" not in text

    def test_replaces_existing_preview_value(self, tmp_path):
        styles_path = tmp_path / "styles.yml"
        styles_path.write_text(
            """styles:
  - id: "a"
    name: "A"
    preview: "public/styles/old.webp"
    example_prompt: "x"
"""
        )

        set_style_preview(styles_path, "a", "public/styles/new.webp")

        text = styles_path.read_text()
        assert 'preview: "public/styles/new.webp"' in text
        assert "old.webp" not in text

    def test_unknown_id_raises(self, tmp_path):
        styles_path = tmp_path / "styles.yml"
        styles_path.write_text('styles:\n  - id: "a"\n    example_prompt: "x"\n')

        with pytest.raises(ValueError, match="not found"):
            set_style_preview(styles_path, "missing", "public/styles/missing.webp")
