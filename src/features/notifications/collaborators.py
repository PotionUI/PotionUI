"""Frozen collaborators bundle for the notifications operations layer.

`notify()` and the preferences/mutation operations all need the same five
infrastructure legs together: the notification repository, the user
repository (for broadcast fan-out and admin checks), the plugin registry
(before/after_create hooks), the websocket connection manager (real-time
push), and settings (stored preferences). Bundling them once here - built in
the composition root and passed to `operations` functions and to
`NotificationController` as a single object - avoids threading five
positional collaborators through every call site. A plain, frozen data
holder (no behavior beyond field access), matching `PromptDatabaseCollaborators`
(see `src.features.prompt_database.collaborators` - the reference shape for a
wide-collaborator dissolution).
"""
from dataclasses import dataclass

from src.features.notifications.repository import NotificationRepository
from src.features.users.repository import UserRepository
from src.platform.plugins import PluginRegistry
from src.platform.settings.settings import Settings
from src.platform.websocket.notification_connection_hub import NotificationConnectionHub


@dataclass(frozen=True)
class NotificationCollaborators:
    repository: NotificationRepository
    users: UserRepository
    plugins: PluginRegistry
    connections: NotificationConnectionHub
    settings: Settings
