"""
User administration operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes exactly the collaborators it needs (repository, password
hasher, plugin registry, settings manager) as leading arguments, followed by
the operation's own parameters. `UserController` (`routes.py`) holds the
collaborators and passes them in; nothing here is stored across calls.

Shape rule: one module per concern (`crud`, `avatar`), each re-exported here
as the public surface - split a module before it outgrows ~200 lines rather
than let it absorb an unrelated concern.
"""
from src.features.users.operations.crud import create, update, delete
from src.features.users.operations.avatar import (
    AVATAR_MAX_BYTES,
    AVATAR_ALLOWED_EXTENSIONS,
    upload_avatar,
    delete_avatar,
    delete_avatar_file,
    resolve_avatar_path,
)

__all__ = [
    "create",
    "update",
    "delete",
    "AVATAR_MAX_BYTES",
    "AVATAR_ALLOWED_EXTENSIONS",
    "upload_avatar",
    "delete_avatar",
    "delete_avatar_file",
    "resolve_avatar_path",
]
