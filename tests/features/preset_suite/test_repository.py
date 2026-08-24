"""PresetSuiteRepository.seed_ephemeral: the raw INSERT OR IGNORE / OR REPLACE
pair that seeds a fresh ephemeral DB before a headless preset-suite run.
`HeadlessGenerationClient._prepare_ephemeral_db` is the real caller (covered
end to end in test_headless_client.py); these are direct repository-level
tests of the seeding contract itself.
"""

from src.features.preset_suite.repository import PresetSuiteRepository


def test_seed_ephemeral_creates_admin_user_and_storage_setting(mock_db):
    repo = PresetSuiteRepository()
    repo.seed_ephemeral("preset-suite", "/tmp/run/storage")

    with mock_db.get_cursor() as cur:
        cur.execute("SELECT account_type FROM users WHERE id = ?", ("preset-suite",))
        user = cur.fetchone()
        cur.execute("SELECT value FROM settings WHERE key = 'file_storage_directory'")
        setting = cur.fetchone()

    assert user is not None and user["account_type"] == "ADMIN"
    assert setting is not None and setting["value"] == "/tmp/run/storage"


def test_seed_ephemeral_is_idempotent(mock_db):
    """A second seed (re-run) must not raise on the already-present user row,
    and the storage directory setting reflects the latest call."""
    repo = PresetSuiteRepository()
    repo.seed_ephemeral("preset-suite", "/tmp/run-1/storage")
    repo.seed_ephemeral("preset-suite", "/tmp/run-2/storage")

    with mock_db.get_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE id = ?", ("preset-suite",))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT value FROM settings WHERE key = 'file_storage_directory'")
        assert cur.fetchone()["value"] == "/tmp/run-2/storage"
