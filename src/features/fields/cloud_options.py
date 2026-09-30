from typing import Any, Dict, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldExampleSpec


class CloudOptions(BaseField):
    def can_handle(self, field_type: str) -> bool:
        return field_type == 'cloud_options'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        config = field_info['configuration']
        schema['configuration'] = {
            'include_unbound': bool(config.get('include_unbound', True)),
        }
        return schema

    @classmethod
    def description(cls) -> str:
        return (
            "Provider options: renders the chosen cloud model's own extra parameters (the 'x.' extras) and "
            "any canonical parameter no other field of the form is bound to, as standard controls. The value "
            "is an object keyed by parameter name and is validated on the server against the model's catalog entry."
        )

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        return [
            FieldConfigSpec(
                name="include_unbound",
                param_type=bool,
                default=True,
                description=(
                    "Also offer the canonical parameters of the model that no other field in this form is bound "
                    "to. Turn off to offer only the provider's own 'x.' extras."
                ),
                example=False,
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Provider options",
                description="Offers the extra parameters of whichever model the model field holds",
                yaml_config="\n".join([
                    "- type: cloud_options",
                    "  name: provider_options",
                    "  label: Provider options",
                    "  capability:",
                    "    model_field: model",
                ]),
            ),
        ]
