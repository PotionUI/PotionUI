from unittest.mock import MagicMock

import pytest

from src.features.presets import operations
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader

_ENTRY = '    - token: "{token}"\n      kind: "marker"\n'


def _write_preset(root, syntax_yaml, modes, promptless=()):
    preset_dir = root / "presets" / "Syntax" / "std"
    for mode in modes:
        mode_dir = preset_dir / "modes" / mode
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
        prompt_line = 'prompt: "none"\n' if mode in promptless else ""
        (mode_dir / "form.yml").write_text(f'{prompt_line}name: "custom"\nfields: []\n')
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    modes_yaml = "\n".join(f"  - {mode}" for mode in modes)
    (preset_dir / "preset.yml").write_text(
        f'schema: 1\nid: "01PRSYNTAXDEFAULT00000000"\nname: "Syntax"\nversion: "1.0.0"\n'
        f'category: "video"\nengine: "native"\nprompt_syntax:\n{syntax_yaml}\nmodes:\n{modes_yaml}\n'
    )
    return preset_dir


def _load(root):
    loader = PresetTemplateLoader([str(root / "presets")])
    loader.load_presets()
    return next(p for p in loader.presets if p.name == "Syntax")


def _tokens(preset, mode):
    return [entry["token"] for entry in preset.prompt_syntax.get(mode, [])]


def _warnings(root):
    return [
        issue.message for issue in PresetLinter([str(root / "presets")]).lint()
        if issue.level == "warning" and "prompt_syntax" in issue.message
    ]


class TestDefaultResolution:
    def test_default_override_and_opt_out(self, tmp_path):
        _write_preset(
            tmp_path,
            '  "*":\n' + _ENTRY.format(token="DEFAULT")
            + "  loop:\n" + _ENTRY.format(token="LOOP")
            + "  talk: []",
            ["txt2video", "loop", "talk"],
        )
        preset = _load(tmp_path)
        assert _tokens(preset, "txt2video") == ["DEFAULT"]
        assert _tokens(preset, "loop") == ["LOOP"]
        assert _tokens(preset, "talk") == []

    def test_default_key_is_not_served_as_a_mode(self, tmp_path):
        _write_preset(tmp_path, '  "*":\n' + _ENTRY.format(token="DEFAULT"), ["txt2video"])
        assert "*" not in _load(tmp_path).prompt_syntax

    def test_promptless_mode_does_not_inherit_the_default(self, tmp_path):
        _write_preset(
            tmp_path, '  "*":\n' + _ENTRY.format(token="DEFAULT"), ["txt2video", "upscale"], promptless=["upscale"]
        )
        preset = _load(tmp_path)
        assert _tokens(preset, "txt2video") == ["DEFAULT"]
        assert _tokens(preset, "upscale") == []

    def test_without_a_default_unlisted_modes_get_nothing(self, tmp_path):
        _write_preset(tmp_path, "  txt2video:\n" + _ENTRY.format(token="ONLY"), ["txt2video", "loop"])
        preset = _load(tmp_path)
        assert _tokens(preset, "txt2video") == ["ONLY"]
        assert _tokens(preset, "loop") == []


class TestLintWarning:
    def test_mode_without_entry_or_default_warns(self, tmp_path):
        _write_preset(tmp_path, "  txt2video:\n" + _ENTRY.format(token="ONLY"), ["txt2video", "loop"])
        warnings = _warnings(tmp_path)
        assert len(warnings) == 1
        assert "mode 'loop'" in warnings[0]

    def test_default_silences_the_warning(self, tmp_path):
        _write_preset(tmp_path, '  "*":\n' + _ENTRY.format(token="D"), ["txt2video", "loop"])
        assert _warnings(tmp_path) == []

    def test_opt_out_and_promptless_modes_do_not_warn(self, tmp_path):
        _write_preset(
            tmp_path, "  txt2video:\n" + _ENTRY.format(token="ONLY") + "  loop: []",
            ["txt2video", "loop", "upscale"], promptless=["upscale"],
        )
        assert _warnings(tmp_path) == []

    def test_preset_without_prompt_syntax_is_not_warned(self, tmp_path):
        preset_dir = _write_preset(tmp_path, "  txt2video: []", ["txt2video", "loop"])
        text = (preset_dir / "preset.yml").read_text().replace("prompt_syntax:\n  txt2video: []\n", "")
        (preset_dir / "preset.yml").write_text(text)
        assert _warnings(tmp_path) == []


OPT_OUT_REASONS = {
    "remix": "the preset writes the structured request itself; the prompt is one plain sentence",
    "talk": "the preset writes the structured request itself; the spoken words come from the Words field",
    "motion_transfer": "the preset writes the structured request itself; the prompt is plain look-and-action text",
}


@pytest.fixture(scope="module")
def shipped():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return {preset.name: preset for preset in loader.presets}


class TestShippedMiniMaxModes:
    @pytest.mark.parametrize("name", ["MiniMax-H3", "MiniMax-H3-Fast"])
    def test_every_mode_with_a_prompt_field_has_syntax_or_a_listed_opt_out(self, shipped, name):
        preset = shipped[name]
        for mode_name, mode in preset.modes.items():
            if mode.prompt == "none":
                continue
            tokens = _tokens(preset, mode_name)
            if mode_name in OPT_OUT_REASONS and name == "MiniMax-H3":
                assert tokens == [], mode_name
            else:
                assert tokens, f"{name}.{mode_name} has a prompt field and no prompt syntax"

    def test_loop_offers_the_document_labels_and_only_shot_one(self, shipped):
        preset = shipped["MiniMax-H3"]
        assert _tokens(preset, "loop") == [
            "integrated_multimodal_description:", "overall_soundscape:", "non_diegetic_music:", "[Shot 1]",
        ]
        shot = next(e for e in preset.prompt_syntax["loop"] if e["token"] == "[Shot 1]")
        assert shot["pattern"] == r"\[Shot 1\]"

    def test_refs_keeps_its_own_list(self, shipped):
        assert "<Subject 1>" in _tokens(shipped["MiniMax-H3"], "refs")

    def test_fast_extend_and_refine_faces_inherit_the_document_syntax(self, shipped):
        for mode in ("extend", "refine_faces", "video"):
            assert "(S1)" in _tokens(shipped["MiniMax-H3-Fast"], mode)

    def test_loop_form_payload_carries_the_syntax(self, shipped):
        preset = shipped["MiniMax-H3"]
        collaborators = MagicMock()
        collaborators.file_repo.find_preset_by_id.return_value = preset
        collaborators.db_repo.get_preset_form_overrides.return_value = {}
        collaborators.form_serializer.process_form_fields.return_value = {"fields": []}
        payload = operations.get_form_schema(collaborators, preset.id, "loop")
        assert [e["token"] for e in payload["prompt_syntax"]][0] == "integrated_multimodal_description:"
        assert "[Shot 1]" in [e["token"] for e in payload["prompt_syntax"]]


class TestShippedLintIsQuiet:
    def test_no_prompt_syntax_warnings_for_shipped_presets(self):
        issues = PresetLinter(["content/presets"]).lint()
        assert [i.message for i in issues if i.level == "warning" and "prompt_syntax" in i.message] == []
