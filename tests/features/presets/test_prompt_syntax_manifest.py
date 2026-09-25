import re
from pathlib import Path

import pytest

from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.schema import validate_manifest

_BASE = {
    "schema": 1,
    "id": "01PRSYNTAX0000000000000000",
    "name": "Syntax",
    "version": "1.0.0",
    "category": "video",
    "engine": "native",
    "modes": ["video"],
}


def _manifest(prompt_syntax):
    return validate_manifest({**_BASE, "prompt_syntax": prompt_syntax})


def _write_preset(tmp_path, prompt_syntax_yaml):
    preset_dir = tmp_path / "presets" / "Syntax" / "std"
    mode_dir = preset_dir / "modes" / "video"
    mode_dir.mkdir(parents=True)
    (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
    (mode_dir / "form.yml").write_text('name: "custom"\nfields: []\n')
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{_BASE['id']}"
name: "Syntax"
version: "1.0.0"
category: "video"
engine: "native"
prompt_syntax:
{prompt_syntax_yaml}
modes:
  - video
"""
    )
    return preset_dir


def _syntax_errors(tmp_path):
    return [
        issue.message for issue in PresetLinter([str(tmp_path)]).lint()
        if issue.level == "error" and "prompt_syntax" in issue.message
    ]


class TestPromptSyntaxSchema:
    def test_valid_entry_is_accepted(self):
        manifest, errors = _manifest({"video": [
            {"token": "(S1)", "kind": "marker", "pattern": r"\(S\d+\)", "help": "Speaker reference"},
        ]})
        assert errors == []
        assert manifest.prompt_syntax["video"][0].token == "(S1)"

    def test_pattern_defaults_to_escaped_token(self):
        manifest, errors = _manifest({"video": [
            {"token": "BREAK", "kind": "marker"},
        ]})
        assert errors == []
        assert manifest.prompt_syntax["video"][0].pattern is None

    def test_unbalanced_insert_braces_is_rejected(self):
        manifest, errors = _manifest({"video": [
            {"token": "weight", "kind": "weight", "insert": "{weight=1.2"},
        ]})
        assert manifest is None
        assert any("unbalanced braces" in error for error in errors)

    def test_invalid_regex_pattern_is_rejected(self):
        manifest, errors = _manifest({"video": [
            {"token": "bad", "kind": "marker", "pattern": "(unclosed"},
        ]})
        assert manifest is None
        assert any("does not compile" in error for error in errors)

    def test_lookbehind_pattern_is_rejected(self):
        manifest, errors = _manifest({"video": [
            {"token": "lb", "kind": "marker", "pattern": r"(?<=x)y"},
        ]})
        assert manifest is None
        assert any("lookbehind" in error for error in errors)

    @pytest.mark.parametrize("kind", ["label", "marker", "wrap", "weight"])
    def test_every_documented_kind_is_accepted(self, kind):
        _, errors = _manifest({"video": [{"token": "X", "kind": kind}]})
        assert errors == []

    def test_unknown_kind_is_rejected(self):
        manifest, errors = _manifest({"video": [{"token": "X", "kind": "bogus"}]})
        assert manifest is None
        assert any("kind" in error for error in errors)

    def test_unknown_tone_is_rejected(self):
        manifest, errors = _manifest({"video": [{"token": "X", "kind": "marker", "tone": "bogus"}]})
        assert manifest is None
        assert any("tone" in error for error in errors)

    def test_same_token_twice_in_one_mode_is_rejected(self):
        manifest, errors = _manifest({"video": [
            {"token": "BREAK", "kind": "marker"},
            {"token": "BREAK", "kind": "marker"},
        ]})
        assert manifest is None
        assert any("declared more than once" in error for error in errors)

    def test_unknown_entry_key_is_rejected(self):
        manifest, _ = _manifest({"video": [
            {"token": "X", "kind": "marker", "bogus": 1},
        ]})
        assert manifest is None


class TestPromptSyntaxLint:
    def test_declared_mode_is_clean(self, tmp_path):
        _write_preset(tmp_path, """  video:
    - token: "BREAK"
      kind: "marker"
""")
        assert _syntax_errors(tmp_path) == []

    def test_undeclared_mode_is_error(self, tmp_path):
        _write_preset(tmp_path, """  edit:
    - token: "BREAK"
      kind: "marker"
""")
        errors = _syntax_errors(tmp_path)
        assert len(errors) == 1
        assert "'edit' is not a mode" in errors[0]


_MARKETPLACE = Path(__file__).resolve().parents[3] / "content" / "presets" / "marketplace"


@pytest.fixture(scope="module")
def shipped_presets():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return {preset.name: preset for preset in loader.presets}


class TestShippedPromptSyntax:
    def test_sdxl_txt2img_has_weight_and_break(self, shipped_presets):
        preset = shipped_presets["SDXL"]
        tokens = {entry["token"]: entry for entry in preset.prompt_syntax["txt2img"]}
        assert tokens["(tag:1.2)"]["kind"] == "weight"
        assert tokens["BREAK"]["kind"] == "marker"

    def test_minimax_h3_video_has_speaker_and_dialogue_syntax(self, shipped_presets):
        preset = shipped_presets["MiniMax-H3"]
        tokens = {entry["token"]: entry for entry in preset.prompt_syntax["video"]}
        assert tokens["(S1)"]["kind"] == "marker"
        assert tokens["<d></d>"]["kind"] == "wrap"
        assert tokens["non_diegetic_music:"]["kind"] == "label"

    def test_minimax_h3_dialogue_pattern_matches_multiline_speech_in_python_and_js_alike(self, shipped_presets):
        preset = shipped_presets["MiniMax-H3"]
        for mode in ("video", "refs"):
            tokens = {entry["token"]: entry for entry in preset.prompt_syntax[mode]}
            pattern = tokens["<d></d>"]["pattern"]
            assert "[\\s\\S]" in pattern
            text = "(S1) says: <d>I cry\nI scream\n...\nUnderneath</d>."
            match = re.search(pattern, text)
            assert match is not None
            assert match.group(0) == "<d>I cry\nI scream\n...\nUnderneath</d>"

    def test_minimax_h3_refs_has_entity_reference_markers(self, shipped_presets):
        preset = shipped_presets["MiniMax-H3"]
        tokens = {entry["token"]: entry for entry in preset.prompt_syntax["refs"]}
        for token in ("<Subject 1>", "<Picture 1>", "<Video 1>", "<Audio 1>"):
            assert tokens[token]["kind"] == "marker"
            assert tokens[token]["insert"].count("{") == tokens[token]["insert"].count("}")

    def test_minimax_h3_fast_video_mirrors_the_same_notation(self, shipped_presets):
        preset = shipped_presets["MiniMax-H3-Fast"]
        tokens = {entry["token"] for entry in preset.prompt_syntax["video"]}
        assert {"(S1)", "<d></d>", "integrated_multimodal_description:"} <= tokens

    def test_shipped_syntax_lints_clean(self):
        issues = PresetLinter([
            str(_MARKETPLACE / "MiniMax-H3"),
            str(_MARKETPLACE / "MiniMax-H3-Fast"),
            str(_MARKETPLACE / "SDXL"),
        ]).lint()
        assert [i.message for i in issues if i.level == "error"] == []
