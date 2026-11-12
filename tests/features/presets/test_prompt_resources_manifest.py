from pathlib import Path

import pytest

from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.schema import validate_manifest
from tests.fixtures.prompt_source_plugin import FAKE_SOURCE, fake_prompt_source

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
  - name: "reference_kits"
    type: "lora_picker"
    configuration:
      model_type: "lora"
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
  - name: "restricted"
    type: "media"
    configuration:
      accepted_types: [image, video]
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

    def test_kit_limits_are_accepted_and_must_be_positive(self):
        manifest, errors = _manifest({"refs": [
            {"field": "references", "kind": "image", "token": "<Picture @>", "limit": 9, "limit_total": 12},
        ]})
        assert errors == []
        assert (manifest.prompt_resources["refs"][0].limit, manifest.prompt_resources["refs"][0].limit_total) == (9, 12)
        for key in ("limit", "limit_total"):
            rejected, problems = _manifest({"refs": [{"field": "f", "kind": "image", "token": "<P @>", key: 0}]})
            assert rejected is None and any(key in problem for problem in problems)

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

    def test_same_field_with_distinct_kinds_is_accepted(self):
        manifest, errors = _manifest({"refs": [
            {"field": "references", "kind": "image", "token": "<Picture @>"},
            {"field": "references", "kind": "video", "token": "<Video @>"},
            {"field": "references", "kind": "audio", "token": "<Audio @>"},
        ]})
        assert errors == []
        assert [entry.kind for entry in manifest.prompt_resources["refs"]] == ["image", "video", "audio"]

    def test_a_bundle_source_is_accepted_per_kind(self, fake_prompt_source):
        manifest, errors = _manifest({"refs": [
            {"field": "reference_kits", "source": FAKE_SOURCE, "kind": "image", "token": "<Picture @>"},
            {"field": "reference_kits", "source": FAKE_SOURCE, "kind": "audio", "token": "<Audio @>"},
        ]})
        assert errors == []
        assert {entry.source for entry in manifest.prompt_resources["refs"]} == {FAKE_SOURCE}

    def test_one_field_cannot_mix_media_and_bundle_sources(self, fake_prompt_source):
        manifest, errors = _manifest({"refs": [
            {"field": "references", "kind": "image", "token": "<Picture @>"},
            {"field": "references", "source": FAKE_SOURCE, "kind": "audio", "token": "<Audio @>"},
        ]})
        assert manifest is None
        assert any(f"mixes source 'media' with '{FAKE_SOURCE}'" in error for error in errors)

    def test_an_unknown_source_is_rejected(self):
        manifest, errors = _manifest({"refs": [
            {"field": "references", "source": "lora", "kind": "image", "token": "<Picture @>"},
        ]})
        assert manifest is None
        assert any("names unknown source 'lora'" in error for error in errors)

    def test_a_source_a_plugin_registers_is_accepted_only_while_it_is_registered(self, fake_prompt_source):
        entries = {"refs": [{"field": "bundles", "source": FAKE_SOURCE, "kind": "image", "token": "<Picture @>"}]}
        manifest, errors = _manifest(entries)
        assert errors == []
        assert manifest.prompt_resources["refs"][0].source == FAKE_SOURCE

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

    def test_several_kinds_on_one_media_field_pass(self, tmp_path):
        _write_preset(tmp_path, """  refs:
    - field: "anything"
      kind: "image"
      token: "<Picture @>"
    - field: "anything"
      kind: "video"
      token: "<Video @>"
""")
        assert _resource_errors(tmp_path) == []

    def test_kind_outside_the_field_accepted_types_is_error(self, tmp_path):
        _write_preset(tmp_path, """  refs:
    - field: "restricted"
      kind: "image"
      token: "<Picture @>"
    - field: "restricted"
      kind: "audio"
      token: "<Audio @>"
""")
        errors = _resource_errors(tmp_path)
        assert len(errors) == 1
        assert "accepts ['image', 'video']" in errors[0]
        assert "kind 'audio'" in errors[0]

    def test_an_unknown_source_is_a_lint_error(self, tmp_path):
        _write_preset(tmp_path, """  refs:
    - field: "reference_kits"
      source: "bundle"
      kind: "image"
      token: "<Picture @>"
""")
        assert any("names unknown source 'bundle'" in error for error in _resource_errors(tmp_path))

    def test_a_plugin_source_is_checked_against_its_own_field_type(self, tmp_path, fake_prompt_source):
        _write_preset(tmp_path, f"""  refs:
    - field: "reference_kits"
      source: "{FAKE_SOURCE}"
      kind: "image"
      token: "<Picture @>"
    - field: "references"
      source: "{FAKE_SOURCE}"
      kind: "image"
      token: "<Picture @>"
""")
        errors = _resource_errors(tmp_path)
        assert len(errors) == 1
        assert f"field 'references' has type ['image']; a '{FAKE_SOURCE}' source needs a lora_picker field" in errors[0]

    def test_a_source_a_linted_plugin_declares_is_known_without_enabling_it(self, tmp_path):
        import yaml

        from src.platform.plugins.loader import PluginLoader

        plugin_dir = tmp_path / "plugins" / "declares-source"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "manifest.yml").write_text(yaml.safe_dump({
            "id": "declares-source", "name": "d", "version": "1.0.0", "description": "d", "author": "a",
            "type": "backend-only",
            "prompt_resource_sources": [
                {"id": "declared-stack", "field_type": "media", "label": "Stack", "badge": "Stack", "members": "s.members"},
            ],
        }))
        _write_preset(tmp_path, """  refs:
    - field: "anything"
      source: "declared-stack"
      kind: "image"
      token: "<Picture @>"
    - field: "reference_kits"
      source: "declared-stack"
      kind: "image"
      token: "<Picture @>"
""")
        manifests = PluginLoader(str(tmp_path / "plugins"), str(tmp_path / "none")).discover_plugins()
        errors = [
            issue.message for issue in PresetLinter([str(tmp_path / "presets")], plugin_manifests=manifests).lint()
            if issue.level == "error" and "prompt_resources" in issue.message
        ]
        assert errors == [
            "preset.yml: prompt_resources.refs: field 'reference_kits' has type ['lora_picker']; "
            "a 'declared-stack' source needs a media field"
        ]
        assert any("names unknown source 'declared-stack'" in error for error in _resource_errors(tmp_path))

    def test_a_lora_picker_without_a_source_is_still_error(self, tmp_path):
        _write_preset(tmp_path, """  refs:
    - field: "reference_kits"
      kind: "image"
      token: "<Picture @>"
""")
        assert "only media picker fields" in _resource_errors(tmp_path)[0]

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
            {"field": "references", "kind": "image", "label": "Pictures", "token": "<Picture @>", "limit": 9, "limit_total": 12},
            {"field": "references", "kind": "video", "label": "Videos", "token": "<Video @>", "limit": 3},
            {"field": "references", "kind": "audio", "label": "Audio", "token": "<Audio @>", "limit": 3},
        ]}

    def test_qwen_image21_combine_uses_the_encoder_image_label(self, shipped_presets):
        preset = shipped_presets["Qwen-Image-2.1"]
        assert preset.prompt_resources == {"combine": [
            {"field": "images", "kind": "image", "label": "Images", "token": "<image@>", "limit": 10},
        ]}

    def test_shipped_mappings_lint_clean(self):
        issues = PresetLinter([str(_MARKETPLACE / "MiniMax-H3"), str(_MARKETPLACE / "QwenImage-2.1")]).lint()
        assert [i.message for i in issues if i.level == "error"] == []
