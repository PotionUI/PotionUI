"""Resolves a model into a URL a remote worker can download without credentials.

Prefers the model's linked provider; falls back to a by-hash lookup across
every registered provider that supports one when the model has no usable
link (most scanned models never get one). The by-hash path hashes the file
on demand and persists the digest, same as the pipeline-bundling path in
`model_bundle_builder.resolve_bundle_entry` - kept as a local helper here to
avoid this feature depending on `remote_execution`.
"""

import hashlib
from pathlib import Path
from typing import Optional

from src.features.models.records import Model, ModelInfo as ModelProviderLink
from src.features.models.repository import ModelRepository
from src.features.providers.base_provider import ProviderCapability, RemoteDownloadRef
from src.features.providers.registry import ProviderRegistry

_HASH_CHUNK_BYTES = 1024 * 1024

#: Headers a provider's `prepare_download` may leave behind that carry no
#: credential and are safe to forward to a remote worker. Anything else
#: remaining is treated as a credential and refused.
_BENIGN_DOWNLOAD_HEADERS = frozenset({
    "user-agent",
    "accept",
    "accept-encoding",
    "accept-language",
    "referer",
})


class RemoteDownloadResolutionError(Exception):
    """Base for remote-download resolution failures."""


class ModelNotLinkedError(RemoteDownloadResolutionError):
    """The model has no usable provider link and no provider can resolve it by hash."""


class ProviderCapabilityMissingError(RemoteDownloadResolutionError):
    """The linked provider cannot resolve a credential-free URL."""


class ProviderResolutionFailedError(RemoteDownloadResolutionError):
    """The linked provider raised while resolving the download."""


class RemoteDownloadSizeMismatchError(RemoteDownloadResolutionError):
    """The resolved download's reported size contradicts the model row's recorded size."""


def providers_support_hash_lookup(provider_registry: ProviderRegistry) -> bool:
    """Cheap capability check, no network and no model lookup - true if any
    registered provider can resolve a remote download from a hash alone."""
    return bool(provider_registry.get_providers_with_capability(ProviderCapability.REMOTE_DOWNLOAD_BY_HASH))


async def resolve_model_remote_download(
    model_repository: ModelRepository,
    provider_registry: ProviderRegistry,
    model_id: str,
) -> RemoteDownloadRef:
    """Given a model id, resolve a credential-free download URL: via its
    linked provider when it has one, otherwise via a by-hash lookup across
    every registered provider that supports it."""
    model = model_repository.get_by_id(model_id, include_providers=True)
    if not model:
        raise ModelNotLinkedError(f"Model {model_id} has no linked provider")

    link = _usable_link(model)
    if link is not None:
        ref = await _resolve_via_link(provider_registry, model_id, link)
    else:
        ref = await _resolve_via_hash(model_repository, provider_registry, model_id, model)

    _check_size(model, ref)
    return ref


def _usable_link(model: Model) -> Optional[ModelProviderLink]:
    for link in model.providers:
        if link.provider_model_id:
            return link
    return None


async def _resolve_via_link(
    provider_registry: ProviderRegistry, model_id: str, link: ModelProviderLink,
) -> RemoteDownloadRef:
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


async def _resolve_via_hash(
    model_repository: ModelRepository, provider_registry: ProviderRegistry, model_id: str, model: Model,
) -> RemoteDownloadRef:
    providers = provider_registry.get_providers_with_capability(ProviderCapability.REMOTE_DOWNLOAD_BY_HASH)
    if not providers:
        raise ModelNotLinkedError(f"Model {model_id} has no linked provider and no provider supports hash lookup")

    sha256 = _ensure_digest(model, model_repository)

    last_error: Optional[Exception] = None
    for provider in providers:
        try:
            return await provider.resolve_remote_download_by_hash(sha256)
        except (RemoteDownloadResolutionError, NotImplementedError) as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc

    raise ProviderResolutionFailedError(f"No provider resolved a by-hash download for model {model_id}: {last_error}")


def _ensure_digest(model: Model, model_repository: ModelRepository) -> str:
    if model.sha256:
        return model.sha256
    if model.is_directory or not model.file_path:
        raise ModelNotLinkedError(f"Model {model.id} has no file to hash for a by-hash lookup")

    path = Path(model.file_path)
    if not path.is_file():
        raise ModelNotLinkedError(f"Model {model.id}'s file is missing on disk - cannot hash it for a by-hash lookup")

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            hasher.update(chunk)
    sha256 = hasher.hexdigest()
    file_size = path.stat().st_size

    model.sha256, model.file_size = sha256, file_size
    model_repository.update_digest(model.id, sha256=sha256, file_size=file_size)
    return sha256


async def resolve_url_remote_download(provider, session, url: str) -> RemoteDownloadRef:
    """A pasted download URL, resolved for handing to a remote (untrusted)
    worker - the Downloader's "download straight onto a worker" destination.

    `provider` is whatever `DownloadWorker._resolve_provider` already
    resolved for this download (explicit `provider_id`, or the first
    provider whose `matches_download_url` claims the URL) - `None` when no
    provider claims it, which is the common case for a plain, unauthenticated
    URL and is safe to hand to the worker as-is, same as a local download
    makes no auth attempt in that case.

    A provider that DOES claim the URL must declare `REMOTE_DOWNLOAD` and its
    `prepare_download()` must land off its own authenticated host with no
    leftover credential (mirrors the check `resolve_remote_download`
    implementations make, e.g. CivitAI's own token-in-URL guard) - otherwise
    this refuses rather than leak credentials to the worker. Leftover headers
    from `_BENIGN_DOWNLOAD_HEADERS` (a User-Agent, an Accept) are not
    credentials and travel with the ref; any other leftover header refuses.
    """
    if provider is None:
        return RemoteDownloadRef(url=url)

    provider_name = provider.get_metadata().name
    if not provider.get_metadata().has_capability(ProviderCapability.REMOTE_DOWNLOAD):
        raise ProviderCapabilityMissingError(
            f"Provider '{provider_name}' does not support remote destinations"
        )

    headers: dict = {}
    resolved_url = await provider.prepare_download(session, url, headers)
    credentialed = {k for k in headers if k.lower() not in _BENIGN_DOWNLOAD_HEADERS}
    if credentialed or provider.matches_download_url(resolved_url):
        raise ProviderCapabilityMissingError(
            f"Provider '{provider_name}' could not resolve a credential-free URL for a remote worker"
        )
    return RemoteDownloadRef(url=resolved_url, headers=dict(headers))


def _check_size(model: Model, ref: RemoteDownloadRef) -> None:
    if ref.size_hint is None or model.file_size is None:
        return
    if ref.size_hint != model.file_size:
        raise RemoteDownloadSizeMismatchError(
            f"Resolved download size {ref.size_hint} contradicts model {model.id}'s recorded size {model.file_size}"
        )
