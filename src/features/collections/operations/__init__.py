"""
Collection administration operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes the `CollectionRepository` as its leading argument,
followed by the operation's own parameters. `CollectionController`
(`routes.py`), the `manage_collections` chat/MCP tool, and
`GenerationOrchestrator`'s auto-tagging path all hold a `CollectionRepository`
and call these directly; nothing here is stored across calls.

`get_collection` isn't a route (there's no `GET /{collection_id}`) - it's the
shared "resolve + enforce ownership/scope, or raise" building block every
mutation below needs, and the one several outside callers (the
`manage_collections` tool, MCP) also reach for directly before previewing a
change. `list_collections` has no such logic (`repository.list(user_id,
scope)` verbatim) so callers use the repository straight, with no operations
wrapper.

Shape rule: one module per concern (`reads`, `crud`, `move`, `members`), each
re-exported here as the public surface - split a module before it outgrows
~200 lines rather than let it absorb an unrelated concern. Callers import
from the package (`from src.features.collections import operations`), never
from a submodule directly.
"""
from src.features.collections.operations.reads import get_collection
from src.features.collections.operations.crud import (
    create_collection,
    rename_collection,
    delete_collection,
)
from src.features.collections.operations.move import move_collection, bulk_move_collections
from src.features.collections.operations.members import (
    add_members,
    remove_members,
    add_upload_members,
    remove_upload_members,
    add_prompt_members,
    remove_prompt_members,
)

__all__ = [
    "get_collection",
    "create_collection",
    "rename_collection",
    "delete_collection",
    "move_collection",
    "bulk_move_collections",
    "add_members",
    "remove_members",
    "add_upload_members",
    "remove_upload_members",
    "add_prompt_members",
    "remove_prompt_members",
]
