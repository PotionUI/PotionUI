"""Builds a `ModelResolver` against the LIVE models table, read-only.

Shared by `scripts/preset_test_suite.py` and `scripts/preset_styles_render.py`,
called before either re-points the DB singleton at its own ephemeral copy.
"""

from __future__ import annotations

from pathlib import Path

from src.features.preset_suite.resolver import ModelResolver


def build_live_resolver(allow_download: bool = False) -> ModelResolver:
    from src.platform.settings.settings import Settings
    from src.features.models.repository import ModelRepository
    from src.platform.settings.repository import SettingRepository

    settings = Settings(SettingRepository())
    models_dir = settings.get_models_dir()
    repo = ModelRepository()

    sha_index: dict = {}
    try:
        for m in repo.get_all(include_providers=False, include_tags=False):
            sha = (getattr(m, "sha256", None) or "").strip().lower()
            file_path = getattr(m, "file_path", None)
            if sha and file_path:
                sha_index[sha] = file_path
    except Exception as e:  # noqa: BLE001 - a snapshot miss just falls back to hash-walk
        print(f"warning: could not snapshot the live models index ({e}); "
              "model resolution will fall back to hashing the models tree.")

    download_queue = None
    if allow_download:
        from src.features.downloads.queue import DownloadQueue
        from src.features.downloads.repository import DownloadRepository
        from src.platform.plugins import PluginRegistry
        from src.platform.websocket.download_connection_hub import DownloadConnectionHub

        download_queue = DownloadQueue(
            download_repository=DownloadRepository(),
            plugin_registry=PluginRegistry(),
            settings=settings,
            connection_hub=DownloadConnectionHub(),
        )

    return ModelResolver(
        models_dir,
        model_repository=repo,
        sha_index=sha_index,
        cache_path=Path("storage") / "model_hash_cache.json",
        allow_download=allow_download,
        download_queue=download_queue,
    )
