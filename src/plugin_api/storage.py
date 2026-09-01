"""Keeping data.

`db` is the application's database. A plugin may create and own its own tables -
give them a name that starts with the plugin id so it is obvious whose they are -
and `generate_ulid()` mints sortable primary keys for them.

`PluginRepository` reads the settings an admin filled in against your manifest's
`settings:` section, including your credentials; `SettingRepository` and
`Settings` read application-wide settings. Do not read another plugin's
settings.
"""

from src.features.plugins.repository import PluginRepository
from src.platform.settings.repository import SettingRepository
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid

__all__ = [
    "PluginRepository",
    "SettingRepository",
    "Settings",
    "db",
    "generate_ulid",
]


def __getattr__(name):
    """`db` is resolved on access, not bound here at import time - a plugin
    importing this module before a test patches the process-default
    `Database` singleton would otherwise keep the pre-patch reference for the
    rest of the process."""
    if name == "db":
        from src.platform.database.database import db
        return db
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
