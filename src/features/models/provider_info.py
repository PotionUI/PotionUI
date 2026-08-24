"""Fetches marketplace metadata for indexed models via the provider registry."""

import logging
from typing import Any, Dict, List, Optional

from src.features.models.exceptions import ProviderFetchException
from src.platform.plugins.hooks import execute_hook
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.models.records import ModelInfo
from src.features.models.repository import ModelRepository
from src.platform.plugins import PluginRegistry

logger = logging.getLogger(__name__)


class ProviderInfoFetcher:
    """Enriches models with provider metadata, matched by SHA256 through the registry.

    A provider (e.g. civitai) is a plugin; this class asks the provider registry
    to resolve each model's hash to marketplace metadata and stores the result.
    """

    def __init__(self, model_repository: ModelRepository, plugin_registry: PluginRegistry):
        self.model_repo = model_repository
        self.plugins = plugin_registry

    def fetch_provider_info(
        self,
        provider: str,
        model_ids: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """Validate the provider and announce a background fetch.

        Fires model_index.before_fetch_info (can block). Raises
        ProviderFetchException if the provider is missing, not initialised, or vetoed.
        """
        from src.features.providers.registry import get_provider_registry

        provider_registry = get_provider_registry()
        provider_instance = provider_registry.get_provider(provider)

        if not provider_instance:
            raise ProviderFetchException(
                f"Provider '{provider}' not found. Please install and configure the provider plugin in Providers settings."
            )

        if not provider_registry.is_provider_initialized(provider):
            raise ProviderFetchException(
                f"Provider '{provider}' is not initialized. Please configure API key and other settings in Providers settings."
            )

        hook_data, blocked = execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.before_fetch_info,
            {
                "provider": provider,
                "model_ids": model_ids,
                "force_refresh": force_refresh
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Provider fetch blocked by plugin")
            raise ProviderFetchException(reason)

        logger.info(f"Using provider plugin for {provider}")

        return {
            "message": f"{provider.title()} info fetch started in background",
            "status": "running",
            "provider": provider,
            "models_to_process": len(model_ids) if model_ids else "all_without_info"
        }

    async def run_provider_fetch(
        self,
        provider: str,
        model_ids: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> None:
        """Execute the actual provider fetch (background task); fires after_fetch_info."""
        try:
            from src.features.providers.registry import get_provider_registry

            provider_registry = get_provider_registry()

            # Get models to process
            if model_ids:
                models = [self.model_repo.get_by_id(mid, include_providers=False) for mid in model_ids]
                models = [m for m in models if m]
            elif force_refresh:
                models = self.model_repo.get_all(include_providers=False, limit=None)
            else:
                models = self.model_repo.get_models_without_provider_info(provider)

            successful = 0
            failed = 0

            for model in models:
                if getattr(model, 'is_directory', False):
                    # HF-layout checkpoints are local-only for now: their
                    # `sha256` is a directory fingerprint, not a content hash a
                    # marketplace could ever match.
                    logger.debug(f"Model {model.id} is an HF-layout directory, skipping provider fetch")
                    continue

                if not model.sha256:
                    logger.warning(f"Model {model.id} has no SHA256 hash, skipping")
                    failed += 1
                    continue

                try:
                    model_info = await provider_registry.get_model_by_hash(
                        provider, model.sha256
                    )
                    if model_info:
                        # Store provider info in database
                        db_model_info = ModelInfo(
                            model_id=model.id,
                            provider=model_info.provider_id,
                            provider_model_id=model_info.provider_model_id,
                            provider_version_id=model_info.provider_version_id,
                            name=model_info.name,
                            description=model_info.description,
                            tags=model_info.tags,
                            nsfw=model_info.nsfw,
                            download_url=model_info.download_url,
                        )
                        self.model_repo.upsert_provider(model.id, db_model_info)
                        successful += 1
                    else:
                        failed += 1
                except Exception as e:
                    logger.error(f"Error fetching info for model {model.id}: {e}")
                    failed += 1

            logger.info(f"Provider fetch completed: {successful} successful, {failed} failed")

            execute_hook(
                self.plugins,
                MODEL_INDEX_HOOKS.after_fetch_info,
                {
                    "provider": provider,
                    "successful": successful,
                    "failed": failed
                }
            )

        except Exception as e:
            logger.error(f"Error during background provider fetch: {e}")
