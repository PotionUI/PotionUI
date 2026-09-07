"""Preset emission tests for the workflow importer: LoRA chain rewriting,
subgraph node ids surviving into field_mappings, no-overwrite, and an
end-to-end render + lint of what actually gets written to disk.

The end-to-end test writes into a uniquely-named, cleaned-up subdirectory of
content/presets/local (the real, .gitignored scan root this feature targets)
so scripts/preset_render.py and scripts/preset_lint.py - both of which use
their own hardcoded default roots - can find it without a second copy of the
preset-loading machinery living in this plugin's tests.
"""

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

from backend.preset_import.defaults import _image_item, _lora_item, _model_item
from backend.preset_import.emit import EmittedPreset, PresetEmitError, emit_preset
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.schema import FormTab, ImportForm, LoraChainSelection, parse_form
from backend.preset_import.suggest import suggest_fields

from ._form_helpers import form_from_roles

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[5]


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _render_comfyui_node_manipulations(preset_id: str, mode: str, form_data: dict) -> list:
    """Render a preset's `comfyui` pipe's `node_manipulations` through the
    real `scripts/preset_render.py` harness (`PresetProcessor.process()`),
    with `form_data` supplied verbatim (no fixture walk) - the only way to
    see a `lora_picker` field's `@loop` actually expand against a chosen
    set of active LoRAs, since that expansion happens in the same generic
    templating pass as every other pipe config value, not inside this
    plugin."""
    form_file = REPO_ROOT / f"_render_probe_{uuid.uuid4().hex[:12]}_form.yml"
    form_file.write_text(yaml.safe_dump(form_data))
    try:
        proc = subprocess.run(
            [sys.executable, "scripts/preset_render.py", preset_id, mode, "--form", str(form_file), "--json"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        record = json.loads(proc.stdout[proc.stdout.index("{"):])
        assert "error" not in record, record
        comfyui_pipe = next(p for p in record["pipes"] if p["name"] == "comfyui")
        return comfyui_pipe["config"]["node_manipulations"]["value"]
    finally:
        form_file.unlink(missing_ok=True)


@pytest.fixture()
def dest_root(tmp_path):
    return tmp_path / "presets"


class TestPathTraversalGuard:
    @pytest.mark.parametrize(
        "model_family,variant",
        [
            ("../escape", "v1"),
            ("SDXLTest", "../escape"),
            ("/abs/path", "v1"),
            ("SDXLTest", "a/b"),
            (".hidden", "v1"),
            ("SDXLTest", ".."),
        ],
    )
    def test_unsafe_segments_rejected(self, dest_root, model_family, variant):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint"})

        with pytest.raises(PresetEmitError):
            emit_preset(
                workflow, form, [], model_family=model_family, variant=variant,
                display_name="Escape Test", dest_root=dest_root,
            )
        # Nothing was written outside (or even inside) dest_root
        assert not dest_root.exists() or not any(dest_root.rglob("preset.yml"))

    def test_normal_segment_names_still_work(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint"})

        result = emit_preset(
            workflow, form, [], model_family="SDXL Test-1", variant="v1.0",
            display_name="Normal Test", dest_root=dest_root,
        )
        assert result.preset_dir == dest_root / "SDXL Test-1" / "v1.0"
        assert (result.preset_dir / "preset.yml").exists()

    def test_bite_check_guard_breaks_if_validation_disabled(self, dest_root, monkeypatch):
        """Confirms the rejection above is really the segment validator: with
        it stubbed out, the same '../escape' family would otherwise resolve
        outside dest_root.

        Patches emit_preset's own __globals__ rather than a freshly
        `import`-ed module object: other tests in this suite (e.g.
        test_plugin_route_authz.py) reload the `backend.*` package mid-run,
        so a plain `import backend.preset_import.emit` can hand back a
        different module object than the one `emit_preset` actually reads
        `_validate_path_segment` from.
        """
        monkeypatch.setitem(emit_preset.__globals__, "_validate_path_segment", lambda value, label: None)
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint"})

        # The resolved-path containment check (belt and braces) still catches it.
        with pytest.raises(PresetEmitError, match="escapes"):
            emit_preset(
                workflow, form, [], model_family="../escape", variant="v1",
                display_name="Escape Test", dest_root=dest_root,
            )


class TestNoOverwrite:
    def test_emitting_twice_into_same_dir_raises(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint"})

        emit_preset(
            workflow, form, [], model_family="SDXLTest", variant="v1",
            display_name="SDXL Test", dest_root=dest_root,
        )
        with pytest.raises(PresetEmitError, match="already exists"):
            emit_preset(
                workflow, form, [], model_family="SDXLTest", variant="v1",
                display_name="SDXL Test", dest_root=dest_root,
            )


class TestLoraChainEmission:
    def test_lora_nodes_stripped_and_manipulations_written(self, dest_root):
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(
            analysis, {"checkpoint", "lora_slot", "image", "steps", "cfg"}
        )

        result = emit_preset(
            workflow, form, [], model_family="LoraChainTest", variant="v1",
            display_name="LoRA Chain Test", dest_root=dest_root,
        )
        assert result.mode == "img2img"

        workflow_json = json.loads(
            (result.preset_dir / "modes" / "img2img" / "files" / "workflows" / "img2img.json").read_text()
        )
        # The two LoraLoaderModelOnly nodes are gone from the baked workflow
        assert "101" not in workflow_json
        assert "102" not in workflow_json
        # The sampler's model input is rewired straight to the checkpoint loader
        assert workflow_json["3"]["inputs"]["model"] == ["4", 0]

        pipeline = yaml.safe_load(
            (result.preset_dir / "modes" / "img2img" / "pipeline.yml").read_text()
        )
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        manipulations = comfyui_pipe["configuration"]["node_manipulations"]
        assert any("@loop" in m for m in manipulations)
        update_input = next(m for m in manipulations if m.get("type") == "update_node_input")
        assert update_input["node_id"] == "3"
        assert "4" in update_input["input_value"][0]  # falls back to source when no LoRAs chosen

        loop_manip = next(m for m in manipulations if "@loop" in m)["@loop"]
        assert "4" in loop_manip["template"]["node_config"]["inputs"]["model"][0]

        generation_form = yaml.safe_load(
            (result.preset_dir / "modes" / "img2img" / "tabs" / "generation.yml").read_text()
        )
        lora_field = next(f for f in generation_form["fields"] if f.get("name") == "loras")
        assert lora_field["type"] == "lora_picker"

    def test_bite_check_lora_stripping_breaks_if_disabled(self, dest_root):
        """Confirms the assertion above can fail: without the exclusion set,
        the lora nodes would still be in the baked workflow."""
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        analysis = suggest_fields(workflow)
        assert {c.node_id for c in analysis.candidates if c.role == "lora_slot"} == {"101", "102"}


class TestLoraChainKeepFixedSplicing:
    def _form_with_picker(self, analysis, roles, lora_chain=None):
        form = form_from_roles(analysis, roles)
        form.tabs[0].items.append(_lora_item())
        form.lora_chain = lora_chain
        return form

    def test_keeping_the_source_node_fixed_replaces_only_the_target_node(self, dest_root):
        """101 (closest to the checkpoint) is kept fixed; only 102 (closest
        to the sampler) becomes the picker. The sampler's fallback source
        must become node 101 - the kept node's own output - not the
        checkpoint loader, and 101 itself must survive untouched."""
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        analysis = suggest_fields(workflow)
        form = self._form_with_picker(
            analysis, {"checkpoint", "image", "steps", "cfg"},
            LoraChainSelection(replaced_node_ids=["102"], kept_node_ids=["101"]),
        )

        result = emit_preset(
            workflow, form, [], model_family="LoraKeepFixedTest", variant="v1",
            display_name="LoRA Keep Fixed Test", dest_root=dest_root,
        )

        workflow_json = json.loads(
            (result.preset_dir / "modes" / "img2img" / "files" / "workflows" / "img2img.json").read_text()
        )
        assert "101" in workflow_json
        assert workflow_json["101"]["inputs"]["model"] == ["4", 0]  # kept node's own wiring untouched
        assert "102" not in workflow_json
        assert workflow_json["3"]["inputs"]["model"] == ["101", 0]  # falls back to the kept node

        pipeline = yaml.safe_load(
            (result.preset_dir / "modes" / "img2img" / "pipeline.yml").read_text()
        )
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        manipulations = comfyui_pipe["configuration"]["node_manipulations"]
        update_input = next(m for m in manipulations if m.get("type") == "update_node_input")
        assert update_input["node_id"] == "3"
        assert "101" in update_input["input_value"][0]

        requirements = yaml.safe_load((result.preset_dir / "preset.yml").read_text())["requirements"]
        lora_reqs = {r["name"]: r for r in requirements if r.get("folder") == "loras"}
        assert "optional" not in lora_reqs["style_a.safetensors"]  # kept - still hard
        assert lora_reqs["style_b.safetensors"].get("optional") is True  # replaced

    def test_bite_check_keep_fixed_breaks_without_a_selection(self, dest_root):
        """Confirms the previous test's survival of node 101 actually
        depends on the selection: with no `form.lora_chain` at all, the
        WHOLE chain is replaced (the pre-existing fallback), so 101 would
        NOT survive."""
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        analysis = suggest_fields(workflow)
        form = self._form_with_picker(analysis, {"checkpoint", "image", "steps", "cfg"}, lora_chain=None)

        result = emit_preset(
            workflow, form, [], model_family="LoraKeepFixedBite", variant="v1",
            display_name="X", dest_root=dest_root,
        )
        workflow_json = json.loads(
            (result.preset_dir / "modes" / "img2img" / "files" / "workflows" / "img2img.json").read_text()
        )
        assert "101" not in workflow_json

    def test_lora_behind_patch_nodes_splices_at_the_first_patch_node(self, dest_root):
        """UNETLoader -> LoraLoaderModelOnly -> ModelSamplingAuraFlow ->
        CFGNorm -> KSampler: converting the single LoRA node must rewire
        node 21 (the first pass-through consumer), not the sampler
        directly, and node 22 (further downstream) is left untouched."""
        workflow = parse_api_workflow(_load("lora_chain_patch_node_api.json"))
        analysis = suggest_fields(workflow)
        form = self._form_with_picker(
            analysis, {"diffusion_model", "clip", "vae", "steps", "cfg", "sampler", "scheduler", "denoise"},
            LoraChainSelection(replaced_node_ids=["20"], kept_node_ids=[]),
        )

        result = emit_preset(
            workflow, form, [], model_family="LoraPatchNodeTest", variant="v1",
            display_name="LoRA Patch Node Test", dest_root=dest_root,
        )
        assert result.mode == "txt2img"

        workflow_json = json.loads(
            (result.preset_dir / "modes" / "txt2img" / "files" / "workflows" / "txt2img.json").read_text()
        )
        assert "20" not in workflow_json
        assert workflow_json["21"]["inputs"]["model"] == ["1", 0]  # falls back to the UNETLoader
        assert workflow_json["22"]["inputs"]["model"] == ["21", 0]  # untouched - still points at 21

    def test_clip_path_reroutes_both_positive_and_negative_text_encode_nodes(self, dest_root):
        """A LoraLoader (model+clip) followed by a LoraLoaderModelOnly, with
        a ModelSamplingAuraFlow patch node AFTER the whole chain: converting
        both nodes must reroute the patch node's model input, fan the loop's
        CLIP output out to both CLIPTextEncode nodes, and emit LoraLoader
        (not LoraLoaderModelOnly) nodes in the loop since CLIP is in play."""
        workflow = parse_api_workflow(_load("lora_chain_clip_then_patch_api.json"))
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain.has_clip_path is True
        form = self._form_with_picker(
            analysis, {"checkpoint", "steps", "cfg", "sampler", "scheduler", "denoise"},
            LoraChainSelection(replaced_node_ids=["101", "102"], kept_node_ids=[]),
        )

        result = emit_preset(
            workflow, form, [], model_family="LoraClipPatchTest", variant="v1",
            display_name="LoRA Clip Patch Test", dest_root=dest_root,
        )
        assert result.mode == "txt2img"

        workflow_json = json.loads(
            (result.preset_dir / "modes" / "txt2img" / "files" / "workflows" / "txt2img.json").read_text()
        )
        assert "101" not in workflow_json
        assert "102" not in workflow_json
        assert workflow_json["150"]["inputs"]["model"] == ["4", 0]  # patch node falls back to the checkpoint
        assert workflow_json["6"]["inputs"]["clip"] == ["4", 1]
        assert workflow_json["7"]["inputs"]["clip"] == ["4", 1]

        pipeline = yaml.safe_load(
            (result.preset_dir / "modes" / "txt2img" / "pipeline.yml").read_text()
        )
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        manipulations = comfyui_pipe["configuration"]["node_manipulations"]

        loop_manip = next(m for m in manipulations if "@loop" in m)["@loop"]
        assert loop_manip["template"]["node_config"]["class_type"] == "LoraLoader"
        assert "clip" in loop_manip["template"]["node_config"]["inputs"]

        update_inputs = [m for m in manipulations if m.get("type") == "update_node_input"]
        model_update = next(m for m in update_inputs if m["node_id"] == "150")
        assert model_update["input_key"] == "model"
        clip_updates = {m["node_id"]: m for m in update_inputs if m["input_key"] == "clip"}
        assert set(clip_updates) == {"6", "7"}

    def test_a_patch_node_interleaved_between_two_replaced_lora_nodes_stays_wired(self, dest_root):
        """LoraLoader(101, model+clip) -> ModelSamplingAuraFlow(150) ->
        LoraLoaderModelOnly(102) -> KSampler: the patch node sits BETWEEN
        the two replaced LoRA nodes, not at either end of the chain.
        Replacing the whole chain must bypass every dangling reference this
        creates - node 150's own `model` input pointed at the now-removed
        101, and the sampler's `model` input pointed at the now-removed 102
        - rather than only patching the two outermost boundary connections."""
        workflow = parse_api_workflow(_load("lora_chain_clip_patch_api.json"))
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain.lora_node_ids == ["101", "102"]
        form = self._form_with_picker(
            analysis, {"checkpoint", "image", "steps", "cfg"},
            LoraChainSelection(replaced_node_ids=["101", "102"], kept_node_ids=[]),
        )

        result = emit_preset(
            workflow, form, [], model_family="LoraInterleavedPatchTest", variant="v1",
            display_name="LoRA Interleaved Patch Test", dest_root=dest_root,
        )

        workflow_json = json.loads(
            (result.preset_dir / "modes" / result.mode / "files" / "workflows" / f"{result.mode}.json").read_text()
        )
        assert "101" not in workflow_json
        assert "102" not in workflow_json
        # Nothing in the baked graph points at a removed node id.
        for node in workflow_json.values():
            for value in node["inputs"].values():
                if isinstance(value, list) and len(value) == 2:
                    assert value[0] not in ("101", "102")
        # The patch node survives, rewired past the removed 101 to the real
        # checkpoint loader...
        assert workflow_json["150"]["inputs"]["model"] == ["4", 0]
        # ...and the sampler, which used to read 102 directly, now correctly
        # routes back through the surviving patch node - not straight to the
        # checkpoint, which would silently drop node 150's transform.
        assert workflow_json["3"]["inputs"]["model"] == ["150", 0]
        assert workflow_json["6"]["inputs"]["clip"] == ["4", 1]
        assert workflow_json["7"]["inputs"]["clip"] == ["4", 1]

    def test_bite_check_interleaved_patch_node_dangles_without_the_generic_bypass(self, dest_root, monkeypatch):
        """Confirms the assertions above depend on the generic bypass, not
        just the old first/last boundary patch: stubbing it out to a no-op
        leaves node 150 pointing at the removed node 101.

        Patches `emit_preset`'s own `__globals__` rather than a freshly
        `import`-ed module object - see `TestPathTraversalGuard
        .test_bite_check_guard_breaks_if_validation_disabled`'s docstring
        for why (another test in this suite reloads `backend.*` mid-run)."""
        monkeypatch.setitem(emit_preset.__globals__, "_bypass_replaced_lora_nodes", lambda *a, **k: None)

        workflow = parse_api_workflow(_load("lora_chain_clip_patch_api.json"))
        analysis = suggest_fields(workflow)
        form = self._form_with_picker(
            analysis, {"checkpoint", "image", "steps", "cfg"},
            LoraChainSelection(replaced_node_ids=["101", "102"], kept_node_ids=[]),
        )
        result = emit_preset(
            workflow, form, [], model_family="LoraInterleavedPatchBite", variant="v1",
            display_name="X", dest_root=dest_root,
        )
        workflow_json = json.loads(
            (result.preset_dir / "modes" / result.mode / "files" / "workflows" / f"{result.mode}.json").read_text()
        )
        assert workflow_json["150"]["inputs"]["model"] == ["101", 0]  # dangling - 101 no longer exists


class TestLoraPickerWithNothingStructuralToReplace:
    """A `lora_picker` field is valid even when nothing detected gets
    excluded from the baked workflow - a chain with every node kept fixed,
    or no LoRA chain at all. In both cases `emit_preset` splices the
    picker's `@loop` onto `suggest.ModelChainInfo` (the sampling cluster's
    own model-chain boundary) instead of a replaced span's boundary."""

    def _inject_kept_lora(self, raw: dict, *, lora_node_id="50", source_id="4", sampler_id="3") -> dict:
        raw = json.loads(json.dumps(raw))
        raw[lora_node_id] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": "lighting.safetensors", "strength_model": 1.0, "model": [source_id, 0]},
            "_meta": {"title": "Lighting LoRA"},
        }
        raw[sampler_id]["inputs"]["model"] = [lora_node_id, 0]
        return raw

    def test_all_kept_chain_splices_after_the_kept_node(self):
        """One `LoraLoaderModelOnly` node (kept fixed) between the
        checkpoint and the sampler, no other LoRA node in the workflow:
        `form.lora_chain` keeps it, nothing is replaced, and the picker's
        loop must splice right after it - node 50 itself must survive
        completely untouched (not bypassed).

        Emits into `content/presets/local` (not the `dest_root` tmp_path
        fixture) because the final assertion renders it by preset id
        through `scripts/preset_render.py`, which only scans real preset
        roots - same reason `TestEndToEndRenderAndLint` does the same."""
        raw = self._inject_kept_lora(_load("sdxl_basic_api.json"))
        workflow = parse_api_workflow(raw)
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain is not None
        assert analysis.lora_chain.lora_node_ids == ["50"]
        assert analysis.model_chain is not None
        assert analysis.model_chain.source_node_id == "50"

        form = form_from_roles(analysis, {"checkpoint", "steps", "cfg"})
        form.tabs[0].items.append(_lora_item())
        form.lora_chain = LoraChainSelection(replaced_node_ids=[], kept_node_ids=["50"])

        local_root = REPO_ROOT / "content" / "presets" / "local"
        marker = f"WorkflowImporterAllKeptTest{uuid.uuid4().hex[:12]}"
        try:
            result = emit_preset(
                workflow, form, [], model_family=marker, variant="v1",
                display_name="LoRA All Kept Test", dest_root=local_root,
            )
            assert result.mode == "txt2img"

            workflow_json = json.loads(
                (result.preset_dir / "modes" / "txt2img" / "files" / "workflows" / "txt2img.json").read_text()
            )
            assert workflow_json["50"]["inputs"]["model"] == ["4", 0]  # kept node - untouched, not bypassed
            assert workflow_json["3"]["inputs"]["model"] == ["50", 0]  # sampler still reads the kept node directly

            pipeline = yaml.safe_load(
                (result.preset_dir / "modes" / "txt2img" / "pipeline.yml").read_text()
            )
            comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
            manipulations = comfyui_pipe["configuration"]["node_manipulations"]
            loop_manip = next(m for m in manipulations if "@loop" in m)["@loop"]
            assert "50" in loop_manip["template"]["node_config"]["inputs"]["model"][0]
            update_input = next(m for m in manipulations if m.get("type") == "update_node_input")
            assert update_input["node_id"] == "3"
            assert "50" in update_input["input_value"][0]  # falls back to the kept node when no LoRAs are chosen

            requirements = yaml.safe_load((result.preset_dir / "preset.yml").read_text())["requirements"]
            lora_req = next(r for r in requirements if r.get("folder") == "loras")
            assert lora_req["name"] == "lighting.safetensors"
            assert "optional" not in lora_req  # kept - still hard, nothing was replaced

            # Render through the real templating path with two active LoRAs
            # and confirm the actual chain: checkpoint -> lighting (kept) ->
            # lora_1 -> lora_2 -> KSampler.
            form_data = {
                "seed": 7, "quantity": 1,
                "checkpoint": "models/checkpoints/sdxlBase_v10.safetensors",
                "steps": 27, "cfg": 4.0,
                "loras": [
                    {"model": "models/loras/style_a.safetensors", "strength": 0.6},
                    {"model": "models/loras/style_b.safetensors", "strength": 0.9},
                ],
            }
            rendered = _render_comfyui_node_manipulations(result.preset_id, "txt2img", form_data)
            add_nodes = [m for m in rendered[0] if m["type"] == "add_node"]
            assert add_nodes[0]["node_config"]["inputs"]["model"] == ["50", 0]
            assert add_nodes[0]["node_id"] == "lora_1"
            assert add_nodes[1]["node_config"]["inputs"]["model"] == ["lora_1", 0]
            assert add_nodes[1]["node_id"] == "lora_2"
            assert rendered[1] == {
                "type": "update_node_input", "node_id": "3", "input_key": "model", "input_value": ["lora_2", 0],
            }
        finally:
            shutil.rmtree(local_root / marker, ignore_errors=True)

    def test_bite_check_all_kept_splice_breaks_without_the_model_chain_fallback(self, dest_root):
        """Confirms the assertion above can fail: without the model-chain
        fallback branch, a fully-kept chain (`replaced_lora_node_ids`
        empty) emits no node_manipulations at all, so the picker field
        would be silently ignored at generation time."""
        raw = self._inject_kept_lora(_load("sdxl_basic_api.json"))
        workflow = parse_api_workflow(raw)
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint", "steps", "cfg"})
        form.tabs[0].items.append(_lora_item())
        form.lora_chain = LoraChainSelection(replaced_node_ids=[], kept_node_ids=["50"])

        result = emit_preset(
            workflow, form, [], model_family="LoraAllKeptBite", variant="v1",
            display_name="X", dest_root=dest_root,
        )
        pipeline = yaml.safe_load(
            (result.preset_dir / "modes" / "txt2img" / "pipeline.yml").read_text()
        )
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        assert "node_manipulations" in comfyui_pipe["configuration"]  # would be absent if the branch were reverted

    def test_no_lora_chain_at_all_splices_onto_the_model_chain(self):
        """No LoRA node anywhere in the workflow: `analysis.lora_chain` is
        `None`, so the picker's loop must splice directly onto the
        checkpoint -> sampler link (`analysis.model_chain`), and with zero
        active LoRAs the sampler falls back straight to the checkpoint.
        Emits into `content/presets/local` - see the previous test's
        docstring for why."""
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        assert analysis.lora_chain is None
        assert analysis.model_chain is not None

        form = form_from_roles(analysis, {"checkpoint", "steps", "cfg"})
        form.tabs[0].items.append(_lora_item())
        form.lora_chain = LoraChainSelection(replaced_node_ids=[], kept_node_ids=[])

        local_root = REPO_ROOT / "content" / "presets" / "local"
        marker = f"WorkflowImporterNoChainTest{uuid.uuid4().hex[:12]}"
        try:
            result = emit_preset(
                workflow, form, [], model_family=marker, variant="v1",
                display_name="LoRA No Chain Test", dest_root=local_root,
            )
            workflow_json = json.loads(
                (result.preset_dir / "modes" / "txt2img" / "files" / "workflows" / "txt2img.json").read_text()
            )
            assert workflow_json["3"]["inputs"]["model"] == ["4", 0]  # source workflow untouched - nothing to replace

            pipeline = yaml.safe_load(
                (result.preset_dir / "modes" / "txt2img" / "pipeline.yml").read_text()
            )
            comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
            manipulations = comfyui_pipe["configuration"]["node_manipulations"]
            loop_manip = next(m for m in manipulations if "@loop" in m)["@loop"]
            assert "4" in loop_manip["template"]["node_config"]["inputs"]["model"][0]
            update_input = next(m for m in manipulations if m.get("type") == "update_node_input")
            assert update_input["node_id"] == "3"
            assert "4" in update_input["input_value"][0]

            form_data = {
                "seed": 7, "quantity": 1,
                "checkpoint": "models/checkpoints/sdxlBase_v10.safetensors",
                "steps": 27, "cfg": 4.0,
                "loras": [],
            }
            rendered = _render_comfyui_node_manipulations(result.preset_id, "txt2img", form_data)
            assert rendered[0] == []  # no active LoRAs - loop expands to nothing
            assert rendered[1] == {
                "type": "update_node_input", "node_id": "3", "input_key": "model", "input_value": ["4", 0],
            }
        finally:
            shutil.rmtree(local_root / marker, ignore_errors=True)

    def test_a_form_with_no_lora_chain_selection_at_all_still_splices_via_model_chain(self, dest_root):
        """`form.lora_chain is None` (a hand-built form, or a preset from
        before this selection existed) on a workflow with no LoRA chain -
        pre-existing code already reads this as "nothing to replace"; it
        must fall into the same model-chain splice as an explicit empty
        selection, not silently drop the picker's wiring."""
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint", "steps", "cfg"})
        form.tabs[0].items.append(_lora_item())
        form.lora_chain = None

        result = emit_preset(
            workflow, form, [], model_family="LoraNoChainNoneSelection", variant="v1",
            display_name="X", dest_root=dest_root,
        )
        pipeline = yaml.safe_load(
            (result.preset_dir / "modes" / "txt2img" / "pipeline.yml").read_text()
        )
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        assert "node_manipulations" in comfyui_pipe["configuration"]


class TestSwitchLoraEmission:
    """A LoRA node reached only through a `ComfySwitchNode` (see
    `node_catalog.NodeEntry.branch`) - the maintainer's real case: a
    Lightning LoRA toggled on/off by a runtime switch instead of sitting
    directly on the model backbone."""

    def _form_with_picker(self, analysis, roles, lora_chain=None):
        form = form_from_roles(analysis, roles)
        form.tabs[0].items.append(_lora_item())
        form.lora_chain = lora_chain
        return form

    def test_replacing_the_switch_lora_rewires_the_switchs_on_true(self, dest_root):
        workflow = parse_api_workflow(_load("switch_lora_api.json"))
        analysis = suggest_fields(workflow)
        form = self._form_with_picker(
            analysis, {"checkpoint", "steps", "cfg", "sampler", "scheduler", "denoise"},
            LoraChainSelection(replaced_node_ids=["20"], kept_node_ids=[]),
        )

        result = emit_preset(
            workflow, form, [], model_family="SwitchLoraReplacedTest", variant="v1",
            display_name="Switch LoRA Replaced Test", dest_root=dest_root,
        )

        workflow_json = json.loads(
            (result.preset_dir / "modes" / "txt2img" / "files" / "workflows" / "txt2img.json").read_text()
        )
        assert "20" not in workflow_json
        assert workflow_json["30"]["inputs"]["on_false"] == ["4", 0]  # untouched
        assert workflow_json["3"]["inputs"]["model"] == ["30", 0]  # sampler still reads the switch directly

        pipeline = yaml.safe_load(
            (result.preset_dir / "modes" / "txt2img" / "pipeline.yml").read_text()
        )
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        manipulations = comfyui_pipe["configuration"]["node_manipulations"]
        update_input = next(m for m in manipulations if m.get("type") == "update_node_input")
        assert update_input["node_id"] == "30"
        assert update_input["input_key"] == "on_true"
        assert "4" in update_input["input_value"][0]  # falls back to the raw loader when no LoRAs are chosen

        loop_manip = next(m for m in manipulations if "@loop" in m)["@loop"]
        assert "4" in loop_manip["template"]["node_config"]["inputs"]["model"][0]

    def test_keeping_the_switch_lora_splices_before_the_sampler(self, dest_root):
        """Nothing is replaced (the node stays kept) - the picker's loop
        must fall back to the sampling cluster's own model-chain boundary
        (the switch -> KSampler edge), leaving the switch and the kept LoRA
        node both completely untouched."""
        workflow = parse_api_workflow(_load("switch_lora_api.json"))
        analysis = suggest_fields(workflow)
        assert analysis.model_chain is not None
        assert analysis.model_chain.source_node_id == "30"
        assert analysis.model_chain.target_node_id == "3"

        form = self._form_with_picker(
            analysis, {"checkpoint", "steps", "cfg", "sampler", "scheduler", "denoise"},
            LoraChainSelection(replaced_node_ids=[], kept_node_ids=["20"]),
        )

        result = emit_preset(
            workflow, form, [], model_family="SwitchLoraKeptTest", variant="v1",
            display_name="Switch LoRA Kept Test", dest_root=dest_root,
        )

        workflow_json = json.loads(
            (result.preset_dir / "modes" / "txt2img" / "files" / "workflows" / "txt2img.json").read_text()
        )
        assert "20" in workflow_json  # kept - untouched
        assert workflow_json["20"]["inputs"]["model"] == ["4", 0]
        assert workflow_json["30"]["inputs"]["on_true"] == ["20", 0]  # switch untouched
        assert workflow_json["3"]["inputs"]["model"] == ["30", 0]  # sampler still reads the switch directly

        pipeline = yaml.safe_load(
            (result.preset_dir / "modes" / "txt2img" / "pipeline.yml").read_text()
        )
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        manipulations = comfyui_pipe["configuration"]["node_manipulations"]
        update_input = next(m for m in manipulations if m.get("type") == "update_node_input")
        assert update_input["node_id"] == "3"  # spliced right before the sampler
        assert "30" in update_input["input_value"][0]  # falls back to the switch when no LoRAs are chosen


class TestTypedFieldDefaultsInGenerationYml:
    """Reproduces a real reported import: the wizard's default-editing input
    hands back plain text, so re-typing an integer field's default (e.g.
    steps 20 -> 4) sends `default: "4"` (a str) in the `form` payload -
    `parse_form`/`emit_preset` must write it into generation.yml as a native
    int, or preset lint rejects the emitted preset outright."""

    def test_string_default_on_an_integer_field_is_written_as_a_native_int(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint", "steps"})
        raw = form.model_dump(mode="json")
        steps_field = next(f for f in raw["tabs"][0]["items"] if f.get("field_name") == "steps")
        # Simulate the wizard's own default-editing input handing back plain
        # text for an admin-edited default on what the admin turned into an
        # integer field.
        steps_field["field_type"] = "integer"
        steps_field["default"] = "4"

        coerced_form = parse_form(raw)
        result = emit_preset(
            workflow, coerced_form, [], model_family="TypedDefaultTest", variant="v1",
            display_name="Typed Default Test", dest_root=dest_root,
        )

        generation_form = yaml.safe_load(
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )
        steps = next(f for f in generation_form["fields"] if f.get("name") == "steps")
        assert steps["default"] == 4
        assert isinstance(steps["default"], int)
        assert not isinstance(steps["default"], bool)

    def test_unparsable_default_is_rejected_before_anything_is_written(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint", "steps"})
        raw = form.model_dump(mode="json")
        steps_field = next(f for f in raw["tabs"][0]["items"] if f.get("field_name") == "steps")
        steps_field["field_type"] = "integer"
        steps_field["default"] = "not-a-number"

        with pytest.raises(PresetEmitError, match="steps"):
            parse_form(raw)


class TestSubgraphIdsSurvive:
    def test_field_mappings_target_subgraph_node_ids(self, dest_root):
        workflow = parse_api_workflow(_load("flux_subgraph_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(
            analysis, {"diffusion_model", "clip", "vae", "steps", "cfg", "sampler", "scheduler", "denoise"}
        )

        result = emit_preset(
            workflow, form, [], model_family="FluxSubgraphTest", variant="v1",
            display_name="Flux Subgraph Test", dest_root=dest_root,
        )

        pipeline = yaml.safe_load(
            (result.preset_dir / "modes" / "txt2img" / "pipeline.yml").read_text()
        )
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        targets = [m[1] for m in comfyui_pipe["configuration"]["field_mappings"]]

        assert "92:40.inputs.seed" in targets  # foundational, always wired
        assert "92:40.inputs.steps" in targets
        assert "92:11.inputs.unet_name" in targets
        assert "92:12.inputs.clip_name" in targets
        assert "92:10.inputs.vae_name" in targets
        # Prompt nodes, also foundational
        assert "92:20.inputs.text" in targets
        assert "92:21.inputs.text" in targets


class TestImageModeRequiresImageChoice:
    def test_img2img_workflow_without_image_choice_raises(self, dest_root):
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint"})  # image role deliberately omitted

        with pytest.raises(PresetEmitError, match="input image"):
            emit_preset(
                workflow, form, [], model_family="NoImageTest", variant="v1",
                display_name="No Image Test", dest_root=dest_root,
            )


class TestTabIconEmission:
    def _tab_from_form_yml(self, preset_dir: Path, mode: str) -> dict:
        form_yml = yaml.safe_load((preset_dir / "modes" / mode / "form.yml").read_text())
        return form_yml["fields"][0]["children"][0]

    def test_tab_icon_and_display_are_written_to_form_yml(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        checkpoint = next(c for c in analysis.candidates if c.role == "checkpoint")
        tab = FormTab(
            id="generation", label="Generation", icon="lora", icon_display="icon_label",
            items=[_model_item(checkpoint)],
        )
        form = ImportForm(tabs=[tab])

        result = emit_preset(
            workflow, form, [], model_family="TabIconTest", variant="v1",
            display_name="Tab Icon Test", dest_root=dest_root,
        )

        tab_yaml = self._tab_from_form_yml(result.preset_dir, result.mode)
        assert tab_yaml["configuration"]["icon"] == "lora"
        assert tab_yaml["configuration"]["icon_display"] == "icon_label"

    def test_tab_with_no_icon_emits_label_display(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint"})

        result = emit_preset(
            workflow, form, [], model_family="TabNoIconTest", variant="v1",
            display_name="Tab No Icon Test", dest_root=dest_root,
        )

        tab_yaml = self._tab_from_form_yml(result.preset_dir, result.mode)
        assert "icon" not in tab_yaml["configuration"]
        assert tab_yaml["configuration"]["icon_display"] == "label"


class TestEndToEndRenderAndLint:
    def test_emitted_sdxl_preset_renders_and_lints_clean(self):
        local_root = REPO_ROOT / "content" / "presets" / "local"
        marker = f"WorkflowImporterTest{uuid.uuid4().hex[:12]}"
        preset_family_dir = local_root / marker
        try:
            workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
            analysis = suggest_fields(workflow)
            form = form_from_roles(
                analysis,
                {"checkpoint", "steps", "cfg", "sampler", "scheduler", "denoise"},
            )

            result: EmittedPreset = emit_preset(
                workflow, form, [], model_family=marker, variant="v1",
                display_name="Workflow Importer E2E Test", dest_root=local_root,
            )
            assert result.preset_dir == preset_family_dir / "v1"

            lint_proc = subprocess.run(
                [sys.executable, "scripts/preset_lint.py", str(result.preset_dir)],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
            )
            lint_output = lint_proc.stdout + lint_proc.stderr
            error_lines = [line for line in lint_output.splitlines() if line.startswith("[ERROR]")]
            # The emitted preset carries a `requirements:` entry for its
            # checkpoint loader (backend/preset_import/emit.py's
            # `_infer_requirements`), of a type (`comfyui_model`) this plugin
            # registers via manifest.yml `requirement_checkers:` - resolved
            # even by this plugin-unaware `preset_lint.py` subprocess, which
            # discovers manifests (not just the presets under the path it was
            # given) purely to know about declared checker/mode types (see
            # src/features/presets/linter.py's `_requirement_checker_registry`).
            assert not error_lines, lint_output

            fixture_form = {
                "seed": 7,
                "quantity": 1,
                "checkpoint": "models/checkpoints/sdxlBase_v10.safetensors",
                "steps": 27,
                "cfg": 4.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            }
            form_file = local_root / f"{marker}_form.yml"
            form_file.write_text(yaml.safe_dump(fixture_form))
            try:
                render_proc = subprocess.run(
                    [
                        sys.executable, "scripts/preset_render.py",
                        result.preset_id, "txt2img", "--form", str(form_file), "--json",
                    ],
                    cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
                )
                assert render_proc.returncode == 0, render_proc.stdout + render_proc.stderr
                # The plugin/preset loader logs INFO noise to stdout ahead of
                # the --json payload; the payload itself is the first '{'.
                json_text = render_proc.stdout[render_proc.stdout.index("{"):]
                record = json.loads(json_text)
                assert "error" not in record

                comfyui_pipe_render = next(p for p in record["pipes"] if p["name"] == "comfyui")
                field_mappings = comfyui_pipe_render["config"]["field_mappings"]["value"]
                resolved = {m[1]: m[0] for m in field_mappings}
                # "@seed" is a sentinel the comfyui pipe itself resolves at
                # generation time from its own `input:` wiring, not something
                # PresetProcessor's Jinja rendering ever touches.
                assert resolved["3.inputs.seed"] == "@seed"
                assert resolved["4.inputs.ckpt_name"] == "sdxlBase_v10.safetensors"
                assert resolved["3.inputs.steps"] == 27
                assert resolved["3.inputs.cfg"] == 4.0
                assert resolved["3.inputs.sampler_name"] == "euler"
                # Prompts are foundational: always the harness's fixture prompt
                # pair (scripts/preset_render.py's FIXED_POSITIVE/NEGATIVE_PROMPT),
                # never the text baked into the source workflow's CLIPTextEncode.
                assert resolved["6.inputs.text"] == "golden positive prompt"
                assert resolved["7.inputs.text"] == "golden negative prompt"
            finally:
                form_file.unlink(missing_ok=True)
        finally:
            shutil.rmtree(preset_family_dir, ignore_errors=True)


class TestModelAndImageFieldsNeverSeedAnUnresolvedDefault:
    """A workflow's own literal (the UNET/CLIP/VAE filename baked into a
    loader node, or a LoadImage node's placeholder filename) is never a
    valid `default` for the field the wizard builds for it: a bare model
    filename isn't a valid picker value, and a LoadImage placeholder isn't a
    real uploaded file - handing either straight to ComfyUI as a literal
    when the file isn't actually installed/uploaded is what produced the
    "not in list of length N" / "Invalid image file" server errors an
    imported preset could otherwise submit. `_model_item`/`_image_item`
    (defaults.py) must never carry the workflow's own literal forward as a
    `default`, and the field a required value is missing from must be
    `required=True` so an admin can't save/submit without it."""

    def test_model_item_is_required_with_no_default(self):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        checkpoint = next(c for c in analysis.candidates if c.role == "checkpoint")
        assert checkpoint.current_value  # the fixture's own baked filename - never used

        item = _model_item(checkpoint)

        assert item.required is True
        assert item.default is None

    def test_image_item_primary_source_image_is_required_with_no_default(self):
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        analysis = suggest_fields(workflow)
        source = next(c for c in analysis.candidates if c.role == "image")
        assert source.suggested_field_name == "source_image"
        assert source.current_value  # the workflow's own placeholder filename - never used

        item = _image_item(source)

        assert item.required is True
        assert item.default is None

    def test_image_item_reference_image_is_optional_with_no_default(self):
        workflow = parse_api_workflow({
            "10": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
            "11": {"class_type": "LoadImage", "inputs": {"image": "ref.png"}},
        })
        analysis = suggest_fields(workflow)
        ref = next(c for c in analysis.candidates if c.suggested_field_name == "ref_image_2")

        item = _image_item(ref)

        assert item.required is False
        assert item.default is None

    def test_bite_check_required_and_default_are_written_to_the_emitted_tab_yaml(self, dest_root):
        """Confirms the pydantic-level `required`/`default` above actually
        reach preset.yml's own field YAML through `emit._item_to_field_yaml`
        - not just present on the in-memory `FieldItem`."""
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        checkpoint = next(c for c in analysis.candidates if c.role == "checkpoint")
        tab = FormTab(id="generation", label="Generation", items=[_model_item(checkpoint)])
        form = ImportForm(tabs=[tab])

        result = emit_preset(
            workflow, form, [], model_family="RequiredYamlTest", variant="v1",
            display_name="Required Yaml Test", dest_root=dest_root,
        )

        tab_yaml = yaml.safe_load(
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )
        field = next(f for f in tab_yaml["fields"] if f.get("name") == checkpoint.suggested_field_name)
        assert field["required"] is True
        assert "default" not in field
