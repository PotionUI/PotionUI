"""Preset requirement checking (see docs/presets.md "Requirements").

`contracts` is the checker contract (`RequirementChecker`, `RequirementResult`,
`RequirementContext`, ...), re-exported by `src.plugin_api.presets`.
`builtin` registers the five core checkers onto a
`src.platform.plugins.requirement_checkers.RequirementCheckerRegistry`.
`context_builder` gathers a `RequirementContext` from the live collaborators.
`evaluator` runs a preset's declared requirements against a context, with a
process-local cache in front of it.
"""

from src.features.presets.requirements.contracts import (
    RequirementAction,
    RequirementBackendInfo,
    RequirementChecker,
    RequirementContext,
    RequirementResult,
    RequirementStatus,
)
from src.features.presets.requirements.evaluator import (
    RequirementsCache,
    evaluate_preset_requirements,
    preset_requirements_fingerprint,
    summarize,
)

__all__ = [
    "RequirementAction",
    "RequirementBackendInfo",
    "RequirementChecker",
    "RequirementContext",
    "RequirementResult",
    "RequirementStatus",
    "RequirementsCache",
    "evaluate_preset_requirements",
    "preset_requirements_fingerprint",
    "summarize",
]
