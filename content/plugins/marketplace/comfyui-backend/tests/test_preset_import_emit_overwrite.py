"""`emit_preset`'s overwrite/preset_id path (reload/modify) and its
`import.json` sidecar - the record reload/modify reads back to reproduce an
import's exact `form`/`history` instead of falling back to "obvious" defaults.
"""

import json
from pathlib import Path

import pytest
import yaml

from backend.preset_import.emit import (
    IMPORT_SIDECAR_FILENAME,
    IMPORTER_VERSION,
    PresetEmitError,
    emit_preset,
)
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.schema import FieldItem, FieldMapping, FormTab, ImportForm

from ._form_helpers import form_from_roles

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _checkpoint_form(analysis) -> ImportForm:
    return form_from_roles(analysis, {"checkpoint"})


class TestSidecarWritten:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def test_sidecar_file_is_written_alongside_preset_yml(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        from backend.preset_import.suggest import suggest_fields

        analysis = suggest_fields(workflow)
        form = _checkpoint_form(analysis)

        result = emit_preset(
            workflow, form, [], model_family="SidecarTest", variant="v1",
            display_name="Sidecar Test", dest_root=dest_root,
        )

        sidecar_path = result.preset_dir / IMPORT_SIDECAR_FILENAME
        assert sidecar_path.exists()
        sidecar = json.loads(sidecar_path.read_text())
        assert sidecar["importer_version"] == IMPORTER_VERSION
        assert sidecar["model_family"] == "SidecarTest"
        assert sidecar["variant"] == "v1"
        assert sidecar["display_name"] == "Sidecar Test"
        assert sidecar["mode"] == result.mode
        assert isinstance(sidecar["created_at"], int)
        assert sidecar["source_file"] == "import-source.json"

    def test_sidecar_carries_the_exact_form_and_history_submitted(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        from backend.preset_import.suggest import suggest_fields

        analysis = suggest_fields(workflow)
        checkpoint_candidate = next(c for c in analysis.candidates if c.role == "checkpoint")
        form = ImportForm(
            tabs=[
                FormTab(
                    id="generation",
                    label="Generation",
                    items=[
                        FieldItem(
                            field_name="my_checkpoint",
                            field_type="model",
                            label="My Checkpoint",
                            mappings=[
                                FieldMapping(
                                    node_id=checkpoint_candidate.node_id,
                                    input_name=checkpoint_candidate.input_name,
                                    transform="strip_model_prefix",
                                )
                            ],
                        )
                    ],
                )
            ]
        )

        result = emit_preset(
            workflow, form, [], model_family="SidecarTest2", variant="v1",
            display_name="Sidecar Test 2", dest_root=dest_root,
        )

        sidecar = json.loads((result.preset_dir / IMPORT_SIDECAR_FILENAME).read_text())
        stored_field = sidecar["form"]["tabs"][0]["items"][0]
        assert stored_field["field_name"] == "my_checkpoint"
        assert stored_field["field_type"] == "model"
        assert stored_field["label"] == "My Checkpoint"
        assert stored_field["mappings"][0]["node_id"] == checkpoint_candidate.node_id
        assert stored_field["mappings"][0]["input_name"] == checkpoint_candidate.input_name
        assert sidecar["history"] == []


class TestOverwrite:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def _emit_once(self, dest_root, *, model_family="OverwriteTest", variant="v1"):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        from backend.preset_import.suggest import suggest_fields

        analysis = suggest_fields(workflow)
        form = _checkpoint_form(analysis)
        return emit_preset(
            workflow, form, [], model_family=model_family, variant=variant,
            display_name="Overwrite Test", dest_root=dest_root,
        )

    def test_overwrite_without_preset_id_is_rejected(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        with pytest.raises(PresetEmitError, match="requires preset_id"):
            emit_preset(
                workflow, ImportForm(tabs=[]), [], model_family="X", variant="v1", display_name="X",
                dest_root=dest_root, overwrite=True,
            )

    def test_overwrite_of_a_nonexistent_directory_is_rejected(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        with pytest.raises(PresetEmitError, match="no preset exists"):
            emit_preset(
                workflow, ImportForm(tabs=[]), [], model_family="NeverImported", variant="v1", display_name="X",
                dest_root=dest_root, overwrite=True, preset_id="some-id",
            )

    def test_same_id_overwrite_keeps_the_id_and_directory(self, dest_root):
        first = self._emit_once(dest_root)

        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        from backend.preset_import.suggest import suggest_fields

        analysis = suggest_fields(workflow)
        second = emit_preset(
            workflow, _checkpoint_form(analysis), [], model_family="OverwriteTest", variant="v1",
            display_name="Overwrite Test Renamed", dest_root=dest_root,
            overwrite=True, preset_id=first.preset_id,
        )

        assert second.preset_id == first.preset_id
        assert second.preset_dir == first.preset_dir
        preset_yml = yaml.safe_load((second.preset_dir / "preset.yml").read_text())
        assert preset_yml["id"] == first.preset_id
        assert preset_yml["name"] == "Overwrite Test Renamed"

    def test_overwrite_drops_stale_files_from_a_previous_shape(self, dest_root):
        """A tab dropped between the original import and a reload/modify
        must not leave its old tab file behind."""
        from backend.preset_import.suggest import suggest_fields

        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        generation_form = form_from_roles(analysis, {"checkpoint"})
        advanced_form = form_from_roles(
            analysis, {"steps", "cfg", "sampler", "scheduler", "denoise"},
            tab_id="advanced", tab_label="Advanced",
        )
        two_tab_form = ImportForm(tabs=[generation_form.tabs[0], advanced_form.tabs[0]])
        first = emit_preset(
            workflow, two_tab_form, [], model_family="DropStale", variant="v1",
            display_name="Drop Stale", dest_root=dest_root,
        )
        tabs_dir = first.preset_dir / "modes" / first.mode / "tabs"
        assert (tabs_dir / "advanced.yml").exists()

        workflow2 = parse_api_workflow(_load("sdxl_basic_api.json"))
        checkpoint_only = _checkpoint_form(suggest_fields(workflow2))
        second = emit_preset(
            workflow2, checkpoint_only, [], model_family="DropStale", variant="v1",
            display_name="Drop Stale", dest_root=dest_root,
            overwrite=True, preset_id=first.preset_id,
        )
        tabs_dir2 = second.preset_dir / "modes" / second.mode / "tabs"
        assert not (tabs_dir2 / "advanced.yml").exists()

    def test_without_overwrite_a_second_emit_still_refuses(self, dest_root):
        first = self._emit_once(dest_root, model_family="NoOverwrite", variant="v1")
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        from backend.preset_import.suggest import suggest_fields

        analysis = suggest_fields(workflow)
        with pytest.raises(PresetEmitError, match="already exists"):
            emit_preset(
                workflow, _checkpoint_form(analysis), [], model_family="NoOverwrite", variant="v1",
                display_name="X", dest_root=dest_root,
            )
