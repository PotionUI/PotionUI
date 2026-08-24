"""Allowlist of parameter names recorded to `generation_parameters` for display.

`generation_parameters` (written by `ParamGenerationOutputHandler`) is a
display-only projection of a generation's run - the full submitted form
always lives untouched in `generations.form_data`. Only the parameters a user
actually cares about when scanning history belong here; everything else a
preset's `param_emitter` happens to emit is dropped rather than recorded.

Extensible the same way `output_type_registry` is: a pipe or plugin module
can call `register_display_parameters` at import time to opt its own
`ParamGenerationOutput` names into display.
"""

from typing import Set

_DISPLAY_PARAMETER_NAMES: Set[str] = {
    # Reproducibility
    "seed", "segment_seed",
    # Prompts
    "positive_prompt", "negative_prompt",
    # Resolution / duration
    "resolution", "resolution_target", "duration", "duration_seconds", "fps", "frames",
    # Sampling
    "cfg", "cfg_scale", "ar_cfg_scale", "steps", "sampler", "scheduler", "clip_skip", "speed_profile",
    # Model / checkpoint
    "model",
    # Strength-type knobs
    "denoise", "refine_strength", "detail_change", "flow_scale", "strength", "scale", "scale_percent", "factor",
    # Krea-2 inline/standalone enhance pass
    "enhance", "enhance_detail", "upscale_by",
    # Maya audio
    "temperature",
}


def register_display_parameters(*names: str) -> None:
    """Extend the set of parameter names recorded to `generation_parameters`.

    Call from a pipe or plugin module to opt a custom `ParamGenerationOutput`
    name into generation history's display parameters.
    """
    _DISPLAY_PARAMETER_NAMES.update(names)


def is_display_parameter(name: str) -> bool:
    """Whether `name` is recorded to `generation_parameters` for display."""
    return name in _DISPLAY_PARAMETER_NAMES
