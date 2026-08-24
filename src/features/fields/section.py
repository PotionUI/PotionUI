from typing import Dict, Any, Optional, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Section(BaseField):
    """Section field: a lightweight divider - mono uppercase title with a
    trailing hairline rule, no indent, no value. Without `children:` it stays
    a flat divider - no fold, no chevron, no click target. With `children:`
    it becomes a foldable container: the title row is a toggle, and its
    children are mapped through the field factory like group/accordion."""

    def __init__(self, preset_loader, field_factory=None):
        super().__init__(preset_loader)
        self.field_factory = field_factory

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'section'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        schema['type'] = 'section'

        # Display-only, like header/alert/markdown - no value to bind.
        schema.pop('name', None)

        config = field_info['configuration']
        if config.get('badge'):
            schema['badge'] = config['badge']
        if config.get('tooltip'):
            schema['tooltip'] = config['tooltip']
        if config.get('collapsed'):
            schema['collapsed'] = True
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
        return "Mono uppercase title with a hairline rule, no indent, no value. A plain divider without `children:`; a foldable container - title row toggles, chevron included - with them."

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        return [
            FieldConfigSpec(
                name="badge",
                param_type=str,
                default="",
                description="Optional trailing meta text shown after the hairline (e.g. a field count)",
                example="4 fields"
            ),
            FieldConfigSpec(
                name="tooltip",
                param_type=str,
                default="",
                description="Optional hint tooltip shown next to the title",
                example="These fields only apply in img2img mode"
            ),
            FieldConfigSpec(
                name="collapsed",
                param_type=bool,
                default=False,
                description="Whether a section that has `children:` starts folded, the first time "
                             "a user sees it. Ignored on a childless section, which has no fold "
                             "state - it stays a plain divider with no chevron and no click "
                             "target. Once a user folds or unfolds the section, the frontend "
                             "remembers their choice in their session (per preset + mode) and "
                             "that remembered state wins on every later visit - this only "
                             "decides the very first render.",
                example=True
            ),
            FieldConfigSpec(
                name="experimental",
                param_type=bool,
                default=False,
                description="Marks the section as experimental, showing a warning-tinted "
                             "'Experimental' chip next to the title",
                example=True
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return empty list - Section is a display-only field with no validation"""
        return []

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Basic Section",
                description="Plain divider between two groups of fields",
                yaml_config="""type: section
label: Sampling""",
                rendered_output={
                    "type": "section",
                    "title": "Sampling"
                },
                frontend_preview={
                    "type": "section",
                    "title": "Sampling"
                }
            ),
            FieldExampleSpec(
                title="Section with trailing meta",
                description="Divider annotated with a field count",
                yaml_config="""type: section
label: Image
configuration:
  badge: 4 fields""",
                rendered_output={
                    "type": "section",
                    "title": "Image",
                    "badge": "4 fields"
                },
                frontend_preview={
                    "type": "section",
                    "title": "Image",
                    "badge": "4 fields"
                }
            ),
            FieldExampleSpec(
                title="Section with hint tooltip",
                description="Divider with an explanatory tooltip next to the title",
                yaml_config="""type: section
label: Refiner
configuration:
  tooltip: Only used when hires fix is enabled""",
                rendered_output={
                    "type": "section",
                    "title": "Refiner",
                    "tooltip": "Only used when hires fix is enabled"
                },
                frontend_preview={
                    "type": "section",
                    "title": "Refiner",
                    "tooltip": "Only used when hires fix is enabled"
                }
            ),
            FieldExampleSpec(
                title="Foldable section with children",
                description="A section with `children:` becomes a foldable container - the title "
                            "row is a toggle, closed by default here via `collapsed: true`",
                yaml_config="""type: section
label: Advanced
configuration:
  collapsed: true
children:
  - type: slider
    name: strength
    label: Strength""",
                rendered_output={
                    "type": "section",
                    "title": "Advanced",
                    "collapsed": True,
                    "children": []  # Would contain processed child fields
                },
                frontend_preview={
                    "type": "section",
                    "title": "Advanced",
                    "collapsed": True,
                    "children": []
                }
            ),
            FieldExampleSpec(
                title="Experimental section",
                description="Flags the section (and its fields) as experimental with a "
                            "warning-tinted chip next to the title",
                yaml_config="""type: section
label: Live Upscale
configuration:
  experimental: true""",
                rendered_output={
                    "type": "section",
                    "title": "Live Upscale",
                    "experimental": True
                },
                frontend_preview={
                    "type": "section",
                    "title": "Live Upscale",
                    "experimental": True
                }
            ),
        ]
