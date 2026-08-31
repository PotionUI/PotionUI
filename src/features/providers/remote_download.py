"""Resolves a model's linked provider into a URL a remote worker can download without credentials."""

from src.features.models.repository import ModelRepository
from src.features.providers.base_provider import ProviderCapability, RemoteDownloadRef
from src.features.providers.registry import ProviderRegistry


class RemoteDownloadResolutionError(Exception):
    """Base for remote-download resolution failures."""


class ModelNotLinkedError(RemoteDownloadResolutionError):
    """The model has no usable provider link."""


class ProviderCapabilityMissingError(RemoteDownloadResolutionError):
    """The linked provider cannot resolve a credential-free URL."""


class ProviderResolutionFailedError(RemoteDownloadResolutionError):
    """The linked provider raised while resolving the download."""


async def resolve_model_remote_download(
    model_repository: ModelRepository,
    provider_registry: ProviderRegistry,
    model_id: str,
) -> RemoteDownloadRef:
    """Given a model id, resolve its linked provider's download to a `RemoteDownloadRef`."""
    model = model_repository.get_by_id(model_id, include_providers=True)
    if not model or not model.providers:
        raise ModelNotLinkedError(f"Model {model_id} has no linked provider")

    link = model.providers[0]
    if not link.provider_model_id:
        raise ModelNotLinkedError(f"Model {model_id}'s provider link has no provider_model_id")

    provider = provider_registry.get_provider(link.provider)
    if not provider:
        raise ModelNotLinkedError(f"Provider '{link.provider}' linked to model {model_id} is not installed")

    if not provider.get_metadata().has_capability(ProviderCapability.REMOTE_DOWNLOAD):
        raise ProviderCapabilityMissingError(
            f"Provider '{link.provider}' does not support remote download resolution"
        )

    try:
        return await provider.resolve_remote_download(link.provider_model_id, link.provider_version_id)
    except NotImplementedError as exc:
        raise ProviderCapabilityMissingError(str(exc)) from exc
    except RemoteDownloadResolutionError:
        raise
    except Exception as exc:
        raise ProviderResolutionFailedError(str(exc)) from exc
