from pathlib import Path

import pytest

from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.schema import validate_manifest

_BASE = {
    "schema": 1,
    "id": "01PRRESOURCES00000000000000",
    "name": "Resources",
    "version": "1.0.0",
    "category": "video",
    "engine": "native",
    "modes": ["refs"],
}

_FORM = """name: "custom"
fields:
  - name: "references"
    type: "image"
    configuration:
      multi: true
  - name: "reference_videos"
    type: "video"
    configuration:
      multi: true
  - name: "anything"
    type: "media"
  - name: "steps"
    type: "slider"
"""


def _manifest(prompt_resources):
    return validate_manifest({**_BASE, "prompt_resources": prompt_resources})


def _write_preset(tmp_path, prompt_resources_yaml):
    preset_dir = tmp_path / "presets" / "Resources" / "std"
    mode_dir = preset_dir / "modes" / "refs"
    mode_dir.mkdir(parents=True)
    (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
    (mode_dir / "form.yml").write_text(_FORM)
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{_BASE['id']}"
name: "Resources"
version: "1.0.0"
category: "video"
engine: "native"
prompt_resources:
{prompt_resources_yaml}
modes:
  - refs
"""
    )
    return preset_dir


def _resource_errors(tmp_path):
    return [
        issue.message for issue in PresetLinter([str(tmp_path)]).lint()
        if issue.level == "error" and "prompt_resources" in issue.message
    ]


class TestPromptResourcesSchema:
    def test_valid_entry_is_accepted(self):
        manifest, errors = _manifest({"refs": [
            {"field": "references", "kind": "image", "label": "Pictures", "token": "<Picture @>"},
        ]})
        assert errors == []
        assert manifest.prompt_resources["refs"][0].token == "<Picture @>"

    def test_token_without_placeholder_is_rejected(self):
        manifest, errors = _manifest({"refs": [
            {"field": "references", "kind": "image", "token": "<Picture 1>"},
        ]})
        assert manifest is None
        assert any("must contain '@'" in error for error in errors)

    @pytest.mark.parametrize("kind", ["picture", "mesh", ""])
    def test_unknown_kind_is_rejected(self, kind):
        manifest, errors = _manifest({"refs": [
            {"field": "references", "kind": kind, "token": "<Picture @>"},
        ]})
        assert manifest is None
        assert any("kind" in error for error in errors)

    @pytest.mark.parametrize("kind", ["image", "video", "audio"])
    def test_every_documented_kind_is_accepted(self, kind):
        _, errors = _manifest({"refs": [{"field": "f", "kind": kind, "token": "<X @>"}]})
        assert errors == []

    def test_same_field_twice_in_one_mode_is_rejected(self):
        manifest, errors = _manifest({"refs": [
            {"field": "references", "kind": "image", "token": "<Picture @>"},
            {"field": "references", "kind": "image", "token": "<Image @>"},
        ]})
        assert manifest is None
        assert any("mapped more than once" in error for error in errors)

    def test_unknown_entry_key_is_rejected(self):
        manifest, _ = _manifest({"refs": [
            {"field": "references", "kind": "image", "token": "<Picture @>", "index": 1},
        ]})
        assert manifest is None


class TestPromptResourcesLint:
    def test_media_fields_of_the_mode_are_clean(self, tmp_path):
        _write_preset(tmp_path, """  refs:
    - field: "references"
      kind: "image"
      token: "<Picture @>"
    - field: "reference_videos"
      kind: "video"
      token: "<Video @>"
    - field: "anything"
      kind: "audio"
      token: "<Audio @>"
""")
        assert _resource_errors(tmp_path) == []

    def test_unknown_field_is_error(self, tmp_path):
        _write_preset(tmp_path, """  refs:
    - field: "missing_field"
      kind: "image"
      token: "<Picture @>"
""")
        errors = _resource_errors(tmp_path)
        assert len(errors) == 1
        assert "'missing_field' is not a field" in errors[0]

    def test_non_media_field_is_error(self, tmp_path):
        _write_preset(tmp_path, """  refs:
    - field: "steps"
      kind: "image"
      token: "<Picture @>"
""")
        errors = _resource_errors(tmp_path)
        assert len(errors) == 1
        assert "only media picker fields" in errors[0]

    def test_kind_contradicting_field_type_is_error(self, tmp_path):
        _write_preset(tmp_path, """  refs:
    - field: "reference_videos"
      kind: "image"
      token: "<Picture @>"
""")
        errors = _resource_errors(tmp_path)
        assert len(errors) == 1
        assert "mapped as kind 'image'" in errors[0]

    def test_undeclared_mode_is_error(self, tmp_path):
        _write_preset(tmp_path, """  edit:
    - field: "references"
      kind: "image"
      token: "<Picture @>"
""")
        errors = _resource_errors(tmp_path)
        assert len(errors) == 1
        assert "'edit' is not a mode" in errors[0]

    def test_token_without_placeholder_fails_lint(self, tmp_path):
        _write_preset(tmp_path, """  refs:
    - field: "references"
      kind: "image"
      token: "<Picture 1>"
""")
        issues = PresetLinter([str(tmp_path)]).lint()
        assert any(i.level == "error" and "must contain '@'" in i.message for i in issues)


_MARKETPLACE = Path(__file__).resolve().parents[3] / "content" / "presets" / "marketplace"


@pytest.fixture(scope="module")
def shipped_presets():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return {preset.name: preset for preset in loader.presets}


class TestShippedPromptResources:
    def test_minimax_h3_refs_maps_pictures_videos_and_audio(self, shipped_presets):
        preset = shipped_presets["MiniMax-H3"]
        assert preset.prompt_resources == {"refs": [
            {"field": "references", "kind": "image", "label": "Pictures", "token": "<Picture @>"},
            {"field": "reference_videos", "kind": "video", "label": "Videos", "token": "<Video @>"},
            {"field": "reference_audios", "kind": "audio", "label": "Audio", "token": "<Audio @>"},
        ]}

    def test_qwen_image21_edit_uses_the_encoder_image_label(self, shipped_presets):
        preset = shipped_presets["Qwen-Image-2.1"]
        assert preset.prompt_resources == {"edit": [
            {"field": "source_image", "kind": "image", "label": "Images", "token": "<image@>"},
        ]}

    def test_shipped_mappings_lint_clean(self):
        issues = PresetLinter([str(_MARKETPLACE / "MiniMax-H3"), str(_MARKETPLACE / "QwenImage-2.1")]).lint()
        assert [i.message for i in issues if i.level == "error"] == []
