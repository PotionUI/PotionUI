"""
Model library collection operations.

Post-Manager reference shape (see `src.features.plugins.operations`, and
`src.features.collections.operations` for the closest sibling - model
collections mirror generation/library collections minus the multi-scope
split). No class holds these collaborators together. Each operation is a
module-level function that takes the `ModelCollectionRepository` as its
leading argument, followed by the operation's own parameters.
`ModelCollectionController` (`routes.py`) holds the repository and calls
these directly; nothing here is stored across calls.

`get_collection` isn't a route (there's no `GET /{collection_id}`) - it's the
shared "resolve + enforce ownership, or raise" building block every mutation
below needs. `list_collections` has no such logic (`repository.list(user_id)`
verbatim) so the controller uses the repository straight, with no operations
wrapper. The per-user favorite/custom-name overlay on models
(`UserModelMetaRepository.set_favorite`/`set_custom_name`) has no validation
logic either (beyond a name trim `ModelController` does inline) - it isn't
part of this package.

Shape rule: one module per concern (`reads`, `crud`, `move`, `members`), each
re-exported here as the public surface - split a module before it outgrows
~200 lines rather than let it absorb an unrelated concern. Callers import
from the package (`from src.features.model_library import operations`),
never from a submodule directly.
"""
from src.features.model_library.operations.reads import get_collection
from src.features.model_library.operations.crud import (
    create_collection,
    rename_collection,
    delete_collection,
)
from src.features.model_library.operations.move import move_collection, bulk_move_collections
from src.features.model_library.operations.members import add_members, remove_members

__all__ = [
    "get_collection",
    "create_collection",
    "rename_collection",
    "delete_collection",
    "move_collection",
    "bulk_move_collections",
    "add_members",
    "remove_members",
]
