"""Persistence for the preset test suite's ephemeral database.

Raw SQL against the (ephemeral) singleton: the settings/user rows may not
exist yet in a fresh schema, and the settings feature's own repository only
UPDATEs existing rows.
"""


class PresetSuiteRepository:
    def seed_ephemeral(self, user_id: str, storage_directory: str, *, db=None) -> None:
        """Create the suite user row and point `file_storage_directory` at
        `storage_directory`, so a real run is fully isolated from the user's
        data. Both statements commit together - see `_prepare_ephemeral_db`.

        `db` is a caller-supplied handle, not resolved by import: a full test
        run showed this write can land on the process-default `Database`
        singleton instead of the ephemeral one even with the import deferred
        to call time - some other test in the same session had already left
        the default singleton in a state where the "current" `db` wasn't the
        one this caller thought it was. Taking `db` explicitly removes that
        dependency on import order entirely. `None` (the real caller's case)
        falls back to the process-default singleton.
        """
        if db is None:
            from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT OR IGNORE INTO users (id, username, email, password_hash, account_type)
                VALUES (?, ?, ?, ?, 'ADMIN')
                """,
                [user_id, user_id, f"{user_id}@preset-suite.local", "x"],
            )
            cursor.execute(
                """
                INSERT OR REPLACE INTO settings (id, key, value, value_type, description, type)
                VALUES ('setting_file_storage_directory', 'file_storage_directory', ?, 'string',
                        'Preset test suite: ephemeral per-run storage', 'SYSTEM')
                """,
                [storage_directory],
            )


preset_suite_repo = PresetSuiteRepository()
