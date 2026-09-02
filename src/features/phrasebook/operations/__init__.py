"""
Phrasebook domain operations: categories, values, and search.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes exactly the repositories/plugin registry it needs as
leading arguments. `PhrasebookController` (`routes.py`) and the chat/MCP tool
surface hold the repositories and pass them in; nothing here is stored
across calls.

Shape rule: one module per concern (`reads`, `categories`, `values`,
`search`, `find`, `batch`, `matching`), each re-exported here as the public
surface.
"""
from src.features.phrasebook.operations.reads import get_category, get_value
from src.features.phrasebook.operations.categories import (
    create_category,
    update_category,
    delete_category,
    toggle_category_active,
)
from src.features.phrasebook.operations.values import (
    create_value,
    update_value,
    delete_value,
    toggle_value_active,
    attach_preview_image,
)
from src.features.phrasebook.operations.search import search_phrasebook
from src.features.phrasebook.operations.find import (
    find_phrasebook,
    parse_fields,
    InvalidFields,
)
from src.features.phrasebook.operations.matching import InvalidPattern
from src.features.phrasebook.operations.batch import (
    BatchError,
    preview_replace,
    replace_values,
    set_values_active,
    move_values,
    delete_values,
)
from src.features.phrasebook.operations.batch_context import RepositoryBatchContext
from src.features.phrasebook.operations.core_ops import register_core_batch_operations

__all__ = [
    "get_category",
    "get_value",
    "create_category",
    "update_category",
    "delete_category",
    "toggle_category_active",
    "create_value",
    "update_value",
    "delete_value",
    "toggle_value_active",
    "attach_preview_image",
    "search_phrasebook",
    "find_phrasebook",
    "parse_fields",
    "InvalidFields",
    "InvalidPattern",
    "BatchError",
    "preview_replace",
    "replace_values",
    "set_values_active",
    "move_values",
    "delete_values",
    "RepositoryBatchContext",
    "register_core_batch_operations",
]
