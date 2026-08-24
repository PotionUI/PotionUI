"""
Migration 122: settings for the optional S3 file storage backend.

`storage_backend` selects between `'local'` (default, unchanged behavior) and
`'s3'`. The `s3_*` settings configure the S3 (or S3-compatible: MinIO, R2, ...)
target; `s3_secret_key` is written pre-encrypted by
`StorageSettingsManager.set_s3_secret_key` - never through the generic
settings endpoint - so no plaintext secret is ever expected here.

Switching `storage_backend` only affects new writes (see `docs/s3-storage.md`
follow-up); existing local files are not migrated by this change.
"""

import time
import random
import string

from src.platform.database.database import db

_SETTINGS = [
    ("storage_backend", "local", "string",
     "Where new file-storage writes go: 'local' (default) or 's3'."),
    ("s3_bucket", "", "string", "S3 bucket name for the optional S3 storage backend."),
    ("s3_prefix", "", "string", "Key prefix inside the S3 bucket (no leading/trailing slash)."),
    ("s3_endpoint_url", "", "string",
     "S3-compatible endpoint URL (MinIO, Cloudflare R2, ...). Empty uses AWS S3."),
    ("s3_region", "us-east-1", "string", "S3 region."),
    ("s3_access_key_id", "", "string", "S3 access key ID."),
    ("s3_secret_key", "", "string", "S3 secret access key, stored encrypted."),
    ("s3_path_style", "false", "boolean",
     "Use path-style addressing (bucket in the URL path). Required by most non-AWS S3-compatible services."),
]


def _simple_id() -> str:
    timestamp = int(time.time() * 1000)
    randomness = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    return f"{timestamp:013d}{randomness}"


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if not cursor.fetchone():
            return

        for key, default_value, value_type, description in _SETTINGS:
            cursor.execute(
                """
                INSERT OR IGNORE INTO settings (
                    id, key, value, value_type, description, type
                ) VALUES (?, ?, ?, ?, ?, 'SYSTEM')
                """,
                [_simple_id(), key, default_value, value_type, description],
            )
        print(f"Migration 122: added {len(_SETTINGS)} S3 storage settings")


def down():
    with db.get_cursor() as cursor:
        keys = [key for key, *_ in _SETTINGS]
        cursor.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in keys])
        print(f"Migration 122: removed {len(keys)} S3 storage settings")
