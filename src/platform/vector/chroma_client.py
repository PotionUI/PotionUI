"""Lazy, thread-safe construction of a shared ``chromadb`` client.

``chromadb`` and its ``PersistentClient`` are heavy to import (telemetry,
OpenTelemetry exporters, ...), so the import and the client construction are
both deferred to first use rather than paid at process boot. A
``ChromaClientProvider`` owns the lifecycle of exactly one client for one
``persist_dir``; construct it once and inject the same instance into every
vector store that persists to that directory so they open one underlying
client instead of each opening (and holding a connection to) its own.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import chromadb


class ChromaClientProvider:
    """Builds and caches one ``chromadb.PersistentClient`` on first use."""

    def __init__(self, persist_dir: str):
        self._persist_dir = persist_dir
        self._client: Optional["chromadb.ClientAPI"] = None
        self._lock = threading.Lock()

    def get(self) -> "chromadb.ClientAPI":
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import chromadb

                    self._client = chromadb.PersistentClient(path=self._persist_dir)
        return self._client

    def close(self) -> None:
        """Release the cached client so the next ``get()`` builds a fresh one.

        Idempotent, and safe to call whether or not a client has been built
        yet. A failed ``get()`` never caches anything, so a later ``get()``
        call already retries on its own; ``close()`` is for a caller that
        holds a live client and wants it released deliberately.

        This provider is the only place a live client may be cached - callers
        (the vector stores) must call ``get()`` on every access rather than
        holding their own reference, or a store that cached an earlier result
        keeps serving a client this provider has since closed. ``close()``
        assumes a quiescent boundary: call it only when nothing else is
        concurrently reading the client returned by a still-in-flight
        ``get()`` call, since closing races with an in-progress read of the
        about-to-be-replaced client are the caller's to avoid, not this
        provider's to prevent.
        """
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()
