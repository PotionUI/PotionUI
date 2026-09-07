"""GET .../presets/imported/{id}/source, POST .../reload, and
DELETE .../presets/imported/{id} - the reload/modify/delete row actions on
the "Imported presets" tab. Every test drives the real emitted-preset
directory shape (built via the real `emit_preset`), then exercises the
endpoint against it, since these endpoints only ever operate on what
`_scan_imported_presets` finds on disk.
"""

import json
from pathlib import Path

import pytest
import yaml

from backend import api
from backend.preset_import.emit import emit_preset
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.schema import LoraChainSelection
from backend.preset_import.suggest import suggest_fields

from ._form_helpers import form_from_roles

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _all_mappings(form_dict: dict) -> list:
    """Every field mapping in a `form` dict, walking rows/groups/sections
    (whose fields nest under their own "items") as well as top-level ones."""
    mappings = []

    def _walk(items):
        for item in items:
            if item["kind"] == "field":
                mappings.extend(item.get("mappings", []))
            elif "items" in item:
                _walk(item["items"])

    for tab in form_dict["tabs"]:
        _walk(tab["items"])
    return mappings


def _import_fixture(dest_root, *, model_family, variant="imported", with_sidecar=True):
    workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
    analysis = suggest_fields(workflow)
    form = form_from_roles(analysis, {"checkpoint"})
    result = emit_preset(
        workflow, form, [], model_family=model_family, variant=variant,
        display_name=f"{model_family} display", dest_root=dest_root,
    )
    if not with_sidecar:
        (result.preset_dir / "import.json").unlink()
    return result


@pytest.fixture(autouse=True)
def _imported_root(tmp_path, monkeypatch):
    root = tmp_path / "presets"
    monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", root)
    # No live container/DB in a plugin unit test - both container touchpoints
    # (requirements_summary peek, catalogue reload) must be no-ops, not a
    # reach into the real process.
    monkeypatch.setattr(api, "get_container", lambda: (_ for _ in ()).throw(AssertionError("no container configured")))
    return root


class TestGetImportedPresetSource:
    @pytest.mark.asyncio
    async def test_404_for_an_unknown_id(self, _imported_root):
        with pytest.raises(Exception) as exc_info:
            await api.get_imported_preset_source("no-such-id", current_user=None)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_the_stored_workflow_and_analysis_and_identity(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="SourceTest")

        response = await api.get_imported_preset_source(result.preset_id, current_user=None)

        assert response["workflow"] == json.loads(json.dumps(_load("sdxl_basic_api.json")))
        assert response["format"] == "api"
        assert response["model_family"] == "SourceTest"
        assert response["variant"] == "imported"
        assert response["display_name"] == "SourceTest display"
        assert any(c["role"] == "checkpoint" for c in response["candidates"])
        assert any(m["input_name"] == "ckpt_name" for m in _all_mappings(response["form"]))
        assert response["history"] == []

    @pytest.mark.asyncio
    async def test_falls_back_to_default_form_without_a_sidecar(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="NoSidecarSource", with_sidecar=False)

        response = await api.get_imported_preset_source(result.preset_id, current_user=None)

        # No sidecar to reproduce: the same "obvious fields" default_form a
        # brand-new analyze would offer - the checkpoint loader is obvious.
        assert any(m["input_name"] == "ckpt_name" for m in _all_mappings(response["form"]))


class TestReloadImportedPreset:
    @pytest.mark.asyncio
    async def test_404_for_an_unknown_id(self, _imported_root):
        with pytest.raises(Exception) as exc_info:
            await api.reload_imported_preset("no-such-id", current_user=None)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_reload_keeps_the_same_id_and_directory(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="ReloadTest")

        response = await api.reload_imported_preset(result.preset_id, current_user=None)

        assert response["preset_id"] == result.preset_id
        assert response["path"] == str(result.preset_dir)
        assert "errors" in response["lint"] and "warnings" in response["lint"]

    @pytest.mark.asyncio
    async def test_reload_reproduces_the_sidecars_form(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="ReloadFieldsTest")
        generation_before = (
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )
        preset_yml_before = yaml.safe_load((result.preset_dir / "preset.yml").read_text())

        await api.reload_imported_preset(result.preset_id, current_user=None)

        generation_after = (
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )
        preset_yml_after = yaml.safe_load((result.preset_dir / "preset.yml").read_text())
        # Same tab body (the checkpoint field, from the sidecar's stored
        # form) - proof it was reproduced, not dropped for the defaults.
        assert generation_after == generation_before
        assert preset_yml_after["id"] == preset_yml_before["id"]

    @pytest.mark.asyncio
    async def test_reload_without_a_sidecar_falls_back_to_default_form_with_a_warning(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="ReloadNoSidecar", with_sidecar=False)

        response = await api.reload_imported_preset(result.preset_id, current_user=None)

        assert "re-imported with the default form/history" in response["lint"]["warnings"]
        assert (result.preset_dir / "import.json").exists()  # reload writes a fresh sidecar going forward

    @pytest.mark.asyncio
    async def test_reload_after_deleting_the_source_workflow_file_errors_cleanly(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="ReloadMissingSource")
        (result.preset_dir / "import-source.json").unlink()
        workflows_dir = result.preset_dir / "modes" / result.mode / "files" / "workflows"
        for f in workflows_dir.iterdir():
            f.unlink()

        with pytest.raises(Exception) as exc_info:
            await api.reload_imported_preset(result.preset_id, current_user=None)
        assert exc_info.value.status_code == 404


class TestCorruptSidecarFormRaisesA400:
    """A sidecar's `form` that fails pydantic validation - e.g. a slider
    field left with an uncoercible default - must surface as the same 400
    `parse_form` gives the create path, not an unguarded `ValidationError`
    bubbling out of `ImportForm.model_validate` as a 500."""

    @staticmethod
    def _corrupt_sidecar_default(preset_dir):
        sidecar_path = preset_dir / "import.json"
        sidecar = json.loads(sidecar_path.read_text())
        sidecar["form"]["tabs"][0]["items"].append({
            "kind": "field",
            "field_name": "bad_slider",
            "field_type": "slider",
            "label": "Bad Slider",
            "default": "not-a-number",
            "config": None,
            "mappings": [],
        })
        sidecar_path.write_text(json.dumps(sidecar))

    @pytest.mark.asyncio
    async def test_get_source_with_a_corrupt_sidecar_form_raises_400_not_500(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="CorruptSourceTest")
        self._corrupt_sidecar_default(result.preset_dir)

        with pytest.raises(Exception) as exc_info:
            await api.get_imported_preset_source(result.preset_id, current_user=None)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_reload_with_a_corrupt_sidecar_form_raises_400_not_500(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="CorruptReloadTest")
        self._corrupt_sidecar_default(result.preset_dir)

        with pytest.raises(Exception) as exc_info:
            await api.reload_imported_preset(result.preset_id, current_user=None)
        assert exc_info.value.status_code == 400


class TestModifyViaImportWorkflowOverwrite:
    """The wizard's "Update preset" path: `POST .../presets/import` with
    `overwrite_preset_id` set - same endpoint a fresh import uses, just told
    which existing preset to become instead of refusing to collide with it."""

    @pytest.mark.asyncio
    async def test_overwrite_preset_id_updates_in_place(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="ModifyTest")

        body = api.ImportWorkflowRequest(
            workflow=_load("sdxl_basic_api.json"),
            form={
                "tabs": [
                    {
                        "id": "generation",
                        "label": "Generation",
                        "items": [
                            {
                                "kind": "field",
                                "field_name": "checkpoint",
                                "field_type": "model",
                                "label": "Checkpoint",
                                "mappings": [
                                    {"node_id": "4", "input_name": "ckpt_name", "transform": "strip_model_prefix"}
                                ],
                            }
                        ],
                    }
                ]
            },
            model_family="ModifyTest",
            variant="imported",
            display_name="ModifyTest renamed",
            overwrite_preset_id=result.preset_id,
        )

        response = await api.import_workflow(body, current_user=None)

        assert response["preset_id"] == result.preset_id
        assert response["path"] == str(result.preset_dir)
        preset_yml = yaml.safe_load((result.preset_dir / "preset.yml").read_text())
        assert preset_yml["id"] == result.preset_id
        assert preset_yml["name"] == "ModifyTest renamed"

    @pytest.mark.asyncio
    async def test_without_overwrite_preset_id_a_second_import_at_the_same_path_still_refuses(self, _imported_root):
        _import_fixture(_imported_root, model_family="NoOverwriteApi")

        body = api.ImportWorkflowRequest(
            workflow=_load("sdxl_basic_api.json"),
            model_family="NoOverwriteApi",
            variant="imported",
            display_name="X",
        )

        with pytest.raises(Exception) as exc_info:
            await api.import_workflow(body, current_user=None)
        assert exc_info.value.status_code == 400


class TestDeleteImportedPreset:
    @pytest.mark.asyncio
    async def test_404_for_an_unknown_id(self, _imported_root):
        with pytest.raises(Exception) as exc_info:
            await api.delete_imported_preset("no-such-id", current_user=None)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_removes_the_directory(self, _imported_root):
        result = _import_fixture(_imported_root, model_family="DeleteTest")
        assert result.preset_dir.exists()

        response = await api.delete_imported_preset(result.preset_id, current_user=None)

        assert response == {"deleted": True, "preset_id": result.preset_id}
        assert not result.preset_dir.exists()

    @pytest.mark.asyncio
    async def test_delete_refuses_a_hand_authored_preset_without_the_provenance_marker(self, _imported_root):
        """A preset _scan_imported_presets never found (no provenance marker)
        is simply unknown to this endpoint, not deletable by guessing its id -
        this is the "only presets this importer made" guarantee end to end."""
        handmade_dir = _imported_root / "HandMade" / "v1"
        handmade_dir.mkdir(parents=True)
        (handmade_dir / "preset.yml").write_text(yaml.safe_dump({"id": "HANDMADE1", "name": "Hand Made"}))
        (handmade_dir / "description.md").write_text("Hand-authored, not from the importer.")

        with pytest.raises(Exception) as exc_info:
            await api.delete_imported_preset("HANDMADE1", current_user=None)
        assert exc_info.value.status_code == 404
        assert handmade_dir.exists()

    @pytest.mark.asyncio
    async def test_delete_refuses_a_directory_outside_the_imported_root(self, _imported_root, monkeypatch):
        """Belt-and-braces: even if `_find_imported_preset` somehow returned
        an entry whose dir resolves outside the imported-presets root, the
        endpoint itself still refuses to delete it."""
        result = _import_fixture(_imported_root, model_family="OutsideTest")

        real_find = api._find_imported_preset

        def _poisoned_find(preset_id):
            entry = real_find(preset_id)
            if entry is None:
                return None
            entry.dir = Path("/tmp/definitely-not-the-imported-root")
            return entry

        monkeypatch.setattr(api, "_find_imported_preset", _poisoned_find)

        with pytest.raises(Exception) as exc_info:
            await api.delete_imported_preset(result.preset_id, current_user=None)
        assert exc_info.value.status_code == 400
        assert result.preset_dir.exists()  # the real directory was untouched


def _import_lora_chain_fixture(dest_root, *, source_text=None):
    """`lora_chain_img2img_api.json` (4 -> 101 -> 102 -> 3) imported with a
    `loras` picker replacing the WHOLE chain, the way the wizard's "Convert
    to LoRA picker" saves it - the emitted `<mode>.json` drops nodes
    101/102, the exact source keeps them."""
    workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
    analysis = suggest_fields(workflow)
    assert analysis.lora_chain is not None and analysis.lora_chain.lora_node_ids == ["101", "102"]
    form = form_from_roles(analysis, {"checkpoint", "lora_slot", "image"}).model_copy(
        update={"lora_chain": LoraChainSelection(replaced_node_ids=["101", "102"], kept_node_ids=[])}
    )
    return emit_preset(
        workflow, form, [], model_family="LoraChain", variant="imported",
        display_name="LoraChain display", dest_root=dest_root, source_text=source_text,
    )


class TestSourceWorkflowPreservation:
    """An imported preset re-opens the workflow exactly as it was imported
    (`import-source.json`), never the emitted `<mode>.json` the `lora_picker`
    rewrite already cut the replaced LoRA nodes from - otherwise the stored
    `form.lora_chain` names nodes the re-analysis can't find and every save
    of an untouched preset fails with "no detected LoRA chain"."""

    def test_the_exact_source_text_is_stored_verbatim(self, _imported_root):
        text = '{"4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}}, "3": {"class_type": "KSampler", "inputs": {"seed": 12345678901234567890, "model": ["4", 0]}}}'
        workflow = parse_api_workflow(json.loads(text))
        result = emit_preset(
            workflow, form_from_roles(suggest_fields(workflow), {"checkpoint"}), [],
            model_family="Exact", variant="v1", display_name="X", dest_root=_imported_root, source_text=text,
        )
        assert (result.preset_dir / "import-source.json").read_text(encoding="utf-8") == text

    def test_without_source_text_every_node_is_stored_including_the_replaced_loras(self, _imported_root):
        result = _import_lora_chain_fixture(_imported_root)
        source = json.loads((result.preset_dir / "import-source.json").read_text(encoding="utf-8"))
        emitted = json.loads((result.preset_dir / "modes" / result.mode / "files" / "workflows" / f"{result.mode}.json").read_text())
        assert {"101", "102"} <= set(source)
        assert not {"101", "102"} & set(emitted)
        assert source["3"]["inputs"]["model"] == ["102", 0]
        assert emitted["3"]["inputs"]["model"] != ["102", 0]

    @pytest.mark.asyncio
    async def test_source_reopens_the_chain_and_the_selection_that_replaced_it(self, _imported_root):
        result = _import_lora_chain_fixture(_imported_root)
        payload = await api.get_imported_preset_source(result.preset_id, current_user=None)
        assert {"101", "102"} <= set(payload["workflow"])
        assert payload["lora_chain"]["lora_node_ids"] == ["101", "102"]
        assert payload["form"]["lora_chain"] == {"replaced_node_ids": ["101", "102"], "kept_node_ids": []}

    @pytest.mark.asyncio
    async def test_saving_a_reopened_preset_unchanged_succeeds(self, _imported_root):
        """The wizard's "Update preset" straight after opening an imported
        preset: the `/source` payload goes back as-is with
        `overwrite_preset_id` set."""
        result = _import_lora_chain_fixture(_imported_root)
        payload = await api.get_imported_preset_source(result.preset_id, current_user=None)
        body = api.ImportWorkflowRequest(
            workflow=payload["workflow"], workflow_text=payload["workflow_text"],
            form=payload["form"], history=payload["history"],
            model_family=payload["model_family"], variant=payload["variant"], display_name=payload["display_name"],
            overwrite_preset_id=result.preset_id,
            schema_fingerprint=payload["schema_fingerprint"], schema_object_info_used=payload["object_info_used"],
        )
        response = await api.import_workflow(body, current_user=None)
        assert response["preset_id"] == result.preset_id
        sidecar = json.loads((result.preset_dir / "import.json").read_text())
        assert sidecar["form"]["lora_chain"] == {"replaced_node_ids": ["101", "102"], "kept_node_ids": []}
        assert (result.preset_dir / "import-source.json").read_text(encoding="utf-8") == payload["workflow_text"]

    @pytest.mark.asyncio
    async def test_reload_of_a_preset_with_a_replaced_chain_succeeds(self, _imported_root):
        result = _import_lora_chain_fixture(_imported_root)
        response = await api.reload_imported_preset(result.preset_id, current_user=None)
        assert response["preset_id"] == result.preset_id
        emitted = json.loads((result.preset_dir / "modes" / result.mode / "files" / "workflows" / f"{result.mode}.json").read_text())
        assert not {"101", "102"} & set(emitted)
        assert {"101", "102"} <= set(json.loads((result.preset_dir / "import-source.json").read_text()))

    @pytest.mark.asyncio
    async def test_a_preset_stored_without_the_source_file_drops_the_replaced_ids_and_still_saves(self, _imported_root):
        """Imported before `import-source.json` existed: the only workflow on
        disk is the emitted one, already without nodes 101/102, so the
        selection that replaced them has nothing left to name."""
        result = _import_lora_chain_fixture(_imported_root)
        (result.preset_dir / "import-source.json").unlink()
        payload = await api.get_imported_preset_source(result.preset_id, current_user=None)
        assert not {"101", "102"} & set(payload["workflow"])
        assert payload["form"]["lora_chain"] == {"replaced_node_ids": [], "kept_node_ids": []}
        body = api.ImportWorkflowRequest(
            workflow=payload["workflow"], workflow_text=payload["workflow_text"],
            form=payload["form"], history=payload["history"],
            model_family=payload["model_family"], variant=payload["variant"], display_name=payload["display_name"],
            overwrite_preset_id=result.preset_id,
        )
        response = await api.import_workflow(body, current_user=None)
        assert (result.preset_dir / "import-source.json").is_file()
        await api.reload_imported_preset(result.preset_id, current_user=None)
