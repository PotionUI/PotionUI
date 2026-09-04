"""The form-designer contract (`backend/preset_import/schema.py`): payload
validation, every container kind round-tripping to a real field type,
transform/history-format value templates, `default_form`/`default_history`,
and one end-to-end emit -> lint -> render through a row + group + section.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path

import jinja2
import pytest
import yaml
from pydantic import ValidationError

from backend.preset_import.defaults import build_default_form, build_default_history
from backend.preset_import.emit import PresetEmitError, emit_preset
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.schema import (
    FieldItem,
    FieldMapping,
    FormTab,
    GroupItem,
    HeaderItem,
    HistoryEntry,
    ImportForm,
    LoraChainSelection,
    RowItem,
    SectionItem,
    parse_form,
    parse_history,
    validate_against_workflow,
)
from backend.preset_import.suggest import AnalyzeResult, InputCandidate, suggest_fields

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[5]


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _sdxl_workflow():
    return parse_api_workflow(_load("sdxl_basic_api.json"))


def _active_loras(value):
    """A plugin test may not import `src.platform.templating` (plugin code
    only ever reaches it through `src.plugin_api`, see
    `tests/architecture/test_layering.py`) - this is a test-local copy of
    `src.platform.templating.dict_utils.active_loras`'s "drop zero-strength
    entries" semantics, used only to render a template's own output for a
    bite-check, never to build one."""
    if not isinstance(value, list):
        return []
    kept = []
    for item in value:
        if not isinstance(item, dict) or "strength" not in item:
            kept.append(item)
            continue
        try:
            strength = float(item["strength"])
        except (TypeError, ValueError):
            kept.append(item)
            continue
        if strength != 0.0:
            kept.append(item)
    return kept


def _render(template: str, context: dict) -> str:
    """Render one of this importer's own Jinja templates with plain
    `jinja2` (never `src.platform.templating`, a plugin-forbidden import -
    see `_active_loras`) - good enough for a bite-check against a template
    string this test built itself, not a stand-in for the real render path."""
    env = jinja2.Environment()
    env.filters["active_loras"] = _active_loras
    return env.from_string(template).render(context)


# ----------------------------------------------------------------------
# Payload validation
# ----------------------------------------------------------------------


class TestPydanticShapeValidation:
    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValidationError):
            ImportForm.model_validate({"tabs": [{"id": "t", "label": "T", "items": [{"kind": "bogus"}]}]})

    def test_field_missing_required_key_is_rejected(self):
        with pytest.raises(ValidationError):
            ImportForm.model_validate(
                {"tabs": [{"id": "t", "label": "T", "items": [{"kind": "field", "field_name": "x"}]}]}
                # missing field_type/label
            )

    def test_row_columns_outside_2_3_4_is_rejected(self):
        with pytest.raises(ValidationError):
            ImportForm.model_validate(
                {"tabs": [{"id": "t", "label": "T", "items": [{"kind": "row", "columns": 5, "items": []}]}]}
            )

    def test_jinja_history_format_requires_a_template(self):
        with pytest.raises(ValidationError):
            HistoryEntry.model_validate({"field": "x", "label": "X", "format": "jinja"})

    def test_jinja_history_format_with_a_template_is_accepted(self):
        entry = HistoryEntry.model_validate({"field": "x", "label": "X", "format": "jinja", "template": "{{ 1 }}"})
        assert entry.template == "{{ 1 }}"

    def test_parse_form_wraps_pydantic_error_as_preset_emit_error(self):
        with pytest.raises(PresetEmitError):
            parse_form({"tabs": [{"id": "t", "label": "T", "items": [{"kind": "bogus"}]}]})

    def test_parse_form_with_no_tabs_defaults_to_one_generation_tab(self):
        form = parse_form(None)
        assert len(form.tabs) == 1
        assert form.tabs[0].id == "generation"
        assert form.tabs[0].label == "Generation"
        assert form.tabs[0].items == []

    def test_parse_history_wraps_pydantic_error(self):
        with pytest.raises(PresetEmitError):
            parse_history([{"field": "x", "label": "X", "format": "jinja"}])

    def test_tab_with_no_icon_is_forced_to_label_display(self):
        """`icon_display` defaults to "icon_only", but that's meaningless
        with no icon to show - a tab with none is always normalized to
        plain-label, whatever display mode the payload asked for."""
        tab = FormTab.model_validate({"id": "t", "label": "T", "icon_display": "icon_label"})
        assert tab.icon is None
        assert tab.icon_display == "label"

    def test_tab_with_an_icon_keeps_its_requested_display(self):
        tab = FormTab.model_validate({"id": "t", "label": "T", "icon": "lora", "icon_display": "icon_label"})
        assert tab.icon_display == "icon_label"

    def test_tab_icon_display_defaults_to_icon_only_when_icon_is_set(self):
        tab = FormTab.model_validate({"id": "t", "label": "T", "icon": "lora"})
        assert tab.icon_display == "icon_only"


class TestTypedDefaultCoercion:
    """A `FieldItem.default` must land as the native Python type its
    `field_type` requires (`src/features/presets/schema.py`'s
    `_validate_typed_default`, i.e. preset lint) - the wizard's own
    default-editing input hands back plain text, so a re-typed default
    would otherwise survive as a string. See `schema._typed_default`."""

    def _field(self, field_type, default):
        return FieldItem.model_validate(
            {"field_name": "x", "field_type": field_type, "label": "X", "default": default}
        )

    @pytest.mark.parametrize("field_type", ["integer", "stepper", "seed"])
    def test_numeric_string_becomes_int(self, field_type):
        field = self._field(field_type, "4")
        assert field.default == 4
        assert isinstance(field.default, int)

    @pytest.mark.parametrize("field_type", ["integer", "stepper", "seed"])
    def test_whole_float_becomes_int(self, field_type):
        field = self._field(field_type, 4.0)
        assert field.default == 4
        assert isinstance(field.default, int)

    @pytest.mark.parametrize("field_type", ["integer", "stepper", "seed"])
    def test_fractional_value_is_rejected(self, field_type):
        with pytest.raises(ValidationError):
            self._field(field_type, 4.5)

    @pytest.mark.parametrize("field_type", ["integer", "stepper", "seed"])
    def test_unparsable_string_is_rejected(self, field_type):
        with pytest.raises(ValidationError):
            self._field(field_type, "not-a-number")

    @pytest.mark.parametrize("field_type", ["number", "slider"])
    def test_numeric_string_becomes_int_or_float(self, field_type):
        int_field = self._field(field_type, "4")
        assert int_field.default == 4
        assert isinstance(int_field.default, int)
        float_field = self._field(field_type, "4.5")
        assert float_field.default == 4.5
        assert isinstance(float_field.default, float)

    @pytest.mark.parametrize("field_type", ["checkbox", "boolean", "gate"])
    def test_boolean_strings_are_coerced(self, field_type):
        assert self._field(field_type, "true").default is True
        assert self._field(field_type, "0").default is False

    @pytest.mark.parametrize("field_type", ["checkbox", "boolean", "gate"])
    def test_unrecognized_boolean_string_is_rejected(self, field_type):
        with pytest.raises(ValidationError):
            self._field(field_type, "maybe")

    def test_none_default_stays_none(self):
        assert self._field("integer", None).default is None

    def test_non_numeric_field_type_is_left_alone(self):
        assert self._field("string", "hello").default == "hello"


class TestWorkflowLevelValidation:
    """The checks pydantic alone can't make: a field's mappings actually
    exist on the workflow it's imported against."""

    def test_field_with_no_mappings_is_rejected(self):
        workflow = _sdxl_workflow()
        form = ImportForm(
            tabs=[FormTab(id="t", label="T", items=[FieldItem(field_name="x", field_type="slider", label="X", mappings=[])])]
        )
        with pytest.raises(PresetEmitError, match="no mappings"):
            validate_against_workflow(form, [], workflow)

    def test_mapping_to_an_unknown_node_is_rejected(self):
        workflow = _sdxl_workflow()
        form = ImportForm(
            tabs=[
                FormTab(
                    id="t", label="T",
                    items=[
                        FieldItem(
                            field_name="x", field_type="slider", label="X",
                            mappings=[FieldMapping(node_id="999", input_name="steps")],
                        )
                    ],
                )
            ]
        )
        with pytest.raises(PresetEmitError, match="unknown node"):
            validate_against_workflow(form, [], workflow)

    def test_mapping_to_an_unknown_input_is_rejected_with_object_info(self):
        workflow = _sdxl_workflow()
        object_info = _load("object_info_sdxl.json")
        form = ImportForm(
            tabs=[
                FormTab(
                    id="t", label="T",
                    items=[
                        FieldItem(
                            field_name="x", field_type="slider", label="X",
                            # KSampler ("3") has no "not_a_real_input"
                            mappings=[FieldMapping(node_id="3", input_name="not_a_real_input")],
                        )
                    ],
                )
            ]
        )
        with pytest.raises(PresetEmitError, match="has no input"):
            validate_against_workflow(form, [], workflow, object_info=object_info)

    def test_mapping_to_a_real_input_passes_with_object_info(self):
        workflow = _sdxl_workflow()
        object_info = _load("object_info_sdxl.json")
        form = ImportForm(
            tabs=[
                FormTab(
                    id="t", label="T",
                    items=[
                        FieldItem(
                            field_name="x", field_type="slider", label="X",
                            mappings=[FieldMapping(node_id="3", input_name="steps")],
                        )
                    ],
                )
            ]
        )
        validate_against_workflow(form, [], workflow, object_info=object_info)  # must not raise

    def test_mapping_without_object_info_accepts_any_input_the_node_already_has(self):
        """No live server to ask (a plain Export (API) import): a mapping to
        any input key the node's own JSON already carries is accepted -
        there is no schema to check it against."""
        workflow = _sdxl_workflow()
        form = ImportForm(
            tabs=[
                FormTab(
                    id="t", label="T",
                    items=[
                        FieldItem(
                            field_name="x", field_type="slider", label="X",
                            mappings=[FieldMapping(node_id="3", input_name="steps")],
                        )
                    ],
                )
            ]
        )
        validate_against_workflow(form, [], workflow)  # must not raise

    def test_lora_picker_is_exempt_from_the_no_mappings_rule(self):
        workflow = _sdxl_workflow()
        form = ImportForm(
            tabs=[
                FormTab(
                    id="t", label="T",
                    items=[FieldItem(field_name="loras", field_type="lora_picker", label="LoRAs", mappings=[])],
                )
            ]
        )
        validate_against_workflow(form, [], workflow)  # must not raise

    def test_history_entry_naming_an_unknown_field_is_rejected(self):
        workflow = _sdxl_workflow()
        form = ImportForm(tabs=[FormTab(id="t", label="T", items=[])])
        history = [HistoryEntry(field="nope", label="Nope")]
        with pytest.raises(PresetEmitError, match="no such form field"):
            validate_against_workflow(form, history, workflow)

    def test_history_entry_naming_a_foundational_field_is_accepted(self):
        """seed/quantity are real `form.*` values at render time even though
        they are never `Item`s the admin arranges (see emit.py)."""
        workflow = _sdxl_workflow()
        form = ImportForm(tabs=[FormTab(id="t", label="T", items=[])])
        history = [HistoryEntry(field="quantity", label="Batch Size", format="number")]
        validate_against_workflow(form, history, workflow)  # must not raise

    def test_multiple_problems_are_all_reported_together(self):
        workflow = _sdxl_workflow()
        form = ImportForm(
            tabs=[
                FormTab(
                    id="t", label="T",
                    items=[FieldItem(field_name="x", field_type="slider", label="X", mappings=[])],
                )
            ]
        )
        history = [HistoryEntry(field="nope", label="Nope")]
        with pytest.raises(PresetEmitError) as exc_info:
            validate_against_workflow(form, history, workflow)
        assert "no mappings" in str(exc_info.value)
        assert "no such form field" in str(exc_info.value)


# ----------------------------------------------------------------------
# LoraChainSelection (replaced/kept partition of a detected LoRA chain).
# ----------------------------------------------------------------------


class TestLoraChainSelectionValidation:
    def _lora_chain_form(self, lora_chain=None):
        return ImportForm(
            tabs=[
                FormTab(
                    id="t", label="T",
                    items=[FieldItem(field_name="loras", field_type="lora_picker", label="LoRAs", mappings=[])],
                )
            ],
            lora_chain=lora_chain,
        )

    def test_no_selection_at_all_is_accepted(self):
        """A hand-built form (or a preset imported before this selection
        existed) simply never sets `lora_chain` - the emitter's own fallback
        treats that as "replace everything", so validation has nothing to
        check here."""
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        form = self._lora_chain_form(lora_chain=None)
        validate_against_workflow(form, [], workflow)  # must not raise

    def test_full_partition_is_accepted(self):

        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        form = self._lora_chain_form(
            LoraChainSelection(replaced_node_ids=["102"], kept_node_ids=["101"])
        )
        validate_against_workflow(form, [], workflow)  # must not raise

    def test_a_node_missing_from_both_lists_is_rejected(self):

        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        form = self._lora_chain_form(
            LoraChainSelection(replaced_node_ids=["102"], kept_node_ids=[])
        )
        with pytest.raises(PresetEmitError, match="missing chain node"):
            validate_against_workflow(form, [], workflow)

    def test_a_node_in_both_lists_is_rejected(self):

        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        form = self._lora_chain_form(
            LoraChainSelection(replaced_node_ids=["101", "102"], kept_node_ids=["102"])
        )
        with pytest.raises(PresetEmitError, match="both replaced and kept"):
            validate_against_workflow(form, [], workflow)

    def test_an_unknown_node_id_is_rejected(self):

        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        form = self._lora_chain_form(
            LoraChainSelection(replaced_node_ids=["101", "102", "999"], kept_node_ids=[])
        )
        with pytest.raises(PresetEmitError, match="not in the detected chain"):
            validate_against_workflow(form, [], workflow)

    def test_selection_given_but_no_chain_detected_is_rejected(self):

        workflow = _sdxl_workflow()
        form = self._lora_chain_form(
            LoraChainSelection(replaced_node_ids=["1"], kept_node_ids=[])
        )
        with pytest.raises(PresetEmitError, match="no detected LoRA chain"):
            validate_against_workflow(form, [], workflow)

    def test_empty_selection_with_no_chain_detected_is_accepted(self):
        """A `lora_picker` field on a workflow with no LoRA chain at all
        legitimately carries an empty selection (`emit.emit_preset` splices
        it onto the sampling cluster's own model-chain boundary instead -
        see `suggest.ModelChainInfo`), unlike the non-empty selection
        above, which still has nothing to refer to."""
        workflow = _sdxl_workflow()
        form = self._lora_chain_form(
            LoraChainSelection(replaced_node_ids=[], kept_node_ids=[])
        )
        validate_against_workflow(form, [], workflow)  # must not raise

    def _three_node_chain_workflow(self):
        """4 (checkpoint) -> 101 -> 102 -> 103 -> 3 (sampler), all
        `LoraLoaderModelOnly` - a plain three-node chain for the
        kept-sandwiched-between-replaced check below."""
        return parse_api_workflow(
            {
                "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxlBase_v10.safetensors"}},
                "101": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "a.safetensors", "strength_model": 1.0, "model": ["4", 0]}},
                "102": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "b.safetensors", "strength_model": 1.0, "model": ["101", 0]}},
                "103": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "c.safetensors", "strength_model": 1.0, "model": ["102", 0]}},
                "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["4", 1]}},
                "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["4", 1]}},
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
                "3": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": 1, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal",
                        "denoise": 1.0, "model": ["103", 0], "positive": ["6", 0], "negative": ["7", 0],
                        "latent_image": ["5", 0],
                    },
                },
            }
        )

    def test_a_kept_node_between_two_replaced_nodes_is_rejected(self):
        """102 is kept fixed while 101 and 103, on either side of it in
        chain order, are both replaced - the shape a flat picker loop can't
        represent (see `_find_sandwiched_kept_nodes`'s docstring)."""
        workflow = self._three_node_chain_workflow()
        form = self._lora_chain_form(
            LoraChainSelection(replaced_node_ids=["101", "103"], kept_node_ids=["102"])
        )
        with pytest.raises(PresetEmitError, match="kept LoRA node 102 sits between replaced nodes 101 and 103"):
            validate_against_workflow(form, [], workflow)

    def test_bite_check_a_kept_node_at_either_end_is_not_sandwiched(self):
        """Confirms the assertion above is really about being IN BETWEEN,
        not merely coexisting with replaced nodes: keeping 101 (the source
        end) fixed while replacing 102 and 103 is a perfectly ordinary,
        already-tested shape and must not raise."""
        workflow = self._three_node_chain_workflow()
        form = self._lora_chain_form(
            LoraChainSelection(replaced_node_ids=["102", "103"], kept_node_ids=["101"])
        )
        validate_against_workflow(form, [], workflow)  # must not raise


# ----------------------------------------------------------------------
# Prompts are never a choosable form field, even with no sampler to find
# them structurally through (maintainer: "of course the positive/negative
# prompt should come from the Prompts section, not the dynamic form").
# ----------------------------------------------------------------------


class TestPromptsAreNeverFormFields:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def _krea2_workflow_and_object_info(self):
        object_info = _load("object_info_krea2_real.json")
        workflow = parse_api_workflow(_load("krea2_real_api.json"))
        return workflow, object_info

    def test_krea2_prompt_input_is_caught_by_the_name_based_fallback(self):
        """No KSampler on this all-in-one node for structural detection to
        key off - `prompt` must still come back with a prompt role, not
        `literal`, so the wizard's left pane locks it."""
        workflow, object_info = self._krea2_workflow_and_object_info()
        analysis = suggest_fields(workflow, object_info=object_info)
        assert analysis.sampler_node_id is None
        prompt_candidate = next(c for c in analysis.candidates if c.node_id == "1" and c.input_name == "prompt")
        assert prompt_candidate.role == "prompt_positive"
        assert prompt_candidate.obvious is True

    def test_structural_detection_still_wins_when_a_real_sampler_exists(self):
        """Bite check the other way: a workflow WITH a KSampler must keep
        using the structural (conditioning-link) detection, never the
        name-based fallback - confirms the fallback only ever fires when
        structural detection found nothing at all."""
        workflow = _sdxl_workflow()
        analysis = suggest_fields(workflow)
        assert analysis.sampler_node_id is not None
        positive = next(c for c in analysis.candidates if c.role == "prompt_positive")
        assert positive.node_id == "6"  # the CLIPTextEncode wired to KSampler's "positive"

    def test_krea2_prompt_never_lands_in_default_form_or_history(self):
        workflow, object_info = self._krea2_workflow_and_object_info()
        analysis = suggest_fields(workflow, object_info=object_info)
        form = build_default_form(analysis)
        history = build_default_history(form)

        all_field_names = {f.field_name for tab in form.tabs for f in tab.items if f.kind == "field"}
        assert "prompt" not in all_field_names
        assert form.tabs == [
            FormTab(id="generation", label="Generation", icon="generation", icon_display="icon_only", items=[])
        ]
        assert history == []

    def test_ltx25_real_export_also_has_no_prompt_role_field_in_default_form(self):
        object_info = _load("object_info_ltx25_img2img_real.json")
        workflow = parse_api_workflow(_load("ltx25_img2img_real_api.json"))
        analysis = suggest_fields(workflow, object_info=object_info)
        form = build_default_form(analysis)

        prompt_role_node_inputs = {
            (c.node_id, c.input_name) for c in analysis.candidates if c.role in ("prompt_positive", "prompt_negative")
        }
        for tab in form.tabs:
            for item in tab.items:
                if item.kind != "field":
                    continue
                for mapping in item.mappings:
                    assert (mapping.node_id, mapping.input_name) not in prompt_role_node_inputs

    def test_mapping_a_field_to_the_fallback_prompt_input_is_rejected(self):
        workflow, object_info = self._krea2_workflow_and_object_info()
        form = ImportForm(
            tabs=[
                FormTab(
                    id="generation", label="Generation",
                    items=[
                        FieldItem(
                            field_name="sneaky_prompt", field_type="textbox", label="Sneaky",
                            mappings=[FieldMapping(node_id="1", input_name="prompt")],
                        )
                    ],
                )
            ]
        )
        with pytest.raises(PresetEmitError, match="prompts come from the Prompts section"):
            validate_against_workflow(form, [], workflow, object_info=object_info)

    def test_mapping_to_a_structurally_detected_prompt_is_also_rejected(self):
        """The same rule applies to a normal KSampler-based workflow, not
        just the fallback path - either way an admin cannot bypass the
        Prompts section by mapping some other field onto the prompt node."""
        workflow = _sdxl_workflow()
        form = ImportForm(
            tabs=[
                FormTab(
                    id="generation", label="Generation",
                    items=[
                        FieldItem(
                            field_name="sneaky_prompt", field_type="textbox", label="Sneaky",
                            mappings=[FieldMapping(node_id="6", input_name="text")],
                        )
                    ],
                )
            ]
        )
        with pytest.raises(PresetEmitError, match="prompts come from the Prompts section"):
            validate_against_workflow(form, [], workflow)

    def test_prompt_rejection_is_reported_alongside_other_problems(self):
        workflow = _sdxl_workflow()
        form = ImportForm(
            tabs=[
                FormTab(
                    id="generation", label="Generation",
                    items=[
                        FieldItem(
                            field_name="sneaky_prompt", field_type="textbox", label="Sneaky",
                            mappings=[FieldMapping(node_id="6", input_name="text")],
                        ),
                        FieldItem(field_name="empty", field_type="textbox", label="Empty", mappings=[]),
                    ],
                )
            ]
        )
        with pytest.raises(PresetEmitError) as exc_info:
            validate_against_workflow(form, [], workflow)
        assert "prompts come from the Prompts section" in str(exc_info.value)
        assert "no mappings" in str(exc_info.value)

    def test_emitted_krea2_preset_still_binds_the_prompt_from_generation_prompts(self, dest_root):
        """Even with zero form fields at all, the foundational prompt wiring
        (from suggest_fields' fallback-detected candidate) still binds
        `1.inputs.prompt` to `generation.prompts.first.positive` - prompts
        are never opt-in."""
        workflow, object_info = self._krea2_workflow_and_object_info()
        analysis = suggest_fields(workflow, object_info=object_info)
        form = build_default_form(analysis)
        history = build_default_history(form)

        result = emit_preset(
            workflow, form, history, model_family="Krea2PromptWiring", variant="v1", display_name="X",
            dest_root=dest_root, object_info=object_info,
        )
        pipeline = yaml.safe_load((result.preset_dir / "modes" / result.mode / "pipeline.yml").read_text())
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        field_mappings = comfyui_pipe["configuration"]["field_mappings"]
        assert field_mappings == [["{{ generation.prompts.first.positive }}", "1.inputs.prompt", "str"]]

    def test_text_input_promotes_to_positive_when_paired_with_a_negative_sibling(self):
        """The "text" fallback name is ambiguous on its own (a select/combo
        can also be a string) - paired with a sibling "negative*" input, or
        being the only string input at all, is what disambiguates it."""
        workflow = parse_api_workflow(
            {
                "1": {
                    "class_type": "SomeAllInOneNode",
                    "inputs": {"text": "a positive prompt", "negative_text": "ugly", "seed": 1},
                }
            }
        )
        analysis = suggest_fields(workflow)
        by_input = {c.input_name: c.role for c in analysis.candidates if c.node_id == "1"}
        assert by_input["text"] == "prompt_positive"

    def test_text_input_promotes_when_it_is_the_only_string_input(self):
        workflow = parse_api_workflow(
            {"1": {"class_type": "SomeAllInOneNode", "inputs": {"text": "a positive prompt", "seed": 1}}}
        )
        analysis = suggest_fields(workflow)
        by_input = {c.input_name: c.role for c in analysis.candidates if c.node_id == "1"}
        assert by_input["text"] == "prompt_positive"

    def test_bite_check_text_input_does_not_promote_alongside_an_unrelated_string(self):
        """Confirms the disambiguation actually matters: with no negative
        sibling AND a second unrelated string input (a model-name combo,
        say), "text" is genuinely ambiguous and stays a plain literal."""
        workflow = parse_api_workflow(
            {
                "1": {
                    "class_type": "SomeAllInOneNode",
                    "inputs": {"text": "a positive prompt", "model_name": "some-model", "seed": 1},
                }
            }
        )
        analysis = suggest_fields(workflow)
        by_input = {c.input_name: c.role for c in analysis.candidates if c.node_id == "1"}
        assert by_input["text"] == "literal"


# ----------------------------------------------------------------------
# Container round-trips
# ----------------------------------------------------------------------


class TestContainerKindsRoundTrip:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def _emit(self, dest_root, items, *, family="ContainerTest"):
        workflow = _sdxl_workflow()
        form = ImportForm(tabs=[FormTab(id="generation", label="Generation", items=items)])
        return emit_preset(workflow, form, [], model_family=family, variant="v1", display_name="X", dest_root=dest_root)

    def _checkpoint_field(self, name="checkpoint") -> FieldItem:
        return FieldItem(
            field_name=name, field_type="model", label="Checkpoint", config={"model_type": "checkpoint"},
            mappings=[FieldMapping(node_id="4", input_name="ckpt_name", transform="strip_model_prefix")],
        )

    def _steps_field(self, name="steps") -> FieldItem:
        return FieldItem(
            field_name=name, field_type="slider", label="Steps", default=20,
            mappings=[FieldMapping(node_id="3", input_name="steps")],
        )

    def _cfg_field(self, name="cfg") -> FieldItem:
        return FieldItem(
            field_name=name, field_type="slider", label="CFG", default=7.0,
            mappings=[FieldMapping(node_id="3", input_name="cfg")],
        )

    def test_row_becomes_a_row_field_with_columns(self, dest_root):
        result = self._emit(dest_root, [RowItem(columns=3, items=[self._steps_field(), self._cfg_field()])])
        fields = yaml.safe_load(
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )["fields"]
        row = next(f for f in fields if f["type"] == "row")
        assert row["configuration"]["columns"] == 3
        assert {c["name"] for c in row["children"]} == {"steps", "cfg"}

    def test_group_becomes_a_group_field_with_a_label(self, dest_root):
        result = self._emit(dest_root, [GroupItem(title="Models", items=[self._checkpoint_field()])])
        fields = yaml.safe_load(
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )["fields"]
        group = next(f for f in fields if f["type"] == "group")
        assert group["label"] == "Models"
        assert group["children"][0]["name"] == "checkpoint"

    def test_section_becomes_a_section_with_collapsed_config(self, dest_root):
        result = self._emit(dest_root, [SectionItem(title="Advanced", collapsed=True, items=[self._steps_field()])])
        fields = yaml.safe_load(
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )["fields"]
        section = next(f for f in fields if f["type"] == "section")
        assert section["label"] == "Advanced"
        assert section["configuration"]["collapsed"] is True
        assert section["children"][0]["name"] == "steps"

    def test_header_becomes_a_header_field_with_no_value(self, dest_root):
        result = self._emit(dest_root, [HeaderItem(text="Sampling")])
        fields = yaml.safe_load(
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )["fields"]
        header = next(f for f in fields if f["type"] == "header")
        assert header["label"] == "Sampling"
        assert "name" not in header

    def test_nesting_a_row_inside_a_group_inside_a_section(self, dest_root):
        nested = SectionItem(
            title="Advanced",
            items=[GroupItem(title="Sampling", items=[RowItem(columns=2, items=[self._steps_field(), self._cfg_field()])])],
        )
        result = self._emit(dest_root, [nested])
        fields = yaml.safe_load(
            (result.preset_dir / "modes" / result.mode / "tabs" / "generation.yml").read_text()
        )["fields"]
        section = next(f for f in fields if f["type"] == "section")
        group = section["children"][0]
        row = group["children"][0]
        assert section["type"] == "section" and group["type"] == "group" and row["type"] == "row"
        assert {c["name"] for c in row["children"]} == {"steps", "cfg"}


# ----------------------------------------------------------------------
# Transform bite-checks
# ----------------------------------------------------------------------


class TestTransforms:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def _field_mappings(self, result) -> list:
        pipeline = yaml.safe_load((result.preset_dir / "modes" / result.mode / "pipeline.yml").read_text())
        comfyui_pipe = next(p for p in pipeline["pipeline"] if p["name"] == "comfyui")
        return comfyui_pipe["configuration"]["field_mappings"]

    def test_strip_model_prefix_uses_the_known_model_type_prefix(self, dest_root):
        workflow = _sdxl_workflow()
        field = FieldItem(
            field_name="checkpoint", field_type="model", label="Checkpoint", config={"model_type": "checkpoint"},
            mappings=[FieldMapping(node_id="4", input_name="ckpt_name", transform="strip_model_prefix")],
        )
        form = ImportForm(tabs=[FormTab(id="generation", label="Generation", items=[field])])
        result = emit_preset(workflow, form, [], model_family="StripKnown", variant="v1", display_name="X", dest_root=dest_root)
        entry = next(m for m in self._field_mappings(result) if m[1] == "4.inputs.ckpt_name")
        assert "models/checkpoints/" in entry[0]
        assert entry[2] == "str"

    def test_bite_check_strip_model_prefix_actually_strips_at_render_time(self, dest_root):
        """Confirms the transform really does something (not just present in
        the template text): rendering it against a form value proves the
        prefix disappears."""
        workflow = _sdxl_workflow()
        field = FieldItem(
            field_name="checkpoint", field_type="model", label="Checkpoint", config={"model_type": "checkpoint"},
            mappings=[FieldMapping(node_id="4", input_name="ckpt_name", transform="strip_model_prefix")],
        )
        form = ImportForm(tabs=[FormTab(id="generation", label="Generation", items=[field])])
        result = emit_preset(workflow, form, [], model_family="StripBite", variant="v1", display_name="X", dest_root=dest_root)
        template = next(m for m in self._field_mappings(result) if m[1] == "4.inputs.ckpt_name")[0]
        rendered = _render(template, {"form": {"checkpoint": "models/checkpoints/foo.safetensors"}})
        assert rendered == "foo.safetensors"

    def test_strip_model_prefix_falls_back_to_every_known_prefix_without_model_type(self, dest_root):
        workflow = _sdxl_workflow()
        field = FieldItem(
            field_name="checkpoint", field_type="model", label="Checkpoint",
            mappings=[FieldMapping(node_id="4", input_name="ckpt_name", transform="strip_model_prefix")],
        )
        form = ImportForm(tabs=[FormTab(id="generation", label="Generation", items=[field])])
        result = emit_preset(workflow, form, [], model_family="StripFallback", variant="v1", display_name="X", dest_root=dest_root)
        template = next(m for m in self._field_mappings(result) if m[1] == "4.inputs.ckpt_name")[0]
        rendered = _render(template, {"form": {"checkpoint": "models/checkpoints/foo.safetensors"}})
        assert rendered == "foo.safetensors"

    def test_split_wh_transforms_produce_int_cast_width_height_mappings(self, dest_root):
        workflow = _sdxl_workflow()
        field = FieldItem(
            field_name="resolution", field_type="resolution", label="Resolution", default="832x1216",
            mappings=[
                FieldMapping(node_id="5", input_name="width", transform="split_wh_width"),
                FieldMapping(node_id="5", input_name="height", transform="split_wh_height"),
            ],
        )
        form = ImportForm(tabs=[FormTab(id="generation", label="Generation", items=[field])])
        result = emit_preset(workflow, form, [], model_family="SplitWH", variant="v1", display_name="X", dest_root=dest_root)
        mappings = self._field_mappings(result)
        width_entry = next(m for m in mappings if m[1] == "5.inputs.width")
        height_entry = next(m for m in mappings if m[1] == "5.inputs.height")
        assert width_entry[2] == height_entry[2] == "int"
        assert _render(width_entry[0], {"form": {"resolution": "512x768"}}) == "512"
        assert _render(height_entry[0], {"form": {"resolution": "512x768"}}) == "768"

    def test_seed_transform_wires_the_at_seed_sentinel_regardless_of_field_value(self, dest_root):
        workflow = _sdxl_workflow()
        field = FieldItem(
            field_name="extra_seed", field_type="seed", label="Extra Seed", default=-1,
            mappings=[FieldMapping(node_id="3", input_name="seed", transform="seed")],
        )
        form = ImportForm(tabs=[FormTab(id="generation", label="Generation", items=[field])])
        result = emit_preset(workflow, form, [], model_family="SeedTransform", variant="v1", display_name="X", dest_root=dest_root)
        entry = next(m for m in self._field_mappings(result) if m[1] == "3.inputs.seed")
        assert entry == ["@seed", "3.inputs.seed", "int"]

    def test_image_field_type_gets_the_image_cast_regardless_of_transform(self, dest_root):
        workflow = parse_api_workflow(_load("lora_chain_img2img_api.json"))
        field = FieldItem(
            field_name="source_image", field_type="image", label="Source Image",
            mappings=[FieldMapping(node_id="10", input_name="image")],
        )
        form = ImportForm(tabs=[FormTab(id="generation", label="Generation", items=[field])])
        result = emit_preset(workflow, form, [], model_family="ImageCast", variant="v1", display_name="X", dest_root=dest_root)
        entry = next(m for m in self._field_mappings(result) if m[1] == "10.inputs.image")
        assert entry[2] == "image"


# ----------------------------------------------------------------------
# History format bite-checks
# ----------------------------------------------------------------------


class TestHistoryFormats:
    @pytest.fixture()
    def dest_root(self, tmp_path):
        return tmp_path / "presets"

    def _steps_form_and_emit(self, dest_root, history):
        workflow = _sdxl_workflow()
        field = FieldItem(
            field_name="steps", field_type="slider", label="Steps", default=20,
            mappings=[FieldMapping(node_id="3", input_name="steps")],
        )
        form = ImportForm(tabs=[FormTab(id="generation", label="Generation", items=[field])])
        return emit_preset(
            workflow, form, history, model_family=f"Hist{uuid.uuid4().hex[:8]}", variant="v1",
            display_name="X", dest_root=dest_root,
        )

    def _param_emitter_parameters(self, result) -> list:
        pipeline = yaml.safe_load((result.preset_dir / "modes" / result.mode / "pipeline.yml").read_text())
        pe = next(p for p in pipeline["pipeline"] if p["name"] == "param_emitter")
        return pe["configuration"]["parameters"]

    def test_history_order_matches_param_emitter_order(self, dest_root):
        history = [
            HistoryEntry(field="steps", label="Steps A", format="as_is"),
            HistoryEntry(field="steps", label="Steps B", format="number"),
        ]
        result = self._steps_form_and_emit(dest_root, history)
        params = self._param_emitter_parameters(result)
        # positive_prompt/negative_prompt are foundational and always come first.
        assert [p[0] for p in params] == ["positive_prompt", "negative_prompt", "steps", "steps"]

    def test_as_is_and_number_are_the_bare_form_value(self, dest_root):
        result = self._steps_form_and_emit(dest_root, [HistoryEntry(field="steps", label="Steps", format="as_is")])
        params = self._param_emitter_parameters(result)
        assert params[-1] == ["steps", "{{ form.steps }}"]

    def test_model_name_strips_the_path_to_the_basename(self, dest_root):
        result = self._steps_form_and_emit(dest_root, [HistoryEntry(field="steps", label="Steps", format="model_name")])
        template = self._param_emitter_parameters(result)[-1][1]
        rendered = _render(template, {"form": {"steps": "models/checkpoints/foo.safetensors"}})
        assert rendered == "foo.safetensors"

    def test_jinja_format_uses_the_template_verbatim(self, dest_root):
        result = self._steps_form_and_emit(
            dest_root, [HistoryEntry(field="steps", label="Steps", format="jinja", template="{{ form.steps * 2 }}")]
        )
        params = self._param_emitter_parameters(result)
        assert params[-1] == ["steps", "{{ form.steps * 2 }}"]

    def test_list_format_reports_active_count_and_names(self, dest_root):
        result = self._steps_form_and_emit(dest_root, [HistoryEntry(field="steps", label="Steps", format="list")])
        template = self._param_emitter_parameters(result)[-1][1]
        loras = [
            {"model": "models/loras/a.safetensors", "strength": 1.0},
            {"model": "models/loras/b.safetensors", "strength": 0.0},  # inactive - dropped
        ]
        rendered = _render(template, {"form": {"steps": loras}})
        assert rendered == "1x a.safetensors"

    def test_wxh_format_passes_the_already_combined_string_through(self, dest_root):
        result = self._steps_form_and_emit(dest_root, [HistoryEntry(field="steps", label="Steps", format="wxh")])
        params = self._param_emitter_parameters(result)
        assert params[-1] == ["steps", "{{ form.steps }}"]


# ----------------------------------------------------------------------
# default_form / default_history
# ----------------------------------------------------------------------


class TestDefaultFormAndHistory:
    def test_sdxl_default_form_has_resolution_models_steps_cfg_in_one_tab(self):
        analysis = suggest_fields(_sdxl_workflow())
        form = build_default_form(analysis)
        history = build_default_history(form, analysis)

        assert [t.id for t in form.tabs] == ["generation"]
        # The leading tab gets a "generation" icon (rendered icon_only) so it
        # isn't left blank in the emitted preset's tab strip.
        assert form.tabs[0].icon == "generation"
        assert form.tabs[0].icon_display == "icon_only"
        items = form.tabs[0].items
        assert items[0].kind == "field" and items[0].field_name == "resolution"
        assert len(items[0].mappings) == 2
        assert {m.transform for m in items[0].mappings} == {"split_wh_width", "split_wh_height"}
        sections = {i.title: i for i in items if i.kind == "section"}
        assert sections.keys() == {"Models", "Sampling"}
        assert [f.field_name for f in sections["Models"].items] == ["checkpoint"]
        assert {f.field_name for f in sections["Sampling"].items} == {
            "steps", "cfg", "sampler_name", "scheduler", "denoise",
        }
        assert {i.field_name for i in items if i.kind == "field"} == {"resolution"}
        assert {h.field for h in history} == {
            "resolution", "checkpoint", "steps", "cfg", "sampler_name", "scheduler", "denoise",
        }
        assert next(h for h in history if h.field == "resolution").format == "wxh"
        assert next(h for h in history if h.field == "checkpoint").format == "model_name"

    def test_flux_subgraph_default_form_sections_three_model_loaders(self):
        workflow = parse_api_workflow(_load("flux_subgraph_api.json"))
        analysis = suggest_fields(workflow)
        form = build_default_form(analysis)
        models_section = next(i for i in form.tabs[0].items if i.kind == "section")
        assert {f.field_name for f in models_section.items} == {"diffusion_model", "clip", "vae"}
        # Subgraph node ids survive into the mappings, same as field_mappings.
        assert any(m.node_id.startswith("92:") for f in models_section.items for m in f.mappings)

    def test_custom_sampling_default_form_has_models_and_sampling_sections(self):
        workflow = parse_api_workflow(_load("flux_custom_sampling_api.json"))
        analysis = suggest_fields(workflow)
        form = build_default_form(analysis)

        items = form.tabs[0].items
        sections = {i.title: i for i in items if i.kind == "section"}
        assert sections.keys() == {"Models", "Sampling"}
        assert {f.field_name for f in sections["Models"].items} == {"diffusion_model", "clip", "clip_2", "vae"}
        assert {f.field_name for f in sections["Sampling"].items} == {
            "steps", "sampler_name", "scheduler", "denoise", "guidance", "shift", "base_shift",
        }
        # "Models" comes before "Sampling" - loaders are enumerated before
        # the sampling cluster in suggest_fields for exactly this reason.
        assert [i.title for i in items if i.kind == "section"] == ["Models", "Sampling"]
        # lora_slot never becomes a default-form field (see the module
        # docstring); the detected chain stays individually mappable.
        assert not any(getattr(f, "field_type", None) == "lora_picker" for i in items for f in getattr(i, "items", []))

    def test_two_vae_loaders_get_distinct_field_names_in_one_analysis(self):
        """`vae`/`vae_2` - the bug this catalog-driven dedup fixes: the old
        per-node suffixing only distinguished multiple inputs on ONE node,
        so two separate VAELoader nodes both wanting "vae" used to collide."""
        workflow = parse_api_workflow(
            {
                "1": {"class_type": "VAELoader", "inputs": {"vae_name": "a.safetensors"}},
                "2": {"class_type": "VAELoader", "inputs": {"vae_name": "b.safetensors"}},
            }
        )
        analysis = suggest_fields(workflow)
        vae_candidates = [c for c in analysis.candidates if c.role == "vae"]
        assert {c.suggested_field_name for c in vae_candidates} == {"vae", "vae_2"}

        form = build_default_form(analysis)
        models_section = next(i for i in form.tabs[0].items if i.kind == "section")
        assert {f.field_name for f in models_section.items} == {"vae", "vae_2"}

    def test_wan_video_latent_default_form_has_frames_in_sampling_section(self):
        """A video-latent node (`WanImageToVideo`) contributes `frames`
        alongside the sampler's own steps/cfg/etc. in one merged "Sampling"
        section, same first-seen-order merge as any other shared section -
        see `_build_tab_items`'s docstring."""
        workflow = parse_api_workflow(_load("wan_video_latent_api.json"))
        analysis = suggest_fields(workflow)
        by_role = {c.role: c for c in analysis.candidates}
        assert by_role["frames"].current_value == 33
        assert by_role["frames"].suggested_field_name == "frames"

        form = build_default_form(analysis)
        items = form.tabs[0].items
        assert items[0].kind == "field" and items[0].field_name == "resolution"
        sections = {i.title: i for i in items if i.kind == "section"}
        assert sections.keys() == {"Models", "Sampling"}
        assert {f.field_name for f in sections["Sampling"].items} == {
            "steps", "cfg", "sampler_name", "scheduler", "denoise", "frames",
        }

    def test_sectioned_catalog_inputs_join_the_default_form_after_named_roles(self):
        """Any catalogued input with a `section` is obvious - a ControlNet
        loader joins Models, its apply-node knobs and CLIPSetLastLayer join
        Sampling - and inside a section the named roles (steps, cfg, ...)
        lead while `option` knobs follow, whichever node came first."""
        doc = _load("sdxl_basic_api.json")
        doc["40"] = {"inputs": {"control_net_name": "tile.safetensors"}, "class_type": "ControlNetLoader"}
        doc["41"] = {
            "inputs": {
                "strength": 0.8, "start_percent": 0.0, "end_percent": 1.0,
                "positive": ["6", 0], "negative": ["7", 0], "control_net": ["40", 0], "image": ["42", 0],
            },
            "class_type": "ControlNetApplyAdvanced",
        }
        doc["42"] = {"inputs": {"image": "guide.png"}, "class_type": "LoadImage"}
        doc["43"] = {"inputs": {"stop_at_clip_layer": -2, "clip": ["4", 1]}, "class_type": "CLIPSetLastLayer"}
        doc["3"]["inputs"]["positive"] = ["41", 0]
        doc["3"]["inputs"]["negative"] = ["41", 1]

        analysis = suggest_fields(parse_api_workflow(doc))
        by_name = {c.suggested_field_name: c for c in analysis.candidates}
        assert by_name["controlnet"].obvious and by_name["controlnet"].section == "Models"
        assert by_name["controlnet_strength"].obvious and by_name["clip_stop_at_layer"].obvious

        form = build_default_form(analysis)
        sections = {i.title: i for i in form.tabs[0].items if i.kind == "section"}
        assert [f.field_name for f in sections["Models"].items] == ["checkpoint", "controlnet"]
        sampling = [f.field_name for f in sections["Sampling"].items]
        assert sampling[:5] == ["steps", "cfg", "sampler_name", "scheduler", "denoise"]
        assert set(sampling[5:]) == {
            "controlnet_strength", "controlnet_start_percent", "controlnet_end_percent", "clip_stop_at_layer",
        }

    def test_default_form_keeps_a_candidates_suggested_transform(self):
        """An off-chain LoRA lands in the default form as a model field whose
        mapping strips the `models/loras/` prefix, exactly as the analysis
        suggested - `_simple_item` must not flatten every transform to none."""
        doc = _load("sdxl_basic_api.json")
        doc["50"] = {
            "inputs": {"lora_name": "detail.safetensors", "strength_model": 0.7, "model": ["4", 0]},
            "class_type": "LoraLoaderModelOnly",
        }
        form = build_default_form(suggest_fields(parse_api_workflow(doc)))
        models = next(i for i in form.tabs[0].items if i.kind == "section" and i.title == "Models")
        lora = next(f for f in models.items if f.field_type == "model" and f.config["model_type"] == "lora")
        assert lora.mappings[0].transform == "strip_model_prefix"

    def test_krea2_real_all_in_one_node_has_no_obvious_fields(self):
        """No KSampler for structural detection to key off (see
        suggest.py's module docstring) - every input on this all-in-one node
        surfaces as a non-obvious "literal" candidate, so default_form is an
        empty Generation tab and default_history is empty too."""
        object_info = _load("object_info_krea2_real.json")
        workflow = parse_api_workflow(_load("krea2_real_api.json"))
        analysis = suggest_fields(workflow, object_info=object_info)
        form = build_default_form(analysis)
        history = build_default_history(form)

        assert [t.id for t in form.tabs] == ["generation"]
        assert form.tabs[0].items == []
        assert history == []

    def test_advanced_tab_gets_settings_icon_other_tabs_stay_iconless(self):
        """Only the leading tab and one explicitly labeled "Advanced" get a
        default icon (per the shipped-preset convention) - a third tab with
        neither distinction starts iconless, forcing its icon_display down
        to "label" (see `FormTab._icon_display_needs_an_icon`)."""
        def candidate(tab: str, role: str, name: str) -> InputCandidate:
            return InputCandidate(
                node_id="1", class_type="X", node_title=None, input_name=name,
                current_value=1, value_type="int", suggested_field_type="number",
                suggested_field_name=name, suggested_label=name, role=role,
                obvious=True, suggested_tab=tab,
            )

        analysis = AnalyzeResult(
            candidates=[
                candidate("Generation", "steps", "steps"),
                candidate("Advanced", "cfg", "cfg"),
                candidate("Extra", "denoise", "denoise"),
            ],
            mode="txt2img", node_count=1, sampler_node_id=None, lora_chain=None,
        )
        form = build_default_form(analysis)

        by_label = {t.label: t for t in form.tabs}
        assert by_label["Generation"].icon == "generation"
        assert by_label["Generation"].icon_display == "icon_only"
        assert by_label["Advanced"].icon == "settings"
        assert by_label["Advanced"].icon_display == "icon_only"
        assert by_label["Extra"].icon is None
        assert by_label["Extra"].icon_display == "label"


# ----------------------------------------------------------------------
# End to end: emit -> lint -> render, through a row + group + section
# ----------------------------------------------------------------------


class TestEndToEndFormDesignerRender:
    def test_row_group_section_form_emits_lints_and_renders(self):
        workflow = _sdxl_workflow()
        row = RowItem(
            columns=2,
            items=[
                FieldItem(
                    field_name="steps", field_type="slider", label="Steps", default=20,
                    mappings=[FieldMapping(node_id="3", input_name="steps")],
                ),
                FieldItem(
                    field_name="cfg", field_type="slider", label="CFG", default=7.0,
                    mappings=[FieldMapping(node_id="3", input_name="cfg")],
                ),
            ],
        )
        group = GroupItem(
            title="Models",
            items=[
                FieldItem(
                    field_name="checkpoint", field_type="model", label="Checkpoint",
                    config={"model_type": "checkpoint"},
                    mappings=[FieldMapping(node_id="4", input_name="ckpt_name", transform="strip_model_prefix")],
                )
            ],
        )
        section = SectionItem(title="Advanced", collapsed=True, items=[row, group])
        form = ImportForm(tabs=[FormTab(id="generation", label="Generation", items=[section])])
        history = [
            HistoryEntry(field="steps", label="Steps", format="number"),
            HistoryEntry(field="checkpoint", label="Checkpoint", format="model_name"),
        ]

        local_root = REPO_ROOT / "content" / "presets" / "local"
        marker = f"FormDesignerE2E{uuid.uuid4().hex[:12]}"
        preset_family_dir = local_root / marker
        try:
            result = emit_preset(
                workflow, form, history, model_family=marker, variant="v1",
                display_name="Form Designer E2E", dest_root=local_root,
            )

            lint_proc = subprocess.run(
                [sys.executable, "scripts/preset_lint.py", str(result.preset_dir)],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
            )
            lint_output = lint_proc.stdout + lint_proc.stderr
            error_lines = [line for line in lint_output.splitlines() if line.startswith("[ERROR]")]
            assert not error_lines, lint_output

            fixture_form = {
                "seed": 7, "quantity": 1,
                "checkpoint": "models/checkpoints/sdxlBase_v10.safetensors",
                "steps": 27, "cfg": 4.0,
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
                json_text = render_proc.stdout[render_proc.stdout.index("{"):]
                record = json.loads(json_text)
                assert "error" not in record

                comfyui_pipe = next(p for p in record["pipes"] if p["name"] == "comfyui")
                field_mappings = comfyui_pipe["config"]["field_mappings"]["value"]
                resolved = {m[1]: m[0] for m in field_mappings}
                assert resolved["3.inputs.steps"] == 27
                assert resolved["3.inputs.cfg"] == 4.0
                assert resolved["4.inputs.ckpt_name"] == "sdxlBase_v10.safetensors"

                param_emitter = next(p for p in record["pipes"] if p["name"] == "param_emitter")
                params = dict(param_emitter["config"]["parameters"]["value"])
                assert params["steps"] == 27
                assert params["checkpoint"] == "sdxlBase_v10.safetensors"
            finally:
                form_file.unlink(missing_ok=True)
        finally:
            import shutil

            shutil.rmtree(preset_family_dir, ignore_errors=True)
