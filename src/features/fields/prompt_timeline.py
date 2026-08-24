from typing import Any, Dict, List

from .base_field import BaseField
from .specs import FieldExampleSpec


class PromptTimeline(BaseField):
    """Prompt timeline field - passthrough mapping, the frontend renders the timeline UI"""

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'prompt_timeline'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        return self.create_base_schema(field_info)

    @classmethod
    def description(cls) -> str:
        return "Prompt timeline field for per-frame/per-segment prompt scheduling"

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Prompt Timeline",
                description="Segment-based prompt scheduling for video generation",
                yaml_config="""type: prompt_timeline
name: prompt_timeline
label: Prompt Timeline""",
                rendered_output={
                    "type": "prompt_timeline",
                    "name": "prompt_timeline",
                    "title": "Prompt Timeline",
                },
            ),
        ]
