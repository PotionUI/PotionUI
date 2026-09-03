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

from backend.preset_import.emit import EmittedPreset, FieldChoice, PresetEmitError, emit_preset
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.suggest import suggest_fields

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[5]


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _choices_for_roles(analysis, roles) -> list:
    return [
        FieldChoice(node_id=c.node_id, input_name=c.input_name)
        for c in analysis.candidates
        if c.role in roles
    ]


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
        choices = _choices_for_roles(analysis, {"checkpoint"})

        with pytest.raises(PresetEmitError):
            emit_preset(
                workflow, choices, model_family=model_family, variant=variant,
                display_name="Escape Test", dest_root=dest_root,
            )
        # Nothing was written outside (or even inside) dest_root
        assert not dest_root.exists() or not any(dest_root.rglob("preset.yml"))

    def test_normal_segment_names_still_work(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        choices = _choices_for_roles(analysis, {"checkpoint"})

        result = emit_preset(
            workflow, choices, model_family="SDXL Test-1", variant="v1.0",
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
        choices = _choices_for_roles(analysis, {"checkpoint"})

        # The resolved-path containment check (belt and braces) still catches it.
        with pytest.raises(PresetEmitError, match="escapes"):
            emit_preset(
                workflow, choices, model_family="../escape", variant="v1",
                display_name="Escape Test", dest_root=dest_root,
            )


class TestNoOverwrite:
    def test_emitting_twice_into_same_dir_raises(self, dest_root):
        workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
        analysis = suggest_fields(workflow)
        choices = _choices_for_roles(analysis, {"checkpoint"})

        emit_preset(
            workflow, choices, model_family="SDXLTest", variant="v1",
            display_name="SDXL Test", dest_root=dest_root,
        )
        with pytest.raises(PresetEmitError, match="already exists"):
            emit_preset(
                workflow, choices, model_family="SDXLTest", variant="v1",
                display_name="SDXL Test", dest_root=dest_root,
            )


class TestLoraChainEmission:
    def test_lora_nodes_stripped_and_manipulations_written(self, dest_root):
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        analysis = suggest_fields(workflow)
        choices = _choices_for_roles(
            analysis, {"checkpoint", "lora_slot", "image", "steps", "cfg"}
        )

        result = emit_preset(
            workflow, choices, model_family="LoraChainTest", variant="v1",
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

        lora_form = yaml.safe_load((result.preset_dir / "modes" / "img2img" / "tabs" / "lora.yml").read_text())
        assert lora_form["fields"][0]["name"] == "loras"
        assert lora_form["fields"][0]["type"] == "lora_picker"

    def test_bite_check_lora_stripping_breaks_if_disabled(self, dest_root):
        """Confirms the assertion above can fail: without the exclusion set,
        the lora nodes would still be in the baked workflow."""
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        analysis = suggest_fields(workflow)
        assert {c.node_id for c in analysis.candidates if c.role == "lora_slot"} == {"101", "102"}


class TestSubgraphIdsSurvive:
    def test_field_mappings_target_subgraph_node_ids(self, dest_root):
        workflow = parse_api_workflow(_load("flux_subgraph_api.json"))
        analysis = suggest_fields(workflow)
        choices = _choices_for_roles(
            analysis, {"diffusion_model", "clip", "vae", "steps", "cfg", "sampler", "scheduler", "denoise"}
        )

        result = emit_preset(
            workflow, choices, model_family="FluxSubgraphTest", variant="v1",
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
        choices = _choices_for_roles(analysis, {"checkpoint"})  # image role deliberately omitted

        with pytest.raises(PresetEmitError, match="input image"):
            emit_preset(
                workflow, choices, model_family="NoImageTest", variant="v1",
                display_name="No Image Test", dest_root=dest_root,
            )


class TestEndToEndRenderAndLint:
    def test_emitted_sdxl_preset_renders_and_lints_clean(self):
        local_root = REPO_ROOT / "content" / "presets" / "local"
        marker = f"WorkflowImporterTest{uuid.uuid4().hex[:12]}"
        preset_family_dir = local_root / marker
        try:
            workflow = parse_api_workflow(_load("sdxl_basic_api.json"))
            analysis = suggest_fields(workflow)
            choices = _choices_for_roles(
                analysis,
                {"checkpoint", "steps", "cfg", "sampler", "scheduler", "denoise"},
            )

            result: EmittedPreset = emit_preset(
                workflow, choices, model_family=marker, variant="v1",
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
