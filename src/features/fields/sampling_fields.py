"""`sampler` / `schedule` field types.

Both render as a plain dropdown (`core:SelectField` on the frontend, same
component `select` uses) whose options come from the sampler/schedule
registries (`src/platform/runtime/native/sampling/registry.py`) instead of a
preset-authored list - the registry is the single source of truth for what
samplers/schedules exist, populated by core at import time and by plugins
through the `samplers:`/`schedules:` manifest roots. `family` narrows the
registry's own family filter; `include`/`exclude` narrow further, same
semantics as `SamplingRegistry.select()`.
"""

from typing import Any, Dict, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec
from src.platform.runtime.native.sampling.registry import sampler_registry, schedule_registry


def _configuration_specs() -> List[FieldConfigSpec]:
    return [
        FieldConfigSpec(
            name="family",
            param_type=str,
            default="",
            description=(
                "Model family to narrow the catalog to (e.g. 'flux', 'wan', 'krea2'). "
                "Empty matches every family-agnostic entry only - see "
                "SamplingRegistry.for_family."
            ),
            example="flux",
        ),
        FieldConfigSpec(
            name="include",
            param_type=list,
            default=[],
            description="Explicit allowlist of registry keys, kept in this order. Empty means no narrowing.",
            example=["euler", "dpmpp_2m_sde"],
        ),
        FieldConfigSpec(
            name="exclude",
            param_type=list,
            default=[],
            description="Registry keys to drop from the family's catalog.",
            example=["lcm"],
        ),
        FieldConfigSpec(
            name="allow_empty",
            param_type=bool,
            default=False,
            description="Allow an empty/null selection option.",
            example=True,
        ),
    ]


class SamplerField(BaseField):
    """Dropdown over the sampler registry (`sampler_registry`)."""

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        config = field_info["configuration"]

        options = sampler_registry.select(
            family=config.get("family") or None,
            include=config.get("include") or None,
            exclude=config.get("exclude") or None,
        )
        schema["options"] = [{"label": d.label, "value": d.key} for d in options]
        schema["configuration"] = {"allow_empty": config.get("allow_empty", False)}
        return schema

    def can_handle(self, field_type: str) -> bool:
        return field_type == "sampler"

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        return _configuration_specs()

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether a selection is required",
                param_type=bool,
                example=True,
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Family-scoped sampler picker",
                description="Samplers registered for the 'flux' family",
                yaml_config="""type: sampler
name: sampler
label: Sampler
default: euler
configuration:
  family: flux""",
                rendered_output={
                    "type": "sampler",
                    "name": "sampler",
                    "title": "Sampler",
                    "default": "euler",
                    "options": [],
                },
            ),
        ]


class ScheduleField(BaseField):
    """Dropdown over the schedule registry (`schedule_registry`)."""

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        config = field_info["configuration"]

        options = schedule_registry.select(
            family=config.get("family") or None,
            include=config.get("include") or None,
            exclude=config.get("exclude") or None,
        )
        schema["options"] = [{"label": d.label, "value": d.key} for d in options]
        schema["configuration"] = {"allow_empty": config.get("allow_empty", False)}
        return schema

    def can_handle(self, field_type: str) -> bool:
        return field_type == "schedule"

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        return _configuration_specs()

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether a selection is required",
                param_type=bool,
                example=True,
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        return [
            FieldExampleSpec(
                title="Schedule picker",
                description="Schedules registered for the 'flux' family",
                yaml_config="""type: schedule
name: schedule
label: Schedule
default: simple
configuration:
  family: flux""",
                rendered_output={
                    "type": "schedule",
                    "name": "schedule",
                    "title": "Schedule",
                    "default": "simple",
                    "options": [],
                },
            ),
        ]
