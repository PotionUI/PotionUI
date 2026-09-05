"""ComfyUI's `/object_info` declares a combo input two equivalent ways: the
inline `[[opt1, opt2, ...], {...}]` form, and the named
`["COMBO", {"options": [...]}]` form (`comfy/comfy_types/node_typing.py`'s
own preferred spelling - used by, among others, the real API-node model
picker on `Krea2ImageNode.model`, see `object_info_krea2_real.json`).
`suggest._find_input_spec`/`_enrich_with_object_info` only ever recognized
the inline spelling; a named-spelling combo silently kept the generic
literal-field guess, skipping both the `select` option list and the
model-file inference `_looks_like_model_file_combo`/`_model_file_type_for_combo`
feed off a combo's real options - see `suggest._combo_options`, the shared
normalization both spellings now go through.
"""

import json
from pathlib import Path

import pytest
import yaml

from backend import api
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.schema import FieldItem, FieldMapping, FormTab, ImportForm
from backend.preset_import.suggest import _combo_options, suggest_fields

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _mock_object_info(monkeypatch, object_info):
    async def _fake_fetch_object_info(backend_id, base_url):
        return object_info

    monkeypatch.setattr(api, "_fetch_object_info", _fake_fetch_object_info)


@pytest.fixture(autouse=True)
def _imported_root(tmp_path, monkeypatch):
    root = tmp_path / "presets"
    monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", root)
    monkeypatch.setattr(api, "get_container", lambda: (_ for _ in ()).throw(AssertionError("no container configured")))
    return root


# A plain (non-model-file) combo, declared both ways, to prove the two
# spellings suggest identically.
_PLAIN_COMBO_WORKFLOW = {"1": {"class_type": "MyComboNode", "inputs": {"mode": "b"}}}
_PLAIN_COMBO_OBJECT_INFO_INLINE = {"MyComboNode": {"input": {"required": {"mode": [["a", "b", "c"]]}}, "output": []}}
_PLAIN_COMBO_OBJECT_INFO_NAMED = {
    "MyComboNode": {"input": {"required": {"mode": ["COMBO", {"options": ["a", "b", "c"]}]}}, "output": []}
}

# A noncatalog LoRA loader's model-file combo, declared both ways - `lora_name`
# is recognized by name alone (suggest._ENRICHMENT_MODEL_FILE_BY_INPUT_NAME).
_LORA_WORKFLOW = {"1": {"class_type": "MyCustomLoraLoader", "inputs": {"lora_name": "my_style.safetensors"}}}
_LORA_OBJECT_INFO_INLINE = {
    "MyCustomLoraLoader": {
        "input": {"required": {"lora_name": [["my_style.safetensors", "other.safetensors"]]}},
        "output": [],
    }
}
_LORA_OBJECT_INFO_NAMED = {
    "MyCustomLoraLoader": {
        "input": {"required": {"lora_name": ["COMBO", {"options": ["my_style.safetensors", "other.safetensors"]}]}},
        "output": [],
    }
}

# A combo on a CONNECTED input (a link, never a literal) - must never become
# a candidate at all, under either spelling.
_CONNECTED_COMBO_WORKFLOW = {
    "1": {"class_type": "SomeUpstreamNode", "inputs": {}},
    "2": {"class_type": "MyComboConsumer", "inputs": {"mode": ["1", 0]}},
}
_CONNECTED_COMBO_OBJECT_INFO = {
    "MyComboConsumer": {"input": {"required": {"mode": ["COMBO", {"options": ["a", "b"]}]}}, "output": []},
}


class TestKrea2RealNamedComboFixture:
    """Item (5)'s headline fixture: the retained real Krea2 object_info,
    whose `model` input is declared with the named spelling."""

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_krea2_model_combo_becomes_a_select_with_the_live_options(self, monkeypatch):
        object_info = _load("object_info_krea2_real.json")
        _mock_object_info(monkeypatch, object_info)
        body = api.AnalyzeWorkflowRequest(workflow=_load("krea2_real_api.json"))

        result = await api.analyze_workflow(body, current_user=None)

        model_candidate = next(c for c in result["candidates"] if c["node_id"] == "1" and c["input_name"] == "model")
        assert model_candidate["suggested_field_type"] == "select"
        assert model_candidate["suggested_config"] == {
            "options": ["krea 2 medium turbo", "krea 2 medium", "krea 2 large"]
        }
        # The workflow's own literal value survives verbatim - it isn't
        # coerced to match an option, nor replaced by the schema default or
        # the first option (it doesn't even case-match any of the three).
        assert model_candidate["current_value"] == "Krea 2 Medium"
        # Never mistaken for a filesystem model path - "model" isn't a
        # recognized model-file input name, and none of the three option
        # values end in a model-file extension.
        assert model_candidate["suggested_folder"] is None

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_bite_check_the_named_spelling_alone_is_what_makes_this_work(self, monkeypatch):
        """Confirms the fixture actually uses the named spelling, not the
        inline one `_enrich_with_object_info` already handled - reshaping it
        to the inline spelling must produce the exact same suggestion."""
        object_info = _load("object_info_krea2_real.json")
        assert object_info["Krea2ImageNode"]["input"]["required"]["model"][0] == "COMBO"
        reshaped = json.loads(json.dumps(object_info))
        options = reshaped["Krea2ImageNode"]["input"]["required"]["model"][1]["options"]
        reshaped["Krea2ImageNode"]["input"]["required"]["model"] = [options]

        workflow = parse_api_workflow(_load("krea2_real_api.json"))
        named_analysis = suggest_fields(workflow, object_info=object_info)
        inline_analysis = suggest_fields(workflow, object_info=reshaped)

        named_candidate = next(c for c in named_analysis.candidates if c.input_name == "model")
        inline_candidate = next(c for c in inline_analysis.candidates if c.input_name == "model")
        assert named_candidate.suggested_field_type == inline_candidate.suggested_field_type == "select"
        assert named_candidate.suggested_config == inline_candidate.suggested_config

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_the_suggested_select_saves_with_its_options_intact(self, monkeypatch):
        """Item (5)'s "designed/emitted field config": the wizard would offer
        this candidate's own suggested_field_type/suggested_config as the
        field's starting shape - confirms that shape actually reaches the
        written tab YAML unchanged when accepted as-is."""
        object_info = _load("object_info_krea2_real.json")
        _mock_object_info(monkeypatch, object_info)
        workflow_json = _load("krea2_real_api.json")

        form = ImportForm(
            tabs=[
                FormTab(
                    id="generation",
                    label="Generation",
                    items=[
                        FieldItem(
                            field_name="model_variant",
                            field_type="select",
                            label="Model",
                            default="Krea 2 Medium",
                            config={"options": ["krea 2 medium turbo", "krea 2 medium", "krea 2 large"]},
                            mappings=[FieldMapping(node_id="1", input_name="model", transform="none")],
                        )
                    ],
                )
            ]
        )

        body = api.ImportWorkflowRequest(
            workflow=workflow_json,
            form=form.model_dump(mode="json"),
            history=[],
            model_family="Krea2ComboTest",
            variant="imported",
            display_name="Krea2 Combo Test",
        )
        response = await api.import_workflow(body, current_user=None)

        tab_yaml = yaml.safe_load(
            (Path(response["path"]) / "modes" / response["mode"] / "tabs" / "generation.yml").read_text()
        )
        field = next(f for f in tab_yaml["fields"] if f["name"] == "model_variant")
        assert field["type"] == "select"
        assert field["configuration"]["options"] == ["krea 2 medium turbo", "krea 2 medium", "krea 2 large"]
        assert field["default"] == "Krea 2 Medium"


class TestPairedSpellingsAgree:
    def test_a_plain_combo_suggests_identically_under_both_spellings(self):
        workflow = parse_api_workflow(_PLAIN_COMBO_WORKFLOW)
        inline_analysis = suggest_fields(workflow, object_info=_PLAIN_COMBO_OBJECT_INFO_INLINE)
        named_analysis = suggest_fields(workflow, object_info=_PLAIN_COMBO_OBJECT_INFO_NAMED)

        inline_candidate = next(c for c in inline_analysis.candidates if c.input_name == "mode")
        named_candidate = next(c for c in named_analysis.candidates if c.input_name == "mode")
        assert inline_candidate.suggested_field_type == named_candidate.suggested_field_type == "select"
        assert inline_candidate.suggested_config == named_candidate.suggested_config == {"options": ["a", "b", "c"]}
        assert inline_candidate.current_value == named_candidate.current_value == "b"

    def test_a_model_file_combo_infers_the_same_model_type_under_both_spellings(self):
        workflow = parse_api_workflow(_LORA_WORKFLOW)
        inline_analysis = suggest_fields(workflow, object_info=_LORA_OBJECT_INFO_INLINE)
        named_analysis = suggest_fields(workflow, object_info=_LORA_OBJECT_INFO_NAMED)

        for analysis in (inline_analysis, named_analysis):
            candidate = next(c for c in analysis.candidates if c.input_name == "lora_name")
            assert candidate.suggested_field_type == "model"
            assert candidate.suggested_config == {"model_type": "lora", "allow_info_modal": True}
            assert candidate.suggested_transform == "strip_model_prefix"
            assert candidate.suggested_folder == "loras"
            assert candidate.current_value == "my_style.safetensors"


class TestConnectedComboInputNeverBecomesALiteralField:
    def test_a_combo_declared_input_that_is_actually_a_link_is_never_a_candidate(self):
        workflow = parse_api_workflow(_CONNECTED_COMBO_WORKFLOW)
        analysis = suggest_fields(workflow, object_info=_CONNECTED_COMBO_OBJECT_INFO)

        assert not any(c.node_id == "2" and c.input_name == "mode" for c in analysis.candidates)


class TestMalformedOrDynamicCombosStayLiteral:
    """Item (4): anything `_combo_options` can't safely resolve keeps the
    fully generic literal-field guess - no invented options, no endpoint
    fetch, no model-file inference either."""

    @staticmethod
    def _literal_candidate(input_name: str, value, spec):
        workflow_json = {"1": {"class_type": "MyWeirdNode", "inputs": {input_name: value}}}
        object_info = {"MyWeirdNode": {"input": {"required": {input_name: spec}}, "output": []}}
        workflow = parse_api_workflow(workflow_json)
        analysis = suggest_fields(workflow, object_info=object_info)
        return next(c for c in analysis.candidates if c.input_name == input_name)

    def test_options_not_a_list_stays_literal(self):
        candidate = self._literal_candidate("opt", "x", ["COMBO", {"options": "not-a-list"}])
        assert candidate.suggested_field_type == "textbox"
        assert candidate.suggested_config == {}

    def test_empty_options_stays_literal(self):
        candidate = self._literal_candidate("opt", "x", ["COMBO", {"options": []}])
        assert candidate.suggested_field_type == "textbox"
        assert candidate.suggested_config == {}

    def test_a_nonscalar_option_entry_stays_literal(self):
        candidate = self._literal_candidate("opt", "x", ["COMBO", {"options": ["a", {"nested": True}]}])
        assert candidate.suggested_field_type == "textbox"
        assert candidate.suggested_config == {}

    def test_named_combo_with_no_options_key_at_all_stays_literal(self):
        candidate = self._literal_candidate("opt", "x", ["COMBO", {}])
        assert candidate.suggested_field_type == "textbox"
        assert candidate.suggested_config == {}

    def test_a_remote_marker_with_no_options_stays_literal(self):
        candidate = self._literal_candidate("opt", "x", ["COMBO", {"remote": {"route": "/models"}}])
        assert candidate.suggested_field_type == "textbox"
        assert candidate.suggested_config == {}

    def test_a_remote_marker_even_alongside_a_real_options_list_stays_literal(self):
        """`remote` means ComfyUI's own UI treats this as dynamically
        populated regardless of whatever static `options` also happen to be
        present - never trusted as the complete list."""
        candidate = self._literal_candidate(
            "opt", "x", ["COMBO", {"remote": {"route": "/models"}, "options": ["a", "b"]}]
        )
        assert candidate.suggested_field_type == "textbox"
        assert candidate.suggested_config == {}

    def test_a_noncombo_string_type_is_unaffected_by_the_combo_check(self):
        """Regression guard: restructuring the if/elif chain around
        `_combo_options` must not disturb the plain STRING/multiline branch
        next to it."""
        candidate = self._literal_candidate("note", "hello", ["STRING", {"multiline": True}])
        assert candidate.suggested_field_type == "textbox"
        assert candidate.suggested_config == {"multiline": True}


class TestComboOptionsHelperDirectly:
    def test_preserves_numeric_option_types_and_order(self):
        resolved = _combo_options("COMBO", {"options": [1, 2, 4, 8]})
        assert resolved == [1, 2, 4, 8]
        assert all(isinstance(v, int) for v in resolved)

    def test_inline_spelling_still_works(self):
        assert _combo_options(["a", "b"], {}) == ["a", "b"]

    def test_not_a_combo_at_all_returns_none(self):
        assert _combo_options("INT", {"min": 0, "max": 10}) is None
