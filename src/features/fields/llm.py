from typing import Dict, Any, List
from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class LLMField(BaseField):
    """LLM configuration field with modular UI components

    Provides a configurable field for selecting LLM configurations
    and entering custom prompts.
    Components can be shown/hidden via configuration.
    """

    def can_handle(self, field_type: str) -> bool:
        """Check if this field handles the given type"""
        return field_type == 'llm'

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform LLM field to frontend schema with loaded options

        Args:
            field: Field configuration from preset
            preset_id: Preset ID (unused but required by interface)

        Returns:
            Dictionary with field schema including options for selects
        """
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        config = field_info['configuration']

        # LLM configurations will be loaded client-side from the API
        # to ensure user-specific LLM assignments are respected
        if config.get('show_llm_select', True):
            schema['llm_options'] = []  # Will be populated by frontend
            # Default to empty if allow_empty is true
            schema['default_llm_id'] = '' if config.get('allow_empty', True) else None

        # Add configuration flags
        schema['configuration'] = {
            'show_llm_select': config.get('show_llm_select', True),
            'show_prompt': config.get('show_prompt', True),
            'allow_empty': config.get('allow_empty', True)
        }

        # Add tooltip if provided
        if config.get('tooltip'):
            schema['tooltip'] = config.get('tooltip')

        return schema


    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="show_llm_select",
                param_type=bool,
                default=True,
                description="Show LLM configuration selector",
                example=True
            ),
            FieldConfigSpec(
                name="show_prompt",
                param_type=bool,
                default=True,
                description="Show prompt text input",
                example=True
            ),
            FieldConfigSpec(
                name="tooltip",
                param_type=str,
                default=None,
                description="Tooltip text to display next to the field label",
                example="Configure LLM settings for prompt expansion",
                required=False
            ),
            FieldConfigSpec(
                name="allow_empty",
                param_type=bool,
                default=True,
                description="Allow empty/null LLM selection to disable the feature",
                example=True
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether an LLM configuration is required",
                param_type=bool,
                example=True
            ),
            FieldValidationSpec(
                rule_name="valid_structure",
                description="Value must be a dictionary with keys: llm_id, prompt",
                param_type=dict,
                example={"llm_id": "string", "prompt": "string"}
            ),
            FieldValidationSpec(
                rule_name="llm_exists",
                description="Validate that selected LLM configuration exists",
                param_type=bool,
                example=True
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Full LLM Field",
                description="LLM field with all components enabled",
                yaml_config="""type: llm
name: expansion_llm
label: Prompt Expansion
configuration:
  show_llm_select: true
  show_prompt: true""",
                rendered_output={
                    "type": "llm",
                    "name": "expansion_llm",
                    "title": "Prompt Expansion",
                    "llm_options": [],  # Populated at runtime
                    "configuration": {
                        "show_llm_select": True,
                        "show_prompt": True
                    }
                },
                frontend_preview={
                    "type": "llm",
                    "name": "preview_llm",
                    "title": "Prompt Expansion",
                    "llm_options": [],
                    "configuration": {
                        "show_llm_select": True,
                        "show_prompt": True
                    }
                }
            ),
            FieldExampleSpec(
                title="Simple LLM Selector",
                description="Just LLM selection",
                yaml_config="""type: llm
name: llm_config
label: Select LLM
configuration:
  show_llm_select: true
  show_prompt: false""",
                rendered_output={
                    "type": "llm",
                    "name": "llm_config",
                    "title": "Select LLM",
                    "llm_options": [],
                    "configuration": {
                        "show_llm_select": True,
                        "show_prompt": False
                    }
                },
                frontend_preview={
                    "type": "llm",
                    "name": "preview_llm_simple",
                    "title": "Select LLM",
                    "llm_options": [],
                    "configuration": {
                        "show_llm_select": True,
                        "show_prompt": False
                    }
                }
            ),
            FieldExampleSpec(
                title="Prompt Expansion Field",
                description="LLM field for prompt expansion",
                yaml_config="""type: llm
name: prompt_expander_config
label: Prompt Expander
description: Configure LLM for expanding your prompts
configuration:
  show_llm_select: true
  show_prompt: true""",
                rendered_output={
                    "type": "llm",
                    "name": "prompt_expander_config",
                    "title": "Prompt Expander",
                    "description": "Configure LLM for expanding your prompts",
                    "llm_options": [],
                    "configuration": {
                        "show_llm_select": True,
                        "show_prompt": True
                    }
                },
                frontend_preview={
                    "type": "llm",
                    "name": "preview_prompt_expander",
                    "title": "Prompt Expander",
                    "description": "Configure LLM for expanding your prompts",
                    "llm_options": [],
                    "configuration": {
                        "show_llm_select": True,
                        "show_prompt": True
                    }
                }
            ),
        ]

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Map a field to its JSON schema representation"""
        return self.output(field, preset_id)
