"""Keeping data.

`db` is the application's database. A plugin may create and own its own tables -
give them a name that starts with the plugin id so it is obvious whose they are -
and `generate_ulid()` mints sortable primary keys for them.

`PluginRepository` reads the settings an admin filled in against your manifest's
`settings:` section, including your credentials; `SettingRepository` and
`SettingsManager` read application-wide settings. Do not read another plugin's
settings.
"""

from src.features.plugins.repository import PluginRepository
from src.platform.settings.repository import SettingRepository
from src.platform.database.database import db
from src.platform.settings.settings import SettingsManager
from src.platform.util.ids import generate_ulid

__all__ = [
    "PluginRepository",
    "SettingRepository",
    "SettingsManager",
    "db",
    "generate_ulid",
]
