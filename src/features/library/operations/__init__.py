"""
Library operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds the seven library collaborators together. Each operation is a
module-level function that takes a `LibraryCollaborators` bundle
(`src.features.library.collaborators`) as its leading argument, followed by
the operation's own parameters. `LibraryController` (`routes.py`) holds the
bundle and passes it in; nothing here is stored across calls.

Shape rule: one module per concern (`reads`, `curation`, `mutations`), plus
`guards` for the ownership/tag-validity preconditions shared across them -
each re-exported here as the public surface. Callers import from the package
(`from src.features.library import operations`), never from a submodule
directly.
"""
from src.features.library.operations.reads import (
    get_facets,
    get_item,
    get_tags,
    list_items,
)
from src.features.library.operations.curation import set_tags
from src.features.library.operations.mutations import (
    copy_generation_file,
    delete_item,
)

__all__ = [
    "list_items",
    "get_facets",
    "get_item",
    "get_tags",
    "set_tags",
    "delete_item",
    "copy_generation_file",
]
