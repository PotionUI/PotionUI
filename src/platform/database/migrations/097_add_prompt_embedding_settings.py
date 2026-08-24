"""
Add settings for the prompt-embedding provider.

Semantic prompt search used to hard-require an external Ollama server. It now
defaults to an in-process transformers encoder (LocalEmbeddingProvider);
Ollama remains available as an opt-in alternative. These rows exist so the
choice is editable through PUT /api/settings/<key> without the
setting_not_found error migration 080 already documented for this pattern.

Read by SettingsManager.get_setting (no dedicated convenience getters yet) in
src/bootstrap/container.py, where the provider is constructed.
"""

from src.platform.database.database import db

_SETTINGS = [
    (
        "prompt_embedding_provider",
        "local",
        "string",
        "Prompt-embedding backend for semantic prompt search: "
        "'local' (in-process transformers, no external service) or 'ollama'.",
    ),
    (
        "prompt_embedding_model",
        "BAAI/bge-small-en-v1.5",
        "string",
        "Hugging Face model id used by the local prompt-embedding provider.",
    ),
    (
        "prompt_embedding_device",
        "cpu",
        "string",
        "Device the local prompt-embedding model runs on.",
    ),
    (
        "prompt_embedding_auto_download",
        "true",
        "boolean",
        "Whether the local prompt-embedding provider may download its model "
        "weights from Hugging Face Hub on first use.",
    ),
    (
        "prompt_embedding_ollama_base_url",
        "http://localhost:11434",
        "string",
        "Base URL of the Ollama server used when prompt_embedding_provider is 'ollama'.",
    ),
    (
        "prompt_embedding_ollama_model",
        "nomic-embed-text",
        "string",
        "Ollama model used when prompt_embedding_provider is 'ollama'.",
    ),
]


def up():
    """Seed the prompt-embedding settings (default provider: local)."""
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
    """Remove the prompt-embedding settings."""
    with db.get_cursor() as cursor:
        keys = ",".join("?" for _ in _SETTINGS)
        cursor.execute(
            f"DELETE FROM settings WHERE key IN ({keys})",
            [key for key, *_ in _SETTINGS],
        )
