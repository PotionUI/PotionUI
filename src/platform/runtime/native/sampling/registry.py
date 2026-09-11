"""Re-exports the sampler/schedule registries for engine-side callers.

The definitions live at ``src.platform.plugins.sampling`` -- a torch-free
module -- so that code which only needs the catalog (form field types, the
preset linter, the sampling catalog route, application boot) can import it
without pulling the native engine (and therefore torch). This module keeps
the historical import path working for ``denoise_loop``, ``flow_schedule``,
``conditioned`` and ``engine``, which already depend on torch.
"""

from __future__ import annotations

from src.platform.plugins.sampling import (
    ANY_FAMILY,
    DuplicateSamplingEntryError,
    OptionSpec,
    SamplerDefinition,
    SamplingRegistry,
    ScheduleContext,
    ScheduleDefinition,
    sampler_registry,
    schedule_registry,
)

__all__ = [
    "ANY_FAMILY",
    "DuplicateSamplingEntryError",
    "OptionSpec",
    "SamplerDefinition",
    "SamplingRegistry",
    "ScheduleContext",
    "ScheduleDefinition",
    "sampler_registry",
    "schedule_registry",
]
