"""Resolves a pasted download URL into a form a remote (untrusted) worker can
fetch without credentials - the Downloader's "download straight onto a
remote destination backend" path (`src/features/downloads/worker.py`).
"""

from src.features.providers.base_provider import ProviderCapability, RemoteDownloadRef

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


class ProviderCapabilityMissingError(RemoteDownloadResolutionError):
    """The linked provider cannot resolve a credential-free URL."""


async def resolve_url_remote_download(provider, session, url: str) -> RemoteDownloadRef:
    """A pasted download URL, resolved for handing to a remote (untrusted)
    worker - the Downloader's "download straight onto a worker" destination.

    `provider` is whatever `DownloadWorker._resolve_provider` already
    resolved for this download (explicit `provider_id`, or the first
    provider whose `matches_download_url` claims the URL) - `None` when no
    provider claims it, which is the common case for a plain, unauthenticated
    URL and is safe to hand to the worker as-is, same as a local download
    makes no auth attempt in that case.

    A provider that DOES claim the URL must declare `REMOTE_DOWNLOAD`. If it
    implements `resolve_remote_url` (e.g. HuggingFace, whose public files
    legitimately stay on huggingface.co so the generic host-check below can't
    apply), that ref is used directly - the provider vouches for it, same as
    `resolve_remote_download` implementations do. Otherwise falls back to
    `prepare_download()`, which must land off its own authenticated host with
    no leftover credential (mirrors the check `resolve_remote_download`
    implementations make, e.g. CivitAI's own token-in-URL guard) - otherwise
    this refuses rather than leak credentials to the worker. Either way,
    leftover headers from `_BENIGN_DOWNLOAD_HEADERS` (a User-Agent, an
    Accept) are not credentials and travel with the ref; any other leftover
    header refuses.
    """
    if provider is None:
        return RemoteDownloadRef(url=url)

    provider_name = provider.get_metadata().name
    if not provider.get_metadata().has_capability(ProviderCapability.REMOTE_DOWNLOAD):
        raise ProviderCapabilityMissingError(
            f"Provider '{provider_name}' does not support remote destinations"
        )

    try:
        ref = await provider.resolve_remote_url(session, url)
    except NotImplementedError:
        pass
    else:
        if {k for k in ref.headers if k.lower() not in _BENIGN_DOWNLOAD_HEADERS}:
            raise ProviderCapabilityMissingError(
                f"Provider '{provider_name}' could not resolve a credential-free URL for a remote worker"
            )
        return ref

    headers: dict = {}
    resolved_url = await provider.prepare_download(session, url, headers)
    credentialed = {k for k in headers if k.lower() not in _BENIGN_DOWNLOAD_HEADERS}
    if credentialed or provider.matches_download_url(resolved_url):
        raise ProviderCapabilityMissingError(
            f"Provider '{provider_name}' could not resolve a credential-free URL for a remote worker"
        )
    return RemoteDownloadRef(url=resolved_url, headers=dict(headers))
