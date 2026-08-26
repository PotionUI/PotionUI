"""
Inspirations operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds the eleven inspirations collaborators together. Each operation is
a module-level function that takes an `InspirationCollaborators` bundle
(`src.features.inspirations.collaborators`) as its leading argument, followed
by the operation's own parameters. `InspirationController` (`routes.py`)
holds the bundle and passes it in; nothing here is stored across calls. GET
handlers still read straight from `collaborators.repository` in the
controller - only mutations go through these functions.

Shape rule: one module per concern (`publishing`, `comments`, `saves`,
`collections`) - each re-exported here as the public surface. Callers import
from the package (`from src.features.inspirations import operations`), never
from a submodule directly.
"""
from src.features.inspirations.operations.publishing import publish, delete
from src.features.inspirations.operations.comments import add_comment, delete_comment
from src.features.inspirations.operations.saves import save_to_library, unsave
from src.features.inspirations.operations.collections import (
    create_collection,
    update_collection,
    delete_collection,
    add_item,
    remove_item,
)

__all__ = [
    "publish",
    "delete",
    "add_comment",
    "delete_comment",
    "save_to_library",
    "unsave",
    "create_collection",
    "update_collection",
    "delete_collection",
    "add_item",
    "remove_item",
]
