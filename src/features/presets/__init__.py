"""
Presets: the model configurations a user generates from.

A preset declares an engine, its modes, the forms a user fills in, and the
pipeline those answers render into. This package owns all of it: the loader and
the processor that turn YAML into a pipeline, `operations` (dispatching onto a
`PresetCollaborators` bundle - install, assign, list modes), the linter, the
schema, the repositories and the routes.

Only the loader and the processor are re-exported here. `operations` and
`PresetCollaborators` are imported from their own modules directly, because
they reach into the form and tag features, which reach back into this
package's `configuration` module: re-exporting them here would make importing
*any* preset module execute that round trip.
"""

from .loader import PresetTemplateLoader
from .processor import PresetProcessor

__all__ = [
    "PresetTemplateLoader",
    "PresetProcessor",
]
