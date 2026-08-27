"""
Tests for @loop directive in PresetProcessor.

This module tests the @loop functionality for dynamic configuration generation
in preset processing.
"""

import pytest
from unittest.mock import Mock

from src.features.presets import PresetProcessor
from src.platform.templating import TemplateProcessor
from src.platform.templating.errors import TemplateEvaluationError
from src.features.presets.templates import PresetTemplate


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


class TestLoopDirective:
    """Test cases for @loop directive functionality."""
    
    def test_count_based_loop(self, preset_processor):
        """Test basic count-based loop."""
        value = {
            "@loop": {
                "count": 3,
                "template": {
                    "index": "{{ loop.index }}",
                    "index0": "{{ loop.index0 }}"
                }
            }
        }
        context = {}
        
        result = preset_processor.process_value(value, context)
        
        # loop.index/index0 are ints; exact-expression scalars evaluate to
        # native values now (spec §1), no rendered-string round-trip.
        assert len(result) == 3
        assert result[0] == {"index": 1, "index0": 0}
        assert result[1] == {"index": 2, "index0": 1}
        assert result[2] == {"index": 3, "index0": 2}
    
    def test_count_based_loop_with_template_count(self, preset_processor):
        """Test count-based loop with templated count value."""
        value = {
            "@loop": {
                "count": "{{ num_items }}",
                "template": {
                    "index": "{{ loop.index }}"
                }
            }
        }
        context = {"num_items": 2}
        
        result = preset_processor.process_value(value, context)
        
        assert len(result) == 2
        assert result[0] == {"index": 1}
        assert result[1] == {"index": 2}
    
    def test_items_based_loop_with_list(self, preset_processor):
        """Test items-based loop with a list."""
        value = {
            "@loop": {
                "items": ["apple", "banana", "cherry"],
                "template": {
                    "fruit": "{{ item }}",
                    "index": "{{ loop.index }}"
                }
            }
        }
        context = {}
        
        result = preset_processor.process_value(value, context)
        
        assert len(result) == 3
        assert result[0] == {"fruit": "apple", "index": 1}
        assert result[1] == {"fruit": "banana", "index": 2}
        assert result[2] == {"fruit": "cherry", "index": 3}
    
    def test_items_literal_list_renders_templated_elements(self, preset_processor):
        """A literal `items:` list (docs/presets.md: "a literal YAML list is
        fine") may itself contain templated string elements - each element
        must be rendered before becoming a loop item, not handed to the loop
        body raw. Mirrors the Krea2 preset's `enhance_detail` param-emitter
        loop, which leaked the unrendered `{{ form.enhance_detail | ... }}`
        string into recorded generation parameters.
        """
        value = {
            "@loop": {
                "items": ["{{ form.detail | default('balanced') }}"],
                "template": ["enhance_detail", "{{ item }}"]
            }
        }
        context = {"form": {"detail": "high"}}

        result = preset_processor.process_value(value, context)

        assert result == [["enhance_detail", "high"]]

    def test_items_based_loop_with_dict(self, preset_processor):
        """Test items-based loop with a dictionary."""
        value = {
            "@loop": {
                "items": {"key1": "value1", "key2": "value2"},
                "as": "k, v",
                "template": {
                    "key": "{{ k }}",
                    "value": "{{ v }}"
                }
            }
        }
        context = {}
        
        result = preset_processor.process_value(value, context)
        
        assert len(result) == 2
        assert {"key": "key1", "value": "value1"} in result
        assert {"key": "key2", "value": "value2"} in result
    
    def test_loop_with_conditional_when(self, preset_processor):
        """Test loop with conditional 'when' filtering."""
        value = {
            "@loop": {
                "items": [
                    {"name": "apple", "enabled": True},
                    {"name": "banana", "enabled": False},
                    {"name": "cherry", "enabled": True}
                ],
                "when": "{{ item.enabled }}",
                "template": {
                    "fruit": "{{ item.name }}"
                }
            }
        }
        context = {}
        
        result = preset_processor.process_value(value, context)
        
        assert len(result) == 2
        assert {"fruit": "apple"} in result
        assert {"fruit": "cherry"} in result
        assert {"fruit": "banana"} not in result
    
    def test_loop_with_first_last_flags(self, preset_processor):
        """Test loop with first/last boolean flags."""
        value = {
            "@loop": {
                "count": 3,
                "template": {
                    "is_first": "{{ loop.first }}",
                    "is_last": "{{ loop.last }}"
                }
            }
        }
        context = {}
        
        result = preset_processor.process_value(value, context)
        
        assert result[0] == {"is_first": True, "is_last": False}
        assert result[1] == {"is_first": False, "is_last": False}
        assert result[2] == {"is_first": False, "is_last": True}
    
    def test_loop_with_templated_items(self, preset_processor):
        """Test loop with items from template expression."""
        value = {
            "@loop": {
                "items": "{{ my_list }}",
                "template": {
                    "item": "{{ item }}"
                }
            }
        }
        context = {"my_list": ["a", "b", "c"]}
        
        result = preset_processor.process_value(value, context)
        
        assert len(result) == 3
        assert result[0] == {"item": "a"}
        assert result[1] == {"item": "b"}
        assert result[2] == {"item": "c"}
    
    def test_nested_loops(self, preset_processor):
        """Test nested @loop directives."""
        value = {
            "@loop": {
                "count": 2,
                "template": {
                    "outer": "{{ loop.index }}",
                    "inner": {
                        "@loop": {
                            "count": 2,
                            "template": {
                                "inner_index": "{{ loop.index }}"
                            }
                        }
                    }
                }
            }
        }
        context = {}
        
        result = preset_processor.process_value(value, context)
        
        assert len(result) == 2
        assert result[0]["outer"] == 1
        assert len(result[0]["inner"]) == 2
        assert result[0]["inner"][0] == {"inner_index": 1}
        assert result[0]["inner"][1] == {"inner_index": 2}
    
    def test_loop_error_no_count_or_items(self, preset_processor):
        """Test that loop without count or items raises error."""
        value = {
            "@loop": {
                "template": {"index": "{{ loop.index }}"}
            }
        }
        context = {}
        
        with pytest.raises(ValueError, match="@loop requires either 'count' or 'items'"):
            preset_processor.process_value(value, context)
    
    def test_loop_error_invalid_count(self, preset_processor):
        """Test that loop with invalid count raises a TemplateEvaluationError.

        `count` is evaluated natively (spec §1); a non-int result raises the
        structured evaluator error instead of the old ValueError.
        """
        value = {
            "@loop": {
                "count": "{{ invalid }}",
                "template": {"index": "{{ loop.index }}"}
            }
        }
        context = {"invalid": "not_a_number"}

        with pytest.raises(TemplateEvaluationError, match="@loop count must evaluate to an int"):
            preset_processor.process_value(value, context)
    
    def test_real_world_lora_example(self, preset_processor):
        """Test real-world example of LoRA configuration."""
        value = {
            "loras": {
                "@loop": {
                    "count": "{{ num_lora_slots }}",
                    "template": {
                        "file_path": "{{ path('lora', input['form']['lora_' ~ loop.index ~ '_file']) }}",
                        "weight": "{{ input['form']['lora_' ~ loop.index ~ '_strength'] }}"
                    }
                }
            }
        }
        context = {
            "num_lora_slots": 3,
            "input": {
                "form": {
                    "lora_1_file": "style.safetensors",
                    "lora_1_strength": "0.5",
                    "lora_2_file": "detail.safetensors", 
                    "lora_2_strength": "0.7",
                    "lora_3_file": "color.safetensors",
                    "lora_3_strength": "0.3"
                }
            }
        }
        
        result = preset_processor.process_value(value, context)
        
        assert "loras" in result
        assert len(result["loras"]) == 3
        assert result["loras"][0]["file_path"] == "models/loras/style.safetensors"
        assert result["loras"][0]["weight"] == "0.5"
        assert result["loras"][1]["file_path"] == "models/loras/detail.safetensors"
        assert result["loras"][1]["weight"] == "0.7"
    
    def test_real_world_embeddings_example(self, preset_processor):
        """Test real-world example of embeddings configuration."""
        value = {
            "embeddings": {
                "@loop": {
                    "items": "{{ embeddings_config }}",
                    "template": {
                        "{{ item.name }}": {
                            "enabled": "{% if item.form_key in enabled_embeddings %}true{% else %}false{% endif %}",
                            "file_path": "models/embeddings/{{ item.name }}.safetensors"
                        }
                    }
                }
            }
        }
        context = {
            "embeddings_config": [
                {"name": "negative_v1", "form_key": "neg_v1"},
                {"name": "positive_v1", "form_key": "pos_v1"}
            ],
            "enabled_embeddings": ["neg_v1"]
        }
        
        result = preset_processor.process_value(value, context)
        
        assert "embeddings" in result
        embeddings = result["embeddings"]
        
        # Debug: Print the actual structure
        print(f"Embeddings result: {embeddings}")
        
        # The result is a list of dicts, each with a dynamic key
        assert len(embeddings) == 2
        
        # Check that we have the expected structure
        # Each item in the list is a dict with the embedding name as key
        embedding_names = []
        for embedding_dict in embeddings:
            embedding_names.extend(embedding_dict.keys())
        
        assert "negative_v1" in embedding_names
        assert "positive_v1" in embedding_names
        
        # Check the enabled status
        for embedding_dict in embeddings:
            if "negative_v1" in embedding_dict:
                assert embedding_dict["negative_v1"]["enabled"] == "true"
            elif "positive_v1" in embedding_dict:
                assert embedding_dict["positive_v1"]["enabled"] == "false"