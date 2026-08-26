"""
Tag administration operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes the `TagRepository`/`PluginRegistry` (and, for delete,
the preset repositories a used-by-preset check needs) as leading arguments,
followed by the operation's own parameters. `TagController` (`routes.py`) and
the `organize_gallery` chat tool hold these collaborators and call the
functions directly; nothing here is stored across calls.

`get_tags`/`search_tags` have no logic beyond
`src.features.tags.dto.effective_user_id_for_type` + a repository call, so
callers use the repository (and that helper) directly, with no operations
wrapper.

Shape rule: one module per concern - currently just `crud` (create/update/
delete), small enough for a single module. Split it before it outgrows ~200
lines rather than let a second concern move in. Callers import from the
package (`from src.features.tags import operations`), never from a submodule
directly.
"""
from src.features.tags.operations.crud import create_tag, update_tag, delete_tag

__all__ = [
    "create_tag",
    "update_tag",
    "delete_tag",
]
