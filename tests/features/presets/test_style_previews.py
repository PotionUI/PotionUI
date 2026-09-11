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


class TestBuildStylePrompt:
    def test_wraps_example_prompt(self):
        prompt, negative = build_style_prompt(_style())
        assert prompt == "old, a cat, retro."
        assert negative == "3d"

    def test_missing_prepend_append_negative_default_empty(self):
        style = {"id": "x", "example_prompt": "a cat"}
        prompt, negative = build_style_prompt(style)
        assert prompt == "a cat"
        assert negative == ""


class TestPreviewRelPath:
    def test_builds_canonical_path(self):
        assert preview_rel_path("retro-90s-cel") == "public/styles/retro-90s-cel.webp"


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
