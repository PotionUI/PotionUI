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
    #: The network-volume mount every staged model lands under, keyed by
    #: role/filename - see ModelBundleEntryV1.relative_path. Deliberately
    #: outside work_dir: a model depot is persistent across executions and
    #: (on RunPod) the mount PotionUI does not otherwise own. Defaulted so
    #: existing direct WorkerConfig(...) construction (tests predating the
    #: model depot) keeps working without naming it.
    model_dir: Path = Path("/models")

    def requested_device(self) -> str:
        """What this worker was asked to run on, for the handshake to report.

        Unset means the worker probes for itself
        (``src.bootstrap.worker_container._probe_default_device``), and that
        probe answers ``cpu`` for a *broken* CUDA install as readily as for a
        host that genuinely has no GPU. Reporting the request rather than that
        degraded result is the only way a dispatching host can tell a
        driver-too-old pod from a deliberate CPU worker.
        """
        return self.device or "cuda"

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
            model_dir=Path(source.get("POTIONUI_WORKER_MODEL_DIR", "/models")).resolve(),
            build_id=source.get("POTIONUI_BUILD_ID") or None,
            device=source.get("POTIONUI_WORKER_DEVICE") or None,
            dtype=source.get("POTIONUI_WORKER_DTYPE") or None,
            vram_limit_gb=float(vram_raw) if vram_raw else None,
        )
