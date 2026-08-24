"""
Presets: the model configurations a user generates from.

A preset declares an engine, its modes, the forms a user fills in, and the
pipeline those answers render into. This package owns all of it: the loader and
the processor that turn YAML into a pipeline, the manager that owns the
operations on presets (install, assign, list modes), the linter, the schema, the
repositories and the routes.

Only the loader and the processor are re-exported here. `PresetManager` is
imported from `src.features.presets.manager` directly, because it reaches into
the form and tag features, which reach back into this package's `configuration`
module: re-exporting it would make importing *any* preset module execute that
round trip.
"""

from .loader import PresetTemplateLoader
from .processor import PresetProcessor

__all__ = [
    "PresetTemplateLoader",
    "PresetProcessor",
]
