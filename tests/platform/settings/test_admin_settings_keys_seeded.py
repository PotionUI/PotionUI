import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.platform.database.database import Database
from src.platform.database.migration_runner import MigrationRunner

_GROUPS_FILE = (
    Path(__file__).resolve().parents[3]
    / "frontend" / "src" / "routes" / "admin" / "components" / "settings" / "settingsGroups.ts"
)


def _admin_setting_keys():
    source = _GROUPS_FILE.read_text(encoding="utf-8")
    body = source.split("SETTINGS_KEY_GROUP", 1)[1].split("= {", 1)[1].split("};", 1)[0]
    return re.findall(r"^\s*([A-Za-z0-9_]+)\s*:", body, re.MULTILINE)


class TestAdminSettingsKeysSeeded(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

    def tearDown(self):
        Database._instance = None

    def test_every_admin_settings_key_is_a_seeded_row(self):
        keys = _admin_setting_keys()
        self.assertGreater(len(keys), 20)

        with patch("src.platform.database.database.db", self.db), \
             patch("src.platform.database.migration_runner.db", self.db):
            MigrationRunner().run_migrations()

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT key FROM settings")
            seeded = {row["key"] for row in cursor.fetchall()}

        self.assertEqual(sorted(set(keys) - seeded), [])
