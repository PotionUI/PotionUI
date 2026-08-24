from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec
from .camera_shot_taxonomy import CATEGORY_KEYS, DEFAULT_CATEGORY_KEYS, resolve_catalog


class CameraShot(BaseField):
    """Camera-shot phrasebook - a display-only picker that copies/inserts prompt text.

    Renders the canonical shot taxonomy (angle/distance/orientation/motion) as a
    viewfinder picker. Selecting a shot surfaces its phrase - the preset's own
    `vocabulary` override for that shot key, else the generic default - to insert
    at the prompt cursor or copy. The preset ships the phrasing curated for the
    model it targets; there is no per-model lookup. Stores no form value - it is a
    helper surface, not generation data (see `input`).
    """

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        config = field_info['configuration']
        raw_categories = config.get('categories')
        if isinstance(raw_categories, list) and raw_categories:
            categories = [key for key in raw_categories if key in set(CATEGORY_KEYS)]
        else:
            categories = list(DEFAULT_CATEGORY_KEYS)

        vocabulary = config.get('vocabulary') if isinstance(config.get('vocabulary'), dict) else {}

        schema['type'] = 'camera_shot'
        schema['configuration'] = {'categories': categories}
        # Fully resolved catalog: each shot's phrase is the preset override (by
        # canonical key) or the built-in default. Unknown override keys are ignored
        # here; preset_lint flags them at authoring time.
        schema['catalog'] = resolve_catalog(vocabulary=vocabulary, categories=categories)

        return schema


    def can_handle(self, field_type: str) -> bool:
        return field_type == 'camera_shot'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        return [
            FieldConfigSpec(
                name="categories",
                param_type=list,
                default=list(DEFAULT_CATEGORY_KEYS),
                description="Which shot categories to show, in order (angle, distance, orientation, motion). Defaults to the image categories; video presets add motion.",
                example=["angle", "distance", "orientation", "motion"],
            ),
            FieldConfigSpec(
                name="vocabulary",
                param_type=dict,
                default={},
                description="Per-preset phrase overrides keyed by canonical shot key (e.g. overhead: 'from the ceiling'). Unset keys fall back to the built-in default phrase.",
                example={"overhead": "from the ceiling", "over_the_shoulder": "over-the-shoulder shot, framed past the near figure"},
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return empty list - camera_shot is a display-only field with no validation."""
        return []

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Image camera-shot picker",
                description="Default (image) categories with a couple of curated phrase overrides",
                yaml_config="""type: camera_shot
name: camera
label: Camera & Shot
configuration:
  vocabulary:
    overhead: "from the ceiling, looking straight down"
    profile: "strict side profile\"""",
                rendered_output={
                    "type": "camera_shot",
                    "name": "camera",
                    "title": "Camera & Shot",
                    "configuration": {"categories": ["angle", "distance", "orientation"]},
                },
                frontend_preview={
                    "type": "camera_shot",
                    "name": "camera",
                    "title": "Camera & Shot",
                    "configuration": {"categories": ["angle", "distance", "orientation"]},
                },
            ),
            FieldExampleSpec(
                title="Video camera-shot picker",
                description="All categories including camera motion",
                yaml_config="""type: camera_shot
name: camera
label: Camera & Shot
configuration:
  categories: [angle, distance, orientation, motion]""",
                rendered_output={
                    "type": "camera_shot",
                    "name": "camera",
                    "title": "Camera & Shot",
                    "configuration": {"categories": ["angle", "distance", "orientation", "motion"]},
                },
                frontend_preview={
                    "type": "camera_shot",
                    "name": "camera",
                    "title": "Camera & Shot",
                    "configuration": {"categories": ["angle", "distance", "orientation", "motion"]},
                },
            ),
        ]
