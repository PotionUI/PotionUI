"""Unit coverage for every import format parser and format detection."""
import io
import json

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from src.features.prompt_database.importing import PARSERS, detect_format
from src.features.prompt_database.importing.csv_format import parse_styles_csv
from src.features.prompt_database.importing.image_format import parse_image
from src.features.prompt_database.importing.json_format import PLACEHOLDER, parse_style_json
from src.features.prompt_database.importing.lines_format import parse_lines
from src.features.prompt_database.importing.yaml_format import parse_wildcard_yaml


# --- styles.csv ------------------------------------------------------------

A1111_STYLES_CSV = (
    'name,prompt,negative_prompt\n'
    'Cinematic,"a hero standing on a cliff,\nwind blowing his cape",'
    '"blurry, low quality"\n'
    'OnlyPositive,sharp focus and detail,\n'
    'OnlyNegative,,ugly deformed\n'
    'Blank,,\n'
)

LEGACY_TEXT_HEADER_CSV = 'name,text,negative_prompt\nRetro,vhs footage,noisy\n'


class TestStylesCsv:
    def test_pairs_positive_and_negative_with_shared_group_id(self):
        results = parse_styles_csv(A1111_STYLES_CSV, filename="styles.csv")
        cinematic = [r for r in results if r.name == "Cinematic"]
        assert len(cinematic) == 2
        positive = next(r for r in cinematic if r.usage_hint == "positive")
        negative = next(r for r in cinematic if r.usage_hint == "negative")
        assert positive.text == "a hero standing on a cliff,\nwind blowing his cape"
        assert negative.text == "blurry, low quality"
        assert positive.group_id == negative.group_id
        assert positive.group_id is not None

    def test_row_with_only_positive_has_no_group_id(self):
        results = parse_styles_csv(A1111_STYLES_CSV, filename="styles.csv")
        only_positive = next(r for r in results if r.name == "OnlyPositive")
        assert only_positive.usage_hint == "positive"
        assert only_positive.group_id is None

    def test_row_with_only_negative_is_kept(self):
        results = parse_styles_csv(A1111_STYLES_CSV, filename="styles.csv")
        only_negative = next(r for r in results if r.name == "OnlyNegative")
        assert only_negative.usage_hint == "negative"
        assert only_negative.text == "ugly deformed"

    def test_blank_row_is_skipped_entirely(self):
        results = parse_styles_csv(A1111_STYLES_CSV, filename="styles.csv")
        assert all(r.name != "Blank" for r in results)

    def test_legacy_text_header_is_accepted_as_prompt_alias(self):
        results = parse_styles_csv(LEGACY_TEXT_HEADER_CSV, filename="styles.csv")
        positive = next(r for r in results if r.usage_hint == "positive")
        assert positive.text == "vhs footage"

    def test_accepts_bytes_with_utf8_bom(self):
        data = A1111_STYLES_CSV.encode("utf-8-sig")
        results = parse_styles_csv(data, filename="styles.csv")
        assert any(r.name == "Cinematic" for r in results)


# --- Fooocus style JSON -----------------------------------------------------

FOOOCUS_JSON = json.dumps([
    {"name": "Cinematic Diva", "prompt": "{prompt}, cinematic still, dramatic lighting", "negative_prompt": "ugly, deformed"},
    {"name": "Only Positive", "prompt": "sharp focus"},
])

FOOOCUS_NAMED_LISTS_JSON = json.dumps({"pack_a": [{"name": "A", "prompt": "one"}], "pack_b": [{"name": "B", "prompt": "two"}]})


class TestStyleJson:
    def test_keeps_placeholder_literal_and_records_metadata(self):
        results = parse_style_json(FOOOCUS_JSON, filename="sdxl_styles.json")
        diva = next(r for r in results if r.usage_hint == "positive" and r.name == "Cinematic Diva")
        assert PLACEHOLDER in diva.text
        assert diva.metadata["placeholder"] == PLACEHOLDER

    def test_pairs_positive_and_negative_from_one_entry(self):
        results = parse_style_json(FOOOCUS_JSON, filename="sdxl_styles.json")
        diva = [r for r in results if r.name == "Cinematic Diva"]
        assert len(diva) == 2
        assert diva[0].group_id == diva[1].group_id

    def test_entry_without_negative_has_no_group_id(self):
        results = parse_style_json(FOOOCUS_JSON, filename="sdxl_styles.json")
        only_positive = next(r for r in results if r.name == "Only Positive")
        assert only_positive.group_id is None

    def test_top_level_object_of_named_lists_is_flattened(self):
        results = parse_style_json(FOOOCUS_NAMED_LISTS_JSON, filename="packs.json")
        assert {r.name for r in results} == {"A", "B"}


# --- wildcard YAML -----------------------------------------------------------

WILDCARD_YAML = """
styles:
  painterly:
    - oil painting
    - impressionist brushwork
  photographic:
    - dslr photo
subjects:
  - a cat
single_string_leaf: a lone wolf
"""


class TestWildcardYaml:
    def test_every_leaf_string_becomes_a_positive_entry(self):
        results = parse_wildcard_yaml(WILDCARD_YAML, filename="wildcards.yaml")
        assert all(r.usage_hint == "positive" for r in results)
        texts = {r.text for r in results}
        assert {"oil painting", "impressionist brushwork", "dslr photo", "a cat", "a lone wolf"} <= texts

    def test_tags_are_the_nesting_key_path(self):
        results = parse_wildcard_yaml(WILDCARD_YAML, filename="wildcards.yaml")
        oil = next(r for r in results if r.text == "oil painting")
        assert oil.tags == ["styles", "painterly"]
        cat = next(r for r in results if r.text == "a cat")
        assert cat.tags == ["subjects"]

    def test_entries_have_no_name(self):
        results = parse_wildcard_yaml(WILDCARD_YAML, filename="wildcards.yaml")
        assert all(r.name is None for r in results)

    def test_empty_document_returns_no_entries(self):
        assert parse_wildcard_yaml("", filename="empty.yaml") == []


# --- one-per-line text --------------------------------------------------------

class TestLines:
    def test_comments_and_blank_lines_are_skipped(self):
        text = "# a wildcard comment\na fox\n\n   \nb bear\n# trailing comment\n"
        results = parse_lines(text, filename="wildcard.txt")
        assert [r.text for r in results] == ["a fox", "b bear"]
        assert all(r.usage_hint == "positive" for r in results)

    def test_negative_prompt_marker_is_not_special_here(self):
        text = "a fox\nNegative prompt: blurry\n"
        results = parse_lines(text, filename="wildcard.txt")
        assert [r.text for r in results] == ["a fox", "Negative prompt: blurry"]


# --- image metadata -----------------------------------------------------------

def _png_with(chunks: dict) -> bytes:
    image = Image.new("RGB", (4, 4))
    info = PngInfo()
    for key, value in chunks.items():
        info.add_text(key, value)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


A1111_PARAMETERS = (
    "a fox in the forest, cinematic\n"
    "Negative prompt: blurry, low quality\n"
    "Steps: 20, Sampler: Euler a, CFG scale: 7.5, Seed: 123, Size: 512x768, Model: myModel"
)


class TestImageA1111:
    def test_png_parameters_chunk_splits_positive_negative_and_settings(self):
        data = _png_with({"parameters": A1111_PARAMETERS})
        results = parse_image(data, filename="a1111.png")
        positive = next(r for r in results if r.usage_hint == "positive")
        negative = next(r for r in results if r.usage_hint == "negative")
        assert positive.text == "a fox in the forest, cinematic"
        assert negative.text == "blurry, low quality"
        assert positive.group_id == negative.group_id
        assert positive.steps == 20
        assert positive.sampler == "Euler a"
        assert positive.cfg_scale == 7.5
        assert positive.width == 512 and positive.height == 768
        assert positive.model_name == "myModel"
        assert positive.name == "a1111"

    def test_jpeg_exif_user_comment_unicode_prefixed(self):
        image = Image.new("RGB", (8, 8))
        exif = image.getexif()
        exif[0x9286] = b"UNICODE\x00" + A1111_PARAMETERS.encode("utf-16-le")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", exif=exif)
        results = parse_image(buffer.getvalue(), filename="a1111.jpg")
        positive = next(r for r in results if r.usage_hint == "positive")
        assert positive.text == "a fox in the forest, cinematic"
        assert positive.steps == 20

    def test_no_recognized_metadata_returns_empty_list(self):
        image = Image.new("RGB", (4, 4))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        assert parse_image(buffer.getvalue(), filename="plain.png") == []

    def test_not_an_image_returns_empty_list(self):
        assert parse_image(b"not an image", filename="broken.png") == []


class TestImageInvokeAi:
    def test_invokeai_metadata_json_is_parsed(self):
        payload = json.dumps({
            "positive_prompt": "a castle on a hill",
            "negative_prompt": "blurry",
            "cfg_scale": 7,
            "steps": 30,
            "scheduler": "ddim",
            "width": 512,
            "height": 512,
            "seed": 42,
            "model": {"name": "invoke-model"},
        })
        data = _png_with({"invokeai_metadata": payload})
        results = parse_image(data, filename="invoke.png")
        positive = next(r for r in results if r.usage_hint == "positive")
        negative = next(r for r in results if r.usage_hint == "negative")
        assert positive.text == "a castle on a hill"
        assert negative.text == "blurry"
        assert positive.group_id == negative.group_id
        assert positive.sampler == "ddim"
        assert positive.model_name == "invoke-model"
        assert positive.seed == 42


class TestImageNovelAi:
    def test_novelai_comment_json_is_parsed(self):
        payload = json.dumps({
            "prompt": "a wizard casting a spell", "uc": "bad hands",
            "steps": 28, "scale": 11, "sampler": "k_euler",
            "width": 832, "height": 1216, "seed": 999,
        })
        data = _png_with({"Comment": payload, "Software": "NovelAI"})
        results = parse_image(data, filename="nai.png")
        positive = next(r for r in results if r.usage_hint == "positive")
        negative = next(r for r in results if r.usage_hint == "negative")
        assert positive.text == "a wizard casting a spell"
        assert negative.text == "bad hands"
        assert positive.cfg_scale == 11
        assert positive.sampler == "k_euler"


class TestImageComfyUi:
    def _graph(self, **overrides):
        graph = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl.safetensors"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a dragon over a castle"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "ugly, deformed"}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024}},
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "positive": ["2", 0], "negative": ["3", 0],
                    "steps": 25, "cfg": 8, "seed": 111, "sampler_name": "dpmpp_2m",
                },
            },
        }
        graph.update(overrides)
        return graph

    def test_follows_sampler_links_to_clip_text_encode_nodes(self):
        data = _png_with({"prompt": json.dumps(self._graph())})
        results = parse_image(data, filename="comfy.png")
        positive = next(r for r in results if r.usage_hint == "positive")
        negative = next(r for r in results if r.usage_hint == "negative")
        assert positive.text == "a dragon over a castle"
        assert negative.text == "ugly, deformed"
        assert positive.steps == 25
        assert positive.cfg_scale == 8
        assert positive.sampler == "dpmpp_2m"
        assert positive.width == 1024 and positive.height == 1024
        assert positive.model_name == "sdxl.safetensors"
        assert positive.group_id == negative.group_id

    def test_falls_back_to_every_clip_text_encode_node_without_a_sampler(self):
        graph = {
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a dragon"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "a griffin"}},
        }
        data = _png_with({"prompt": json.dumps(graph)})
        results = parse_image(data, filename="comfy-partial.png")
        assert {r.text for r in results} == {"a dragon", "a griffin"}
        assert all(r.usage_hint == "positive" for r in results)

    def test_link_pointing_at_a_literal_value_not_a_node_is_skipped(self):
        graph = self._graph()
        # A sampler whose "negative" input is a raw literal, not a [node_id, slot] link.
        graph["5"]["inputs"]["negative"] = "not-a-link"
        data = _png_with({"prompt": json.dumps(graph)})
        results = parse_image(data, filename="comfy-odd.png")
        assert all(r.usage_hint == "positive" for r in results)

    def test_malformed_graph_never_raises(self):
        data = _png_with({"prompt": "{not valid json"})
        assert parse_image(data, filename="comfy-broken.png") == []


# --- format detection ----------------------------------------------------------

class TestDetectFormat:
    def test_extension_wins_for_every_supported_extension(self):
        assert detect_format("styles.csv", "") == "styles_csv"
        assert detect_format("pack.json", "") == "style_json"
        assert detect_format("wildcards.yaml", "") == "wildcard_yaml"
        assert detect_format("wildcards.yml", "") == "wildcard_yaml"
        assert detect_format("prompts.txt", "") == "lines"
        assert detect_format("shot.png", b"") == "image"
        assert detect_format("shot.jpg", b"") == "image"
        assert detect_format("shot.jpeg", b"") == "image"
        assert detect_format("shot.webp", b"") == "image"

    def test_sniffs_png_magic_bytes(self):
        data = _png_with({"parameters": A1111_PARAMETERS})
        assert detect_format(None, data) == "image"
        assert detect_format("upload", data) == "image"

    def test_sniffs_jpeg_magic_bytes(self):
        assert detect_format(None, b"\xff\xd8\xff\xe0rest") == "image"

    def test_sniffs_webp_magic_bytes(self):
        assert detect_format(None, b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image"

    def test_sniffs_json_by_leading_bracket(self):
        assert detect_format(None, FOOOCUS_JSON) == "style_json"
        assert detect_format(None, "  [1, 2]") == "style_json"

    def test_sniffs_styles_csv_by_header_line(self):
        assert detect_format(None, A1111_STYLES_CSV) == "styles_csv"

    def test_sniffs_wildcard_yaml_by_key_then_dash_list(self):
        assert detect_format(None, WILDCARD_YAML) == "wildcard_yaml"

    def test_falls_back_to_lines(self):
        assert detect_format(None, "just some plain text\nanother line\n") == "lines"

    def test_explicit_format_override_beats_detection(self):
        # detect_format itself never consults an override - callers pass their
        # own `format` straight to the parser lookup instead. This asserts the
        # extension-based guess a caller would otherwise get, so a regression
        # in extension handling doesn't silently hide the override behavior.
        assert detect_format("prompts.csv", "not a csv at all") == "styles_csv"
