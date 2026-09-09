"""The archive's description of itself."""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.platform.version import POTIONUI_VERSION

BACKUP_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = {BACKUP_SCHEMA_VERSION}

MANIFEST_FILENAME = "manifest.json"


class UnsupportedArchiveError(RuntimeError):
    """The archive was written by a version that this checkout cannot read."""


def build_manifest(
    *,
    tier: str,
    tiers: list,
    items: Dict[str, Dict[str, int]],
    storage_backend: str,
    migration_head: Optional[str],
    include_models: bool,
    include_animated_thumbnails: bool,
    media_mirror: Optional[Dict[str, Any]] = None,
    models_mirror: Optional[Dict[str, Any]] = None,
    created_at: Optional[datetime] = None,
    hostname: Optional[str] = None,
) -> Dict[str, Any]:
    moment = created_at or datetime.now(timezone.utc)
    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "app_version": POTIONUI_VERSION,
        "migration_head": migration_head,
        "created_at": moment.astimezone(timezone.utc).isoformat(),
        "hostname": hostname if hostname is not None else socket.gethostname(),
        "tier": tier,
        "tiers": list(tiers),
        "storage_backend": storage_backend,
        "include_models": include_models,
        "include_animated_thumbnails": include_animated_thumbnails,
        "items": items,
        "media_mirror": media_mirror,
        "models_mirror": models_mirror,
    }


def dump_manifest(manifest: Dict[str, Any]) -> bytes:
    return json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")


def load_manifest(raw: bytes | str) -> Dict[str, Any]:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        manifest = json.loads(text)
    except ValueError as exc:
        raise UnsupportedArchiveError(f"{MANIFEST_FILENAME} is not valid JSON ({exc}).") from None
    if not isinstance(manifest, dict):
        raise UnsupportedArchiveError(f"{MANIFEST_FILENAME} is not an object.")
    version = manifest.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedArchiveError(
            f"archive schema_version {version!r} is not supported by this checkout "
            f"(supports {sorted(SUPPORTED_SCHEMA_VERSIONS)}). Restore it with the "
            f"PotionUI version that wrote it."
        )
    return manifest
