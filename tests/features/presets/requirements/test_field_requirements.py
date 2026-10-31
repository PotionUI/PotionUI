import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.mode_fields import install_recipe_id, resolve_requirement_fields
from src.features.presets.requirements.builtin import ModelRequirementChecker, ModelRequirementSchema
from src.features.presets.requirements.contracts import RequirementContext
from tests.features.recipes.mode_fixtures import mode, model_field, rec


def _ctx(models) -> RequirementContext:
    return RequirementContext(models=models, gpu_available=False, gpu_total_vram_gb=None, backend=None, platform="linux")


class _Repo:
    def __init__(self, present=()):
        self.present = {pair: SimpleNamespace(model_type=pair[0], filename=pair[1], is_available=True) for pair in present}

    def get_by_identity(self, model_type, filename):
        return self.present.get((model_type, filename))

    def get_by_sha256(self, sha256):
        return None


CONTROL = model_field("control_model", "model_patch", [rec("ctrl-int8.safetensors", 1, "int8"), rec("ctrl-bf16.safetensors", 2, "bf16")], required=False)
RESTORE = model_field("restore_lora", "lora", [rec("restore.safetensors", 1)], required=False)
DEBLUR = model_field("deblur_lora", "lora", [rec("deblur.safetensors", 1)], required=False)


def _check(spec, present):
    resolved = resolve_requirement_fields(spec, mode([CONTROL, RESTORE, DEBLUR]))
    models = SimpleNamespace(model_repo=_Repo(present), digests=None)
    return asyncio.run(ModelRequirementChecker().check(resolved, _ctx(models)))


def test_field_requirement_is_met_by_any_recommendation_of_that_field():
    spec = {"type": "model", "field": "control_model", "hint": "Install the patch."}
    assert _check(spec, [("model_patch", "ctrl-bf16.safetensors")]).status == "ok"

    missing = _check(spec, [])
    assert missing.status == "missing"
    assert missing.hint == "Install the patch."
    assert missing.action.payload["field"] == "control_model"


def test_any_of_requirement_needs_one_of_the_fields():
    spec = {"type": "model", "any_of": ["restore_lora", "deblur_lora"]}
    assert _check(spec, [("lora", "deblur.safetensors")]).status == "ok"
    assert _check(spec, []).status == "missing"


def test_resolution_lists_candidates_per_field_and_leaves_other_specs_alone():
    resolved = resolve_requirement_fields({"type": "model", "field": "control_model"}, mode([CONTROL]))
    assert [f["filename"] for f in resolved["candidates"][0]["files"]] == ["ctrl-int8.safetensors", "ctrl-bf16.safetensors"]
    assert resolve_requirement_fields({"type": "model", "field": "absent"}, mode([CONTROL]))["candidates"] == [{"field": "absent", "files": []}]
    plain = {"type": "model", "filename": "x.safetensors"}
    assert resolve_requirement_fields(plain, mode([CONTROL])) is plain


def test_schema_needs_exactly_one_selector():
    ModelRequirementSchema.model_validate({"type": "model", "field": "control_model"})
    ModelRequirementSchema.model_validate({"type": "model", "any_of": ["a", "b"]})
    with pytest.raises(ValidationError):
        ModelRequirementSchema.model_validate({"type": "model", "field": "a", "filename": "b"})
    with pytest.raises(ValidationError):
        ModelRequirementSchema.model_validate({"type": "model"})


def test_describe_names_the_field():
    checker = ModelRequirementChecker()
    assert checker.describe({"field": "control_model"}) == "control_model"
    assert checker.describe({"any_of": ["a", "b"]}) == "one of a, b"


def test_install_recipe_id_slugs_the_preset_name():
    assert install_recipe_id("LTX-2.5") == "modes-ltx-2-5"
    assert install_recipe_id("Wan 2.1 / 2.2") == "modes-wan-2-1-2-2"


FORM = """fields:
  - name: "control_model"
    type: "model"
    label: "Control"
    configuration:
      model_type: "model_patch"
      recommendations:
        - name: "ctrl.safetensors"
          provider: "huggingface"
          ref: '{"repo": "o/r", "file": "ctrl.safetensors"}'
          size: "1 GB"
          size_bytes: 9
          precision: "int9"
requirements:
  - type: model
    field: "control_model"
  - type: model
    filename: "ctrl.safetensors"
  - type: model
    field: "nope"
"""


def _write(tmp_path: Path) -> Path:
    preset_dir = tmp_path / "presets" / "Foo"
    (preset_dir / "modes" / "motion").mkdir(parents=True)
    (preset_dir / "preset.yml").write_text(
        'schema: 1\nid: "01FIELDREQAAAAAAAAAAAAAAAA"\nname: "Foo"\nversion: "1.0.0"\ncategory: "video"\nengine: "native"\nmodes:\n  - motion\n'
    )
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    (preset_dir / "modes" / "motion" / "pipeline.yml").write_text("pipeline: []\n")
    (preset_dir / "modes" / "motion" / "form.yml").write_text(FORM)
    return preset_dir


def test_loader_resolves_field_requirements_onto_the_preset(tmp_path):
    _write(tmp_path)
    loader = PresetTemplateLoader([str(tmp_path / "presets")])
    loader.load_presets()
    spec = next(s for s in loader.presets[0].requirements if s.get("field") == "control_model")

    assert spec["mode"] == "motion"
    assert spec["candidates"][0]["files"][0]["filename"] == "ctrl.safetensors"


def test_linter_checks_recommendations_and_field_references(tmp_path):
    _write(tmp_path)
    messages = [(i.level, i.message) for i in PresetLinter([str(tmp_path / "presets")]).lint()]

    assert any(sev == "error" and "does not match 'size_bytes'" in msg for sev, msg in messages)
    assert any(sev == "error" and "'precision' must be one of" in msg for sev, msg in messages)
    assert any(sev == "error" and "field 'nope'" in msg for sev, msg in messages)
    assert any(sev == "warning" and "use 'field: control_model'" in msg for sev, msg in messages)
