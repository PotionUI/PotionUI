"""
Tests for @loop directive in form field processing.

This module tests the @loop functionality for dynamic form field generation.
"""

import pytest
from unittest.mock import Mock

from src.features.presets import PresetProcessor
from src.platform.templating import TemplateProcessor
from src.features.presets.templates import FieldTemplate


@pytest.fixture
def preset_processor():
    """Create a PresetProcessor instance with mocked dependencies."""
    settings = Mock()
    template_processor = TemplateProcessor(settings)
    model_directories = Mock()
    preset_template_loader = Mock()

    return PresetProcessor(
        template_processor=template_processor,
        model_directories=model_directories,
        settings=settings,
        preset_template_loader=preset_template_loader
    )


class TestFormLoopDirective:
    """Test cases for @loop directive functionality in form fields."""

    def test_simple_count_based_loop(self, preset_processor):
        """Test basic count-based loop for form fields."""
        fields = [
            FieldTemplate(
                type="@loop",
                configuration={
                    "count": 3,
                    "template": {
                        "type": "textbox",
                        "name": "field_{{ loop.index }}",
                        "label": "Field {{ loop.index }}"
                    }
                }
            )
        ]
        context = {}

        result = preset_processor._expand_loop_fields(fields, context)

        assert len(result) == 3
        assert result[0].type == "textbox"
        assert result[0].name == "field_1"
        assert result[0].label == "Field 1"
        assert result[1].name == "field_2"
        assert result[2].name == "field_3"

    def test_loop_with_template_count_from_context(self, preset_processor):
        """Test loop with count from context variable."""
        fields = [
            FieldTemplate(
                type="@loop",
                configuration={
                    "count": "{{ num_slots }}",
                    "template": {
                        "type": "select",
                        "name": "slot_{{ loop.index }}",
                        "label": "Slot {{ loop.index }}"
                    }
                }
            )
        ]
        context = {"num_slots": 2}

        result = preset_processor._expand_loop_fields(fields, context)

        assert len(result) == 2
        assert result[0].name == "slot_1"
        assert result[1].name == "slot_2"

    def test_loop_with_group_and_children(self, preset_processor):
        """Test loop creating groups with nested children."""
        fields = [
            FieldTemplate(
                type="@loop",
                configuration={
                    "count": 2,
                    "template": {
                        "type": "group",
                        "label": "ControlNet {{ loop.index }}",
                        "children": [
                            {
                                "type": "model",
                                "name": "controlnet_{{ loop.index }}_model",
                                "label": "Model"
                            },
                            {
                                "type": "slider",
                                "name": "controlnet_{{ loop.index }}_scale",
                                "label": "Scale"
                            }
                        ]
                    }
                }
            )
        ]
        context = {}

        result = preset_processor._expand_loop_fields(fields, context)

        assert len(result) == 2
        assert result[0].type == "group"
        assert result[0].label == "ControlNet 1"
        assert len(result[0].children) == 2
        assert result[0].children[0].name == "controlnet_1_model"
        assert result[0].children[1].name == "controlnet_1_scale"
        assert result[1].label == "ControlNet 2"
        assert result[1].children[0].name == "controlnet_2_model"

    def test_loop_with_nested_loop(self, preset_processor):
        """Test nested @loop directives in form fields.

        Note: Due to template processing order, nested loops that use {{ loop.index }}
        will have their templates resolved with the outer loop's context first.
        To create truly independent nested loops, use a unique field naming pattern
        or avoid template variables in nested loop field names.
        """
        fields = [
            FieldTemplate(
                type="@loop",
                configuration={
                    "count": 2,
                    "template": {
                        "type": "group",
                        "label": "Group {{ loop.index }}",
                        "children": [
                            {
                                "type": "@loop",
                                "configuration": {
                                    "count": 2,
                                    "template": {
                                        "type": "textbox",
                                        "name": "field_a",
                                        "label": "Field A"
                                    }
                                }
                            }
                        ]
                    }
                }
            )
        ]
        context = {}

        result = preset_processor._expand_loop_fields(fields, context)

        assert len(result) == 2
        assert result[0].type == "group"
        assert result[0].label == "Group 1"
        # Inner loop creates 2 fields per group
        assert len(result[0].children) == 2
        assert result[0].children[0].name == "field_a"
        assert result[0].children[1].name == "field_a"
        assert result[1].label == "Group 2"
        assert len(result[1].children) == 2
        assert result[1].children[0].name == "field_a"
        assert result[1].children[1].name == "field_a"

    def test_loop_with_preset_vars(self, preset_processor):
        """Test loop using preset variables from context."""
        fields = [
            FieldTemplate(
                type="@loop",
                configuration={
                    "count": "{{ preset['vars']['num_lora_slots'] }}",
                    "template": {
                        "type": "model",
                        "name": "lora_{{ loop.index }}",
                        "label": "LoRA {{ loop.index }}",
                        "configuration": {
                            "model_type": "lora"
                        }
                    }
                }
            )
        ]
        context = {
            "preset": {
                "vars": {
                    "num_lora_slots": 3
                }
            }
        }

        result = preset_processor._expand_loop_fields(fields, context)

        assert len(result) == 3
        assert result[0].name == "lora_1"
        assert result[0].configuration["model_type"] == "lora"
        assert result[2].name == "lora_3"

    def test_loop_mixed_with_regular_fields(self, preset_processor):
        """Test loop fields mixed with regular fields."""
        fields = [
            FieldTemplate(
                type="checkbox",
                name="enable_feature",
                label="Enable Feature"
            ),
            FieldTemplate(
                type="@loop",
                configuration={
                    "count": 2,
                    "template": {
                        "type": "textbox",
                        "name": "field_{{ loop.index }}",
                        "label": "Field {{ loop.index }}"
                    }
                }
            ),
            FieldTemplate(
                type="button",
                name="submit",
                label="Submit"
            )
        ]
        context = {}

        result = preset_processor._expand_loop_fields(fields, context)

        assert len(result) == 4
        assert result[0].type == "checkbox"
        assert result[0].name == "enable_feature"
        assert result[1].type == "textbox"
        assert result[1].name == "field_1"
        assert result[2].type == "textbox"
        assert result[2].name == "field_2"
        assert result[3].type == "button"
        assert result[3].name == "submit"

    def test_loop_with_when_condition(self, preset_processor):
        """Test loop with conditional when clause."""
        fields = [
            FieldTemplate(
                type="@loop",
                configuration={
                    "count": 3,
                    "when": "{{ loop.index <= 2 }}",
                    "template": {
                        "type": "textbox",
                        "name": "field_{{ loop.index }}",
                        "label": "Field {{ loop.index }}"
                    }
                }
            )
        ]
        context = {}

        result = preset_processor._expand_loop_fields(fields, context)

        # Only 2 fields should be created due to when condition
        assert len(result) == 2
        assert result[0].name == "field_1"
        assert result[1].name == "field_2"

    def test_loop_with_items_list(self, preset_processor):
        """Test loop iterating over a list of items."""
        fields = [
            FieldTemplate(
                type="@loop",
                configuration={
                    "items": ["canny", "depth", "openpose"],
                    "template": {
                        "type": "select",
                        "name": "controlnet_{{ item }}",
                        "label": "ControlNet {{ item }}"
                    }
                }
            )
        ]
        context = {}

        result = preset_processor._expand_loop_fields(fields, context)

        assert len(result) == 3
        assert result[0].name == "controlnet_canny"
        assert result[0].label == "ControlNet canny"
        assert result[1].name == "controlnet_depth"
        assert result[2].name == "controlnet_openpose"

    def test_loop_error_no_count_or_items(self, preset_processor):
        """Test that loop without count or items raises error."""
        fields = [
            FieldTemplate(
                type="@loop",
                configuration={
                    "template": {
                        "type": "textbox",
                        "name": "field",
                        "label": "Field"
                    }
                }
            )
        ]
        context = {}

        with pytest.raises(ValueError, match="@loop requires either 'count' or 'items'"):
            preset_processor._expand_loop_fields(fields, context)

    def test_loop_error_no_configuration(self, preset_processor):
        """Test that loop without configuration raises error."""
        fields = [
            FieldTemplate(
                type="@loop"
            )
        ]
        context = {}

        with pytest.raises(ValueError, match="@loop field requires configuration"):
            preset_processor._expand_loop_fields(fields, context)

    def test_loop_with_template_list(self, preset_processor):
        """Test loop with template that returns a list of fields."""
        fields = [
            FieldTemplate(
                type="@loop",
                configuration={
                    "count": 2,
                    "template": [
                        {
                            "type": "textbox",
                            "name": "field_{{ loop.index }}_a",
                            "label": "Field {{ loop.index }} A"
                        },
                        {
                            "type": "textbox",
                            "name": "field_{{ loop.index }}_b",
                            "label": "Field {{ loop.index }} B"
                        }
                    ]
                }
            )
        ]
        context = {}

        result = preset_processor._expand_loop_fields(fields, context)

        # 2 iterations × 2 fields per iteration = 4 fields
        assert len(result) == 4
        assert result[0].name == "field_1_a"
        assert result[1].name == "field_1_b"
        assert result[2].name == "field_2_a"
        assert result[3].name == "field_2_b"

    def test_real_world_controlnet_example(self, preset_processor):
        """Test real-world ControlNet configuration with loop."""
        fields = [
            FieldTemplate(
                type="checkbox",
                name="enable_controlnet",
                label="Enable ControlNet",
                default=False
            ),
            FieldTemplate(
                type="@loop",
                configuration={
                    "count": 3,
                    "template": {
                        "type": "group",
                        "label": "ControlNet {{ loop.index }}",
                        "children": [
                            {
                                "type": "model",
                                "name": "controlnet_{{ loop.index }}_model",
                                "label": "ControlNet Model",
                                "configuration": {
                                    "model_type": "controlnet",
                                    "placeholder": "Select a ControlNet model..."
                                }
                            },
                            {
                                "type": "select",
                                "name": "controlnet_{{ loop.index }}_type",
                                "label": "ControlNet Type",
                                "default": "canny",
                                "configuration": {
                                    "options": [
                                        {"label": "Canny Edge", "value": "canny"},
                                        {"label": "Depth", "value": "depth"},
                                        {"label": "OpenPose", "value": "openpose"}
                                    ]
                                }
                            },
                            {
                                "type": "image",
                                "name": "controlnet_{{ loop.index }}_image",
                                "label": "Control Image"
                            },
                            {
                                "type": "slider",
                                "name": "controlnet_{{ loop.index }}_scale",
                                "label": "Conditioning Scale",
                                "default": 1.0,
                                "configuration": {
                                    "min": 0.0,
                                    "max": 2.0,
                                    "step": 0.05
                                }
                            }
                        ]
                    }
                }
            )
        ]
        context = {}

        result = preset_processor._expand_loop_fields(fields, context)

        assert len(result) == 4  # 1 checkbox + 3 groups
        assert result[0].type == "checkbox"
        assert result[1].type == "group"
        assert result[1].label == "ControlNet 1"
        assert len(result[1].children) == 4
        assert result[1].children[0].name == "controlnet_1_model"
        assert result[1].children[1].name == "controlnet_1_type"
        assert result[1].children[2].name == "controlnet_1_image"
        assert result[1].children[3].name == "controlnet_1_scale"
        assert result[3].label == "ControlNet 3"
        assert result[3].children[0].name == "controlnet_3_model"
