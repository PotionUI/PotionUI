from typing import Dict, Any, Optional, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec
from src.features.presets.configuration import resolve_field_filter_tags, resolve_reactions_filter_tags


class LoraPicker(BaseField):
    """LoRA picker field - select multiple LoRAs with per-item strength"""

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform lora_picker field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        if schema.get('reactions'):
            schema['reactions'] = resolve_reactions_filter_tags(schema['reactions'], preset_id)

        config = field_info['configuration']
        schema['configuration'] = {
            'model_type': config.get('model_type', 'lora'),
            'placeholder': config.get('placeholder', 'Select a LoRA...'),
            'strength_min': config.get('strength_min', -2.0),
            'strength_max': config.get('strength_max', 2.0),
            'strength_step': config.get('strength_step', 0.1),
            'strength_default': config.get('strength_default', 1.0),
            'max_items': config.get('max_items', 6),
            'allow_info_modal': config.get('allow_info_modal', True),
            'show_triggers': config.get('show_triggers', True),
            # Resolved tag-id list (or None = no filtering), same admin-set
            # "base model" mechanism as the `model` field - see
            # resolve_field_filter_tags in src/features/presets/configuration.py.
            'filter_tags': resolve_field_filter_tags(config.get('filter_tags'), preset_id),
        }

        schema['type'] = 'lora_picker'

        # Lets the frontend source options from this preset's engine
        # (`GET /api/presets/{preset_id}/models`) instead of the global library.
        schema['preset_id'] = preset_id

        return schema

    def input(self, field_name: str, value: Any, validation_rules: Optional[Dict[str, Any]] = None) -> Any:
        """Process lora_picker input - a list of {model, strength} entries"""
        if value is None:
            return []

        if not isinstance(value, list):
            raise ValueError(f"Invalid value for '{field_name}': expected a list of LoRA entries")

        if value == []:
            return []

        validation_rules = validation_rules or {}
        strength_min = validation_rules.get('strength_min', -2.0)
        strength_max = validation_rules.get('strength_max', 2.0)
        strength_default = validation_rules.get('strength_default', 1.0)
        max_items = validation_rules.get('max_items', 6)

        cleaned: List[Dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError(f"Invalid entry for '{field_name}': each LoRA entry must be an object")

            model = item.get('model')
            if not isinstance(model, str) or not model.strip():
                # Drop rows with missing/empty/non-string model
                continue

            raw_strength = item.get('strength', strength_default)
            try:
                strength = float(raw_strength)
            except (TypeError, ValueError):
                strength = float(strength_default)

            strength = max(strength_min, min(strength_max, strength))

            cleaned.append({
                'model': model.strip(),
                'strength': strength,
            })

        if max_items is not None and len(cleaned) > max_items:
            raise ValueError(f"Too many LoRA entries for '{field_name}': maximum is {max_items}")

        return cleaned

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'lora_picker'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="model_type",
                param_type=str,
                default="lora",
                description="Type of model to filter by (fixed to lora)",
                example="lora"
            ),
            FieldConfigSpec(
                name="placeholder",
                param_type=str,
                default="Select a LoRA...",
                description="Placeholder text for the LoRA picker",
                example="Add a LoRA..."
            ),
            FieldConfigSpec(
                name="strength_min",
                param_type=float,
                default=-2.0,
                description="Minimum allowed strength value",
                example=-2.0
            ),
            FieldConfigSpec(
                name="strength_max",
                param_type=float,
                default=2.0,
                description="Maximum allowed strength value",
                example=2.0
            ),
            FieldConfigSpec(
                name="strength_step",
                param_type=float,
                default=0.1,
                description="Step increment for the strength slider",
                example=0.1
            ),
            FieldConfigSpec(
                name="strength_default",
                param_type=float,
                default=1.0,
                description="Default strength applied to newly added LoRAs",
                example=1.0
            ),
            FieldConfigSpec(
                name="max_items",
                param_type=int,
                default=6,
                description="Maximum number of LoRAs that can be selected (None for unlimited)",
                example=6
            ),
            FieldConfigSpec(
                name="allow_info_modal",
                param_type=bool,
                default=True,
                description="Allow opening the LoRA information modal",
                example=True
            ),
            FieldConfigSpec(
                name="show_triggers",
                param_type=bool,
                default=True,
                description="Show trigger words for each selected LoRA",
                example=True
            ),
            FieldConfigSpec(
                name="filter_tags",
                param_type=list,
                default=None,
                description=(
                    "Restrict this field's LoRA options to models tagged with at least one "
                    "of these admin Tag IDs (OR semantics). Either a literal list of tag IDs, "
                    "or '@config:<key>' to resolve against the preset's stored `configuration:` "
                    "value for <key> at form-schema time. Missing/empty resolved value means "
                    "no filtering. Same mechanism as the `model` field's filter_tags."
                ),
                example="@config:lora_tags"
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        base_rules = super().validation_rules()
        lora_rules = [
            FieldValidationSpec(
                rule_name="strength_min",
                description="Minimum allowed strength value",
                param_type=float,
                example=-2.0
            ),
            FieldValidationSpec(
                rule_name="strength_max",
                description="Maximum allowed strength value",
                param_type=float,
                example=2.0
            ),
            FieldValidationSpec(
                rule_name="max_items",
                description="Maximum number of LoRAs allowed",
                param_type=int,
                example=6
            ),
        ]
        return base_rules + lora_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Basic LoRA Picker",
                description="Select up to 6 LoRAs with adjustable strength",
                yaml_config="""type: lora_picker
name: loras
label: LoRAs
configuration:
  max_items: 6
  strength_min: -2.0
  strength_max: 2.0
  strength_default: 1.0""",
                rendered_output={
                    "type": "lora_picker",
                    "name": "loras",
                    "title": "LoRAs",
                    "configuration": {
                        "model_type": "lora",
                        "placeholder": "Select a LoRA...",
                        "strength_min": -2.0,
                        "strength_max": 2.0,
                        "strength_step": 0.1,
                        "strength_default": 1.0,
                        "max_items": 6,
                        "allow_info_modal": True,
                        "show_triggers": True
                    }
                },
                frontend_preview={
                    "type": "lora_picker",
                    "name": "preview_loras",
                    "title": "LoRAs",
                    "configuration": {
                        "max_items": 6,
                        "strength_min": -2.0,
                        "strength_max": 2.0
                    }
                }
            ),
        ]
