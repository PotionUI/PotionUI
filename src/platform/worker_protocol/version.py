"""The worker protocol's version number, alone.

This module deliberately imports nothing. The version is needed both by the
envelope that stamps and validates wire documents and by the fingerprint the
handshake compares, and those two live on opposite sides of the pipelines ->
platform import direction. A leaf module lets the fingerprint side depend on the
number without dragging in the contract models.

Two hand-maintained copies of this integer would be worse than a shared import:
if they drifted, the fingerprint would report a compatible worker while the
envelope rejected its documents as the wrong version.
"""

from __future__ import annotations

WORKER_PROTOCOL_VERSION = 1
