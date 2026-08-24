"""
Add settings for the gallery vision embedder.

Free-text visual search over the generation gallery embeds images and query
text into SigLIP's shared image/text space. These rows make the model, device
and download policy editable through PUT /api/settings/<key> (same pattern as
migrations 097/098).

Read by build_vision_embedder in src/features/media_index/vision_embedder.py,
constructed in src/bootstrap/container.py.
"""

from src.platform.database.database import db

_SETTINGS = [
    (
        "media_vision_model",
        "google/siglip-base-patch16-224",
        "string",
        "Hugging Face model id of the SigLIP checkpoint used for gallery visual search.",
    ),
    (
        "media_vision_device",
        "cpu",
        "string",
        "Device the gallery vision embedder runs on.",
    ),
    (
        "media_vision_auto_download",
        "true",
        "boolean",
        "Whether the gallery vision embedder may download its weights from "
        "Hugging Face Hub on first use.",
    ),
]


def up():
    """Seed the gallery vision-embedder settings."""
    with db.get_cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if not cursor.fetchone():
            return

        import random
        import string
        import time

        for key, value, value_type, description in _SETTINGS:
            timestamp = int(time.time() * 1000)
            randomness = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
            simple_id = f"{timestamp:013d}{randomness}"
            cursor.execute(
                """
                INSERT OR IGNORE INTO settings (
                    id, key, value, value_type, description, type
                ) VALUES (?, ?, ?, ?, ?, 'SYSTEM')
                """,
                [simple_id, key, value, value_type, description],
            )


def down():
    """Remove the gallery vision-embedder settings."""
    with db.get_cursor() as cursor:
        keys = ",".join("?" for _ in _SETTINGS)
        cursor.execute(
            f"DELETE FROM settings WHERE key IN ({keys})",
            [key for key, *_ in _SETTINGS],
        )
