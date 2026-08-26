"""
Segment domain operations: saved Segments, Segment Templates, and categories.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes exactly the repositories/plugin registry it needs as
leading arguments. `SegmentController` (`routes.py`) and the chat/MCP tool
surface hold the repositories and pass them in; nothing here is stored
across calls.

Shape rule: one module per concern (`reads`, `categories`, `segments`,
`templates`), each re-exported here as the public surface.
"""
from src.features.segments.operations.reads import get_category, get_segment, get_template
from src.features.segments.operations.categories import (
    create_category,
    update_category,
    delete_category,
)
from src.features.segments.operations.segments import (
    create_segment,
    update_segment,
    delete_segment,
)
from src.features.segments.operations.templates import (
    create_template,
    update_template,
    delete_template,
)

__all__ = [
    "get_category",
    "get_segment",
    "get_template",
    "create_category",
    "update_category",
    "delete_category",
    "create_segment",
    "update_segment",
    "delete_segment",
    "create_template",
    "update_template",
    "delete_template",
]
