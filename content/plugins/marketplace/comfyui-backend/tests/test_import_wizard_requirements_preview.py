"""The import wizard's Requirements step: `POST /presets/import/requirements`
runs `_infer_requirements` on a freshly-parsed workflow (no preset/backend
resolution exists yet - this is a pre-create preview) and evaluates every
entry directly through this plugin's own checker classes
(backend/requirements.py), against a `_ConfiguredBackend` built straight from
plugin settings rather than a registered core Backend.
"""

import asyncio

import pytest

from backend import api
from backend.preset_import.parser import parse_api_workflow
from backend.requirements import ComfyUIModelChecker, ComfyUINodeChecker
from src.plugin_api.presets import RequirementResult


def _fixture_with_custom_node_and_model():
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxlBase_v10.safetensors"}},
        "2": {"class_type": "FaceDetailer", "inputs": {"image": ["1", 0]}, "_meta": {"title": "Face Detailer"}},
    }


class TestPreviewRequirementsInference:
    @pytest.mark.asyncio
    async def test_no_inferred_requirements_returns_empty_results(self):
        body = api.AnalyzeWorkflowRequest(workflow={"1": {"class_type": "SaveImage", "inputs": {}}})
        result = await api.preview_workflow_requirements(body, current_user=None)
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_evaluates_each_inferred_entry_through_its_own_checker(self, monkeypatch):
        async def fake_node_check(self, spec, ctx):
            return RequirementResult(status="missing", detail="node not installed", hint="install it")

        async def fake_model_check(self, spec, ctx):
            return RequirementResult(status="ok", detail="model present")

        monkeypatch.setattr(ComfyUINodeChecker, "check", fake_node_check)
        monkeypatch.setattr(ComfyUIModelChecker, "check", fake_model_check)

        body = api.AnalyzeWorkflowRequest(workflow=_fixture_with_custom_node_and_model())
        result = await api.preview_workflow_requirements(body, current_user=None)

        by_type = {r["type"]: r for r in result["results"]}
        assert by_type["comfyui_node"]["name"] == "FaceDetailer"
        assert by_type["comfyui_node"]["status"] == "missing"
        assert by_type["comfyui_node"]["detail"] == "node not installed"
        assert by_type["comfyui_node"]["hint"] == "install it"

        assert by_type["comfyui_model"]["name"] == "sdxlBase_v10.safetensors"
        assert by_type["comfyui_model"]["status"] == "ok"
        assert by_type["comfyui_model"]["hint"] is None

    @pytest.mark.asyncio
    async def test_a_checker_that_raises_resolves_to_unknown_not_a_500(self, monkeypatch):
        async def boom(self, spec, ctx):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(ComfyUINodeChecker, "check", boom)

        body = api.AnalyzeWorkflowRequest(workflow=_fixture_with_custom_node_and_model())
        result = await api.preview_workflow_requirements(body, current_user=None)

        node_result = next(r for r in result["results"] if r["type"] == "comfyui_node")
        assert node_result["status"] == "unknown"
        assert "kaboom" in node_result["detail"]

    @pytest.mark.asyncio
    async def test_a_pending_check_past_the_budget_resolves_to_unknown(self, monkeypatch):
        async def slow_check(self, spec, ctx):
            await asyncio.sleep(10)
            return RequirementResult(status="ok", detail="too slow to matter")

        monkeypatch.setattr(ComfyUINodeChecker, "check", slow_check)
        monkeypatch.setattr(api, "_REQUIREMENTS_PREVIEW_BUDGET_SECONDS", 0.05)

        body = api.AnalyzeWorkflowRequest(workflow=_fixture_with_custom_node_and_model())
        result = await api.preview_workflow_requirements(body, current_user=None)

        node_result = next(r for r in result["results"] if r["type"] == "comfyui_node")
        assert node_result["status"] == "unknown"
        assert "Timed out" in node_result["detail"]

    @pytest.mark.asyncio
    async def test_checks_run_against_this_plugins_configured_backend(self, monkeypatch):
        """The checker sees a `ctx.backend` whose engine is "comfyui" and whose
        `config.get_base_url()` returns this plugin's own settings-derived
        base URL - not `None`, and not a registered core Backend lookup."""
        seen = {}

        async def capture(self, spec, ctx):
            seen["engine"] = ctx.backend.engine
            seen["base_url"] = ctx.backend.config.get_base_url()
            return RequirementResult(status="ok", detail="captured")

        monkeypatch.setattr(ComfyUINodeChecker, "check", capture)
        monkeypatch.setattr(api, "_get_comfyui_base_url", lambda: "http://example-comfyui:9999")

        body = api.AnalyzeWorkflowRequest(workflow=_fixture_with_custom_node_and_model())
        await api.preview_workflow_requirements(body, current_user=None)

        assert seen == {"engine": "comfyui", "base_url": "http://example-comfyui:9999"}
