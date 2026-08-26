"""
Stats operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes exactly the repositories it needs as leading arguments.
`StatsController` (`routes.py`) and `GenerationOrchestrator` hold the
repositories and pass them in; nothing here is stored across calls.

Shape rule: one module per concern (`reads`, `completion`), each re-exported
here as the public surface.
"""
from src.features.stats.operations.reads import (
    breakdown,
    dimensions,
    storage,
    timeseries,
)
from src.features.stats.operations.completion import record_completion

__all__ = [
    "breakdown",
    "dimensions",
    "storage",
    "timeseries",
    "record_completion",
]
