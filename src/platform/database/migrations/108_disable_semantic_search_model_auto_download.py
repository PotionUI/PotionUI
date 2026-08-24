"""
Disable silent auto-download for the semantic-search / media-indexing models.

The admin settings pane for these three assets (prompt-embedding, media
tagger, media vision-embedder - migrations 097/098/099) already surfaces an
explicit Fetch action, but the auto-download toggles it also exposes all
defaulted to `true`: the first search/tag/embed call would silently pull the
weights from Hugging Face Hub in the background instead of the admin ever
seeing a fetch happen. That's the opposite of the explicit-Fetch design the
pane exists for, so this flips the policy to opt-in.

Any admin who already flipped one of these back to `true` themselves has
their choice discarded here too - the point is that silent first-use download
is no longer the shipped default for anyone.

Read by build_embedding_provider (src/features/prompt_database/embedding.py),
build_tagger_provider and build_vision_embedder
(src/features/media_index/tagger.py / vision_embedder.py).
"""

from src.platform.database.database import db

_KEYS = (
    "prompt_embedding_auto_download",
    "media_tagger_auto_download",
    "media_vision_auto_download",
)


def up():
    """Flip the three auto-download settings to `false`."""
    with db.get_cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if not cursor.fetchone():
            return

        placeholders = ",".join("?" for _ in _KEYS)
        cursor.execute(
            f"UPDATE settings SET value = 'false' WHERE key IN ({placeholders})",
            _KEYS,
        )


def down():
    """Restore the original `true` default."""
    with db.get_cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if not cursor.fetchone():
            return

        placeholders = ",".join("?" for _ in _KEYS)
        cursor.execute(
            f"UPDATE settings SET value = 'true' WHERE key IN ({placeholders})",
            _KEYS,
        )
