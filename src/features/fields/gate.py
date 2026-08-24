from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Gate(BaseField):
    """Gate field: a card that owns a boolean and the fields that boolean
    governs. Off, the card shows label/summary/toggle only; on, it expands
    and renders its `children:` inline - unlike section/group/accordion, a
    gate keeps its `name` and carries a real boolean value."""

    def __init__(self, preset_loader, field_factory=None):
        super().__init__(preset_loader)
        self.field_factory = field_factory

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'gate'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        schema['type'] = 'gate'

        config = field_info['configuration']
        if config.get('summary'):
            schema['summary'] = config['summary']
        if config.get('experimental'):
            schema['experimental'] = True

        children = self._get_children(field)
        if children:
            if self.field_factory:
                schema['children'] = [
                    self.field_factory.map_field(child, preset_id) for child in children
                ]
            else:
                schema['children'] = []

        return schema

    def _get_children(self, field) -> List:
        """Get children from field regardless of format"""
        if hasattr(field, 'children'):
            return field.children or []
        else:
            return field.get('children', [])


    @classmethod
    def description(cls) -> str:
        return ("A card that owns a boolean and the fields it governs. Off: label, optional "
                "`Experimental` chip, a static one-line summary, and the toggle - children are not "
                "rendered. On: the card expands and renders `children:` inline.")

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        return [
            FieldConfigSpec(
                name="summary",
                param_type=str,
                default="",
                description="Static one-line summary shown under the label while the gate is off. "
                             "Not a template - it does not interpolate field values.",
                example="Second pass at 2048×2048 · Balanced (2 steps)"
            ),
            FieldConfigSpec(
                name="experimental",
                param_type=bool,
                default=False,
                description="Marks the gate as experimental, showing a warning-tinted "
                             "'Experimental' chip next to the label",
                example=True
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return empty list - a gate's own value is a plain boolean with nothing to validate"""
        return []

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Basic gate",
                description="Off by default, no summary or Experimental chip",
                yaml_config="""type: gate
name: hires_fix
label: Hires Fix
default: false
children:
  - type: slider
    name: hires_strength
    label: Strength""",
                rendered_output={
                    "type": "gate",
                    "name": "hires_fix",
                    "title": "Hires Fix",
                    "default": False,
                    "children": []  # Would contain processed child fields
                },
                frontend_preview={
                    "type": "gate",
                    "name": "hires_fix",
                    "title": "Hires Fix",
                    "default": False,
                    "children": []
                }
            ),
            FieldExampleSpec(
                title="Gate with summary and Experimental chip",
                description="Static summary shown while off; warning-tinted chip next to the label",
                yaml_config="""type: gate
name: enhance
label: Enhance
default: false
configuration:
  experimental: true
  summary: "Second pass at 2048×2048 · Balanced (2 steps)"
children:
  - type: resolution
    name: enhance_resolution
  - type: select
    name: enhance_detail""",
                rendered_output={
                    "type": "gate",
                    "name": "enhance",
                    "title": "Enhance",
                    "default": False,
                    "experimental": True,
                    "summary": "Second pass at 2048×2048 · Balanced (2 steps)",
                    "children": []
                },
                frontend_preview={
                    "type": "gate",
                    "name": "enhance",
                    "title": "Enhance",
                    "default": False,
                    "experimental": True,
                    "summary": "Second pass at 2048×2048 · Balanced (2 steps)",
                    "children": []
                }
            ),
            FieldExampleSpec(
                title="Childless gate",
                description="A gate with no `children:` still emits its own boolean value - it "
                            "degrades to a self-contained toggle with no expandable region",
                yaml_config="""type: gate
name: verbose_logging
label: Verbose Logging
default: false""",
                rendered_output={
                    "type": "gate",
                    "name": "verbose_logging",
                    "title": "Verbose Logging",
                    "default": False
                },
                frontend_preview={
                    "type": "gate",
                    "name": "verbose_logging",
                    "title": "Verbose Logging",
                    "default": False
                }
            ),
        ]
