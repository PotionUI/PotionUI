"""
Session administration operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no class
holds these collaborators together. Each operation is a module-level function
that takes exactly the collaborators it needs (session_repository,
plugin_registry, and - only for version history - the optional
session_version_repository/file_preset_repository) as leading arguments,
followed by the operation's own parameters. `SessionController` (`routes.py`)
holds the collaborators and passes them in; nothing here is stored across
calls.

Shape rule: one module per concern (`save`, `delete`), each re-exported here
as the public surface - split a module before it outgrows ~200 lines rather
than let it absorb an unrelated concern. Callers import from the package
(`from src.features.sessions.operations import save_session`), never from a
submodule directly.
"""
from src.features.sessions.operations.save import save_session, update_session
from src.features.sessions.operations.delete import delete_session

__all__ = [
    "save_session",
    "update_session",
    "delete_session",
]
