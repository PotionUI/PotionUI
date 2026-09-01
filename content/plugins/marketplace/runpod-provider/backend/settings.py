"""Reading this plugin's own admin-configured settings.

`PluginRepository` decrypts `is_secret` settings transparently (see
`src.plugin_api.storage`'s docstring) - `load_settings()` re-reads on every
call rather than caching, so an admin rotating the API key in Admin -> Plugins
takes effect on the next request without a process restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from src.plugin_api import PluginRepository

PLUGIN_ID = "runpod-provider"


@dataclass(frozen=True)
class RunPodSettings:
    api_key: Optional[str]
    region: Optional[str]
    gpu_type_id: str
    volume_size_gb: int
    worker_image: Optional[str]
    container_registry_auth_id: Optional[str]
    allowed_cuda_versions: Tuple[str, ...]


def _parse_cuda_versions(raw: Optional[str]) -> Tuple[str, ...]:
    """RunPod's `allowedCudaVersions` is an exact-match allowlist, not a
    minimum - every acceptable version has to be named. "13.0" is both the top
    of RunPod's enum and the floor the shipped worker image's `cu130` torch
    build needs (an older host driver drops it to CPU silently), so this
    default travels with `worker_image`. An explicit empty value means "any
    CUDA version"."""
    if raw is None:
        return ("13.0",)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def load_settings(repository: Optional[PluginRepository] = None) -> RunPodSettings:
    repo = repository or PluginRepository()

    def _value(key: str) -> Optional[str]:
        setting = repo.get_plugin_setting(PLUGIN_ID, key)
        return setting.setting_value if setting else None

    volume_size_raw = _value("volume_size_gb")

    return RunPodSettings(
        api_key=_value("api_key"),
        region=_value("region"),
        gpu_type_id=_value("gpu_type_id") or "NVIDIA GeForce RTX 4090",
        volume_size_gb=int(volume_size_raw) if volume_size_raw else 100,
        worker_image=_value("worker_image"),
        container_registry_auth_id=_value("container_registry_auth_id"),
        allowed_cuda_versions=_parse_cuda_versions(_value("allowed_cuda_versions")),
    )
