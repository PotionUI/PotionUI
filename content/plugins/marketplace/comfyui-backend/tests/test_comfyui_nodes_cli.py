"""Tests for the `scripts/comfyui_nodes.py` CLI - run via `main([...])` with
stdout captured, not subprocess, since the plugin dir bootstrap `conftest.py`
already does is exactly what the script's own sys.path insert duplicates."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _PLUGIN_DIR / "scripts" / "comfyui_nodes.py"
FIXTURES = Path(__file__).parent / "fixtures"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("comfyui_nodes_cli", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli_module()


def _run(args, capsys):
    exit_code = cli.main(args)
    captured = capsys.readouterr()
    return exit_code, captured.out


class TestCoverage:
    def test_sdxl_basic_reports_no_uncatalogued_of_interest(self, capsys):
        exit_code, out = _run(["coverage", str(FIXTURES / "sdxl_basic_api.json")], capsys)
        assert exit_code == 0
        assert "mode: txt2img" in out
        assert "default form outline:" in out
        assert "Uncatalogued classes:" in out

    def test_flux_custom_sampling_outline_and_uncatalogued_line(self, capsys):
        exit_code, out = _run(["coverage", str(FIXTURES / "flux_custom_sampling_api.json")], capsys)
        assert exit_code == 0
        assert "sampler: 40 (SamplerCustomAdvanced)" in out
        assert "Sampling" in out
        assert "Uncatalogued classes: CLIPTextEncode, VAEDecode" in out

    def test_fail_on_uncatalogued_exits_1_when_list_nonempty(self, capsys):
        exit_code, _ = _run(
            ["coverage", str(FIXTURES / "flux_custom_sampling_api.json"), "--fail-on-uncatalogued"], capsys
        )
        assert exit_code == 1

    def test_fail_on_uncatalogued_exits_0_when_everything_catalogued(self, tmp_path, capsys):
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
        }
        workflow_path = tmp_path / "wf.json"
        workflow_path.write_text(json.dumps(workflow))
        exit_code, out = _run(["coverage", str(workflow_path), "--fail-on-uncatalogued"], capsys)
        assert "Uncatalogued classes: (none)" in out
        assert exit_code == 0

    def test_ui_format_export_exits_2_with_the_teaching_message(self, tmp_path, capsys):
        ui_export = {"nodes": [{"id": 1, "type": "KSampler"}], "links": [[1, 1, 0, 2, 0]]}
        path = tmp_path / "ui.json"
        path.write_text(json.dumps(ui_export))
        exit_code, out = _run(["coverage", str(path)], capsys)
        assert exit_code == 2
        assert "Export (API)" in out

    def test_unknown_class_is_listed_as_uncatalogued(self, tmp_path, capsys):
        workflow = {
            "1": {"class_type": "TotallyMadeUpNodeClass", "inputs": {"foo": 1}},
        }
        path = tmp_path / "wf.json"
        path.write_text(json.dumps(workflow))
        exit_code, out = _run(["coverage", str(path), "--fail-on-uncatalogued"], capsys)
        assert exit_code == 1
        assert "TotallyMadeUpNodeClass" in out


class TestScaffold:
    def test_from_object_info(self, capsys):
        exit_code, out = _run(
            ["scaffold", "KSampler", "--object-info", str(FIXTURES / "object_info_sdxl.json")], capsys
        )
        assert exit_code == 0
        assert "KSampler:" in out
        assert "model_chain" in out

    def test_unknown_class_in_object_info_exits_1(self, capsys):
        exit_code = cli.main(["scaffold", "NoSuchClass", "--object-info", str(FIXTURES / "object_info_sdxl.json")])
        assert exit_code == 1


class TestCheck:
    def test_shipped_catalog_is_valid(self, capsys):
        exit_code, out = _run(["check"], capsys)
        assert exit_code == 0
        assert "OK" in out


class TestList:
    def test_lists_catalogued_classes(self, capsys):
        exit_code, out = _run(["list"], capsys)
        assert exit_code == 0
        assert "CheckpointLoaderSimple" in out

    def test_category_filter(self, capsys):
        exit_code, out = _run(["list", "--category", "loader"], capsys)
        assert exit_code == 0
        for line in out.splitlines():
            assert "loader" in line
