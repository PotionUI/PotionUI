"""Worker process configuration, read once from the environment at boot.

Deliberately not a Pydantic settings model wired to the PotionUI settings
table: a worker has no PotionUI database, so environment variables are the
only configuration surface it can have.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


class WorkerMisconfigured(RuntimeError):
    """The worker cannot start because a required setting is missing."""


@dataclass(frozen=True)
class WorkerConfig:
    token: str
    worker_id: str
    provider: str
    host: str
    port: int
    work_dir: Path
    artifacts_dir: Path
    #: None degrades compute_build_fingerprint() to a protocol-version-only
    #: value - see src.pipelines.remote_fingerprint.compute_build_fingerprint.
    build_id: Optional[str]
    #: None means "probe this host instead of trusting an operator override" -
    #: see src.bootstrap.worker_container._probe_default_device/_dtype.
    device: Optional[str]
    dtype: Optional[str]
    vram_limit_gb: Optional[float]

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "WorkerConfig":
        source = os.environ if env is None else env

        token = (source.get("POTIONUI_WORKER_TOKEN") or "").strip()
        if not token:
            raise WorkerMisconfigured(
                "POTIONUI_WORKER_TOKEN is required - refusing to start an "
                "unauthenticated worker"
            )

        work_dir = Path(source.get("POTIONUI_WORKER_DIR", "./worker_data")).resolve()
        vram_raw = source.get("POTIONUI_WORKER_VRAM_GB")

        return cls(
            token=token,
            worker_id=source.get("POTIONUI_WORKER_ID") or f"worker-{uuid.uuid4().hex[:12]}",
            provider=source.get("POTIONUI_WORKER_PROVIDER", "manual"),
            host=source.get("POTIONUI_WORKER_HOST", "127.0.0.1"),
            port=int(source.get("POTIONUI_WORKER_PORT", "8100")),
            work_dir=work_dir,
            artifacts_dir=work_dir / "artifacts",
            build_id=source.get("POTIONUI_BUILD_ID") or None,
            device=source.get("POTIONUI_WORKER_DEVICE") or None,
            dtype=source.get("POTIONUI_WORKER_DTYPE") or None,
            vram_limit_gb=float(vram_raw) if vram_raw else None,
        )
