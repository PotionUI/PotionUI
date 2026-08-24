"""
Migration 098: system tags + the reusable media index queue.

``media_system_tags`` stores what a local auto-tagger said about a media file:
one row per (file, tag) with a confidence, a ``category`` ('general' /
'character' / 'rating') and a ``provenance`` (slug of the producing model).
Rating scores share the table as ``category='rating'`` rows (tag names:
general / sensitive / questionable / explicit, all four always written) -
same provenance semantics, no separate schema. Provenance is load-bearing:
switching tagger models re-tags by deleting rows of the old provenance and
requeueing those files. ``generation_id`` is denormalized alongside
``file_id`` so history filters need no extra join hop.

``media_index_queue`` is the shared pending queue for per-file index passes.
``pass_type`` is open-ended: 'tags' today, later passes (e.g. a CLIP
gallery-search embedding pass) reuse the same rows/drain loop. One row per
(file, pass); attempts count toward a failed terminal state.

Settings: tagger model/device/download/thresholds are SYSTEM; the NSFW blur
toggle is a USER setting so each user can override it through the existing
per-user settings mechanism (PUT /api/settings/<key>).
"""

from src.platform.database.database import db

_SETTINGS = [
    (
        "media_tagger_model",
        "SmilingWolf/wd-vit-tagger-v3",
        "string",
        "Hugging Face model id of the local WD tagger that produces system tags.",
        "SYSTEM",
    ),
    (
        "media_tagger_device",
        "cpu",
        "string",
        "Device the tagger model runs on.",
        "SYSTEM",
    ),
    (
        "media_tagger_auto_download",
        "true",
        "boolean",
        "Whether the tagger may download its weights from Hugging Face Hub on first use.",
        "SYSTEM",
    ),
    (
        "media_tagger_tag_threshold",
        "0.35",
        "float",
        "Minimum confidence for a general tag to be stored as a system tag.",
        "SYSTEM",
    ),
    (
        "media_tagger_character_threshold",
        "0.75",
        "float",
        "Minimum confidence for a character tag to be stored as a system tag.",
        "SYSTEM",
    ),
    (
        "media_nsfw_blur_threshold",
        "0.6",
        "float",
        "Blur a gallery item when its questionable + explicit rating scores reach this value.",
        "SYSTEM",
    ),
    (
        "media_nsfw_blur_enabled",
        "true",
        "boolean",
        "Blur gallery media the tagger rated as NSFW (click to reveal). Per-user preference.",
        "USER",
    ),
]


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS media_system_tags (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                generation_id TEXT,
                tag TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                confidence REAL NOT NULL,
                provenance TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
                UNIQUE (file_id, category, tag)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_system_tags_file ON media_system_tags (file_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_system_tags_tag ON media_system_tags (tag)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_system_tags_generation ON media_system_tags (generation_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_system_tags_provenance ON media_system_tags (provenance)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS media_index_queue (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                pass_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'done', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
                UNIQUE (file_id, pass_type)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_index_queue_drain "
            "ON media_index_queue (pass_type, status, created_at)"
        )

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if not cursor.fetchone():
            return

        import random
        import string
        import time

        for key, value, value_type, description, setting_type in _SETTINGS:
            timestamp = int(time.time() * 1000)
            randomness = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
            simple_id = f"{timestamp:013d}{randomness}"
            cursor.execute(
                """
                INSERT OR IGNORE INTO settings (
                    id, key, value, value_type, description, type
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [simple_id, key, value, value_type, description, setting_type],
            )


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS media_index_queue")
        cursor.execute("DROP TABLE IF EXISTS media_system_tags")
        keys = ",".join("?" for _ in _SETTINGS)
        cursor.execute(
            f"DELETE FROM settings WHERE key IN ({keys})",
            [key for key, *_ in _SETTINGS],
        )
