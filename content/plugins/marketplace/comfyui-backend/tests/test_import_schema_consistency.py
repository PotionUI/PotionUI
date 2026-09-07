"""One `object_info` resolution shared by analyze, source, requirements-
preview, import and reload, and the `schema_fingerprint`/
`schema_object_info_used` drift check that guards a save against a workflow
classifying differently than what the caller was shown (see
`api._resolve_analysis`, `suggest.classification_fingerprint`,
`emit._check_schema_drift`).

Two defects this covers directly:

- A noncatalog node with a bare `text`/`prompt`-shaped literal input is only
  correctly excluded from the prompt fallback's name-based guess
  (`suggest._fallback_prompt_roles`) when `object_info` narrows its scope to
  image/video-producing nodes; every endpoint that later validates or emits
  the saved form must resolve `object_info` the same way analyze did, or a
  field the wizard legitimately offered gets rejected as "a prompt input".
- A noncatalog loader's model-file input (e.g. `vae_name`) is only
  recognized as a `comfyui_model` candidate through `object_info`
  enrichment; the emitted preset's `requirements:` block depends on that
  candidate reaching `_infer_requirements`, independent of whether the
  admin put a form field on it at all.
"""

from pathlib import Path

import pytest
import yaml

from backend import api
from backend.preset_import.emit import emit_preset
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.schema import ImportForm
from backend.requirements import _object_info_cache

from ._form_helpers import raw_form_dict_for_roles

# An all-in-one generator with a baked-in `prompt` input (no separate
# sampler node - see suggest._fallback_prompt_roles's docstring for why this
# is the one workflow shape the fallback name-based prompt detection runs
# for) plus a noncatalog helper node whose own `text` literal is NOT a
# prompt - it must stay a plain, offerable form field.
_HELPER_WORKFLOW = {
    "1": {"class_type": "MyAllInOneGenerator", "inputs": {"prompt": "a cat wearing a hat"}},
    "2": {"class_type": "MyStringHelper", "inputs": {"text": "static filename suffix"}},
    "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
}

_HELPER_OBJECT_INFO = {
    "MyAllInOneGenerator": {"input": {"required": {"prompt": ["STRING", {"multiline": True}]}}, "output": ["IMAGE"]},
    "MyStringHelper": {"input": {"required": {"text": ["STRING", {}]}}, "output": ["STRING"]},
}

# A noncatalog VAE loader - `vae_name` is a recognized model-file input name
# (suggest._ENRICHMENT_MODEL_FILE_BY_INPUT_NAME) but only ever turned into a
# `model`-typed candidate with a `suggested_folder` once `object_info` shows
# the class actually declares it as a combo.
_VAE_WORKFLOW = {
    "1": {"class_type": "MyCustomVAELoader", "inputs": {"vae_name": "myVae.safetensors"}},
    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
}

_VAE_OBJECT_INFO = {
    "MyCustomVAELoader": {
        "input": {"required": {"vae_name": [["myVae.safetensors", "other.safetensors"]]}},
        "output": ["VAE"],
    },
}


@pytest.fixture(autouse=True)
def _clear_object_info_cache():
    _object_info_cache._ready.clear()
    _object_info_cache._inflight.clear()
    yield
    _object_info_cache._ready.clear()
    _object_info_cache._inflight.clear()


@pytest.fixture(autouse=True)
def _imported_root(tmp_path, monkeypatch):
    root = tmp_path / "presets"
    monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", root)
    monkeypatch.setattr(api, "get_container", lambda: (_ for _ in ()).throw(AssertionError("no container configured")))
    return root


def _mock_object_info(monkeypatch, object_info):
    async def _fake_fetch_object_info(backend_id, base_url):
        return object_info

    monkeypatch.setattr(api, "_fetch_object_info", _fake_fetch_object_info)


def _mock_object_info_unreachable(monkeypatch):
    async def _boom(backend_id, base_url):
        raise RuntimeError("backend unreachable")

    monkeypatch.setattr(api, "_fetch_object_info", _boom)


class TestNoncatalogHelperFieldNotMisclassifiedAsPrompt:
    """Scenario (a): the STRING helper offered as a normal field must not be
    reclassified as another prompt input when the same workflow is saved."""

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_analyze_with_object_info_offers_the_helper_as_a_plain_field(self, monkeypatch):
        _mock_object_info(monkeypatch, _HELPER_OBJECT_INFO)
        body = api.AnalyzeWorkflowRequest(workflow=_HELPER_WORKFLOW)

        result = await api.analyze_workflow(body, current_user=None)

        assert result["object_info_used"] is True
        prompt_candidates = [c for c in result["candidates"] if c["role"] == "prompt_positive"]
        assert [c["node_id"] for c in prompt_candidates] == ["1"]
        helper_candidate = next(c for c in result["candidates"] if c["node_id"] == "2")
        assert helper_candidate["role"] == "literal"
        assert result["schema_fingerprint"]

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_bite_check_without_object_info_the_helper_is_misclassified_as_a_second_prompt(self, monkeypatch):
        """Confirms the assertion above is really about object_info narrowing
        the fallback's scope - with none available, both nodes qualify."""
        body = api.AnalyzeWorkflowRequest(workflow=_HELPER_WORKFLOW)  # no object_info mock call at all

        result = await api.analyze_workflow(body, current_user=None)

        assert result["object_info_used"] is False
        prompt_candidates = [c for c in result["candidates"] if c["role"] == "prompt_positive"]
        assert sorted(c["node_id"] for c in prompt_candidates) == ["1", "2"]

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_saving_with_the_same_object_info_does_not_reject_the_offered_helper_mapping(self, monkeypatch):
        _mock_object_info(monkeypatch, _HELPER_OBJECT_INFO)
        analyze_result = await api.analyze_workflow(
            api.AnalyzeWorkflowRequest(workflow=_HELPER_WORKFLOW), current_user=None
        )
        form = raw_form_dict_for_roles(analyze_result, {"literal"})
        assert form["tabs"][0]["items"], "the helper's text input should be offered as a literal field"

        body = api.ImportWorkflowRequest(
            workflow=_HELPER_WORKFLOW,
            form=form,
            history=[],
            model_family="HelperFieldTest",
            variant="imported",
            display_name="Helper Field Test",
            schema_fingerprint=analyze_result["schema_fingerprint"],
            schema_object_info_used=analyze_result["object_info_used"],
        )

        # The save succeeding at all (no PresetEmitError -> 400) is the
        # assertion: pre-fix, validate_against_workflow rejected the helper's
        # mapping as targeting "a prompt input". Not asserting `lint` is
        # clean - this workflow's noncatalog nodes get a `comfyui_node`
        # requirement lint can't check without a checker registry, which
        # only exists once the full app (not this bare test process) builds
        # its container.
        response = await api.import_workflow(body, current_user=None)
        assert response["preset_id"]


class TestNoncatalogModelFileRequirementInference:
    """Scenario (d): a noncatalog loader's model-file input must surface as
    a `comfyui_model` requirement in both the preview and the saved preset,
    even with no form field mapped to it."""

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_requirements_preview_includes_the_enriched_model_file(self, monkeypatch):
        _mock_object_info(monkeypatch, _VAE_OBJECT_INFO)
        body = api.RequirementsPreviewRequest(workflow=_VAE_WORKFLOW)

        result = await api.preview_workflow_requirements(body, current_user=None)

        model_results = [r for r in result["results"] if r["type"] == "comfyui_model"]
        assert [r["name"] for r in model_results] == ["myVae.safetensors"]

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_bite_check_without_object_info_the_preview_misses_the_model_file(self, monkeypatch):
        body = api.RequirementsPreviewRequest(workflow=_VAE_WORKFLOW)  # no object_info mock

        result = await api.preview_workflow_requirements(body, current_user=None)

        assert [r["type"] for r in result["results"]] == ["comfyui_node"]

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_saved_preset_carries_the_model_requirement_with_no_form_field(self, monkeypatch, tmp_path):
        _mock_object_info(monkeypatch, _VAE_OBJECT_INFO)
        analyze_result = await api.analyze_workflow(
            api.AnalyzeWorkflowRequest(workflow=_VAE_WORKFLOW), current_user=None
        )

        body = api.ImportWorkflowRequest(
            workflow=_VAE_WORKFLOW,
            form=None,  # deliberately no field for vae_name
            history=[],
            model_family="VaeReqTest",
            variant="imported",
            display_name="VAE Requirement Test",
            schema_fingerprint=analyze_result["schema_fingerprint"],
            schema_object_info_used=analyze_result["object_info_used"],
        )

        response = await api.import_workflow(body, current_user=None)

        preset_yml = yaml.safe_load((Path(response["path"]) / "preset.yml").read_text())
        model_requirements = [r for r in preset_yml["requirements"] if r["type"] == "comfyui_model"]
        assert model_requirements == [{"type": "comfyui_model", "folder": "vae", "name": "myVae.safetensors"}]


class TestReloadResolvesObjectInfoConsistently:
    """`reload_imported_preset`'s own two bugs: its no-sidecar fallback built
    the default form/history from an unenriched analysis, and neither branch
    passed `object_info` to `emit_preset` at all - so a reload could drop a
    noncatalog model requirement `import_workflow` had just emitted, and
    could never benefit from the drift check either."""

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_reload_without_a_sidecar_still_infers_the_noncatalog_model_requirement(self, monkeypatch, tmp_path):
        _mock_object_info(monkeypatch, _VAE_OBJECT_INFO)
        workflow = parse_api_workflow(_VAE_WORKFLOW)
        created = emit_preset(
            workflow,
            ImportForm(tabs=[]),
            [],
            model_family="ReloadNoSidecarVae",
            variant="imported",
            display_name="Reload No Sidecar Vae",
            dest_root=tmp_path / "presets",
            object_info=_VAE_OBJECT_INFO,
        )
        (created.preset_dir / "import.json").unlink()

        response = await api.reload_imported_preset(created.preset_id, current_user=None)

        preset_yml = yaml.safe_load((Path(response["path"]) / "preset.yml").read_text())
        model_requirements = [r for r in preset_yml["requirements"] if r["type"] == "comfyui_model"]
        assert model_requirements == [{"type": "comfyui_model", "folder": "vae", "name": "myVae.safetensors"}]

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_reload_refuses_when_the_schema_drifted_since_the_original_save(self, monkeypatch):
        _mock_object_info(monkeypatch, _HELPER_OBJECT_INFO)
        analyze_result = await api.analyze_workflow(
            api.AnalyzeWorkflowRequest(workflow=_HELPER_WORKFLOW), current_user=None
        )
        form = raw_form_dict_for_roles(analyze_result, {"literal"})
        created = await api.import_workflow(
            api.ImportWorkflowRequest(
                workflow=_HELPER_WORKFLOW,
                form=form,
                history=[],
                model_family="ReloadDriftTest",
                variant="imported",
                display_name="Reload Drift Test",
                schema_fingerprint=analyze_result["schema_fingerprint"],
                schema_object_info_used=analyze_result["object_info_used"],
            ),
            current_user=None,
        )

        drifted_object_info = {
            **_HELPER_OBJECT_INFO,
            "MyStringHelper": {**_HELPER_OBJECT_INFO["MyStringHelper"], "output": ["IMAGE"]},
        }
        _mock_object_info(monkeypatch, drifted_object_info)

        with pytest.raises(Exception) as exc_info:
            await api.reload_imported_preset(created["preset_id"], current_user=None)
        assert exc_info.value.status_code == 400
        assert "classification" in exc_info.value.detail


class TestSchemaDriftRefusal:
    """Scenario (g): a workflow that classifies differently between analyze
    and save must be refused, never silently re-saved under the new
    classification."""

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_a_classification_change_between_analyze_and_save_is_refused(self, monkeypatch):
        _mock_object_info(monkeypatch, _HELPER_OBJECT_INFO)
        analyze_result = await api.analyze_workflow(
            api.AnalyzeWorkflowRequest(workflow=_HELPER_WORKFLOW), current_user=None
        )
        form = raw_form_dict_for_roles(analyze_result, {"literal"})

        # The live server's schema drifted since analyze: the helper node is
        # now (incorrectly, from a real server's perspective, but this is
        # exactly what "the server's schema changed" looks like) reported as
        # producing an IMAGE too, widening the prompt fallback's scope.
        drifted_object_info = {
            **_HELPER_OBJECT_INFO,
            "MyStringHelper": {**_HELPER_OBJECT_INFO["MyStringHelper"], "output": ["IMAGE"]},
        }
        _mock_object_info(monkeypatch, drifted_object_info)

        body = api.ImportWorkflowRequest(
            workflow=_HELPER_WORKFLOW,
            form=form,
            history=[],
            model_family="DriftTest",
            variant="imported",
            display_name="Drift Test",
            schema_fingerprint=analyze_result["schema_fingerprint"],
            schema_object_info_used=analyze_result["object_info_used"],
        )

        with pytest.raises(Exception) as exc_info:
            await api.import_workflow(body, current_user=None)
        assert exc_info.value.status_code == 400
        assert "classification" in exc_info.value.detail
        assert "prompt input" not in exc_info.value.detail

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_object_info_unreachable_at_save_after_being_available_at_analyze_is_refused(self, monkeypatch):
        _mock_object_info(monkeypatch, _HELPER_OBJECT_INFO)
        analyze_result = await api.analyze_workflow(
            api.AnalyzeWorkflowRequest(workflow=_HELPER_WORKFLOW), current_user=None
        )
        form = raw_form_dict_for_roles(analyze_result, {"literal"})

        _mock_object_info_unreachable(monkeypatch)

        body = api.ImportWorkflowRequest(
            workflow=_HELPER_WORKFLOW,
            form=form,
            history=[],
            model_family="UnreachableAtSaveTest",
            variant="imported",
            display_name="Unreachable At Save Test",
            schema_fingerprint=analyze_result["schema_fingerprint"],
            schema_object_info_used=analyze_result["object_info_used"],
        )

        with pytest.raises(Exception) as exc_info:
            await api.import_workflow(body, current_user=None)
        assert exc_info.value.status_code == 400
        assert "none is reachable now" in exc_info.value.detail

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_object_info_becoming_available_at_save_proceeds_when_classification_is_unchanged(self, monkeypatch):
        # Analyze runs offline (no object_info mock installed yet).
        analyze_result = await api.analyze_workflow(
            api.AnalyzeWorkflowRequest(workflow=_VAE_WORKFLOW), current_user=None
        )
        assert analyze_result["object_info_used"] is False

        # By save time a backend has become reachable, but it doesn't change
        # this workflow's classification (no combo/model-file candidate the
        # offline pass hadn't already produced some other way).
        _mock_object_info(monkeypatch, {})

        body = api.ImportWorkflowRequest(
            workflow=_VAE_WORKFLOW,
            form=None,
            history=[],
            model_family="RicherAtSaveTest",
            variant="imported",
            display_name="Richer At Save Test",
            schema_fingerprint=analyze_result["schema_fingerprint"],
            schema_object_info_used=analyze_result["object_info_used"],
        )

        response = await api.import_workflow(body, current_user=None)
        assert response["preset_id"]

    @pytest.mark.asyncio
    async def test_offline_path_with_no_backend_at_all_is_never_refused(self):
        """No `uses_object_info_mock` marker - the suite's own autouse fixture
        keeps `_try_object_info` returning `None` for both calls below,
        exactly the no-backend-configured path every other offline test in
        this suite exercises."""
        analyze_result = await api.analyze_workflow(
            api.AnalyzeWorkflowRequest(workflow=_HELPER_WORKFLOW), current_user=None
        )
        assert analyze_result["object_info_used"] is False
        form = raw_form_dict_for_roles(analyze_result, {"literal"})

        body = api.ImportWorkflowRequest(
            workflow=_HELPER_WORKFLOW,
            form=form,
            history=[],
            model_family="OfflineDriftTest",
            variant="imported",
            display_name="Offline Drift Test",
            schema_fingerprint=analyze_result["schema_fingerprint"],
            schema_object_info_used=analyze_result["object_info_used"],
        )

        response = await api.import_workflow(body, current_user=None)
        assert response["preset_id"]

    def test_emit_preset_directly_skips_the_check_with_no_expected_fingerprint(self, tmp_path):
        """A caller with no baseline (a hand-built script, a reload whose
        sidecar predates this feature) is unaffected - see
        `emit._check_schema_drift`'s docstring."""
        workflow = parse_api_workflow(_HELPER_WORKFLOW)
        result = emit_preset(
            workflow,
            ImportForm(tabs=[]),
            [],
            model_family="NoBaselineTest",
            variant="imported",
            display_name="No Baseline Test",
            dest_root=tmp_path,
        )
        assert result.preset_id
