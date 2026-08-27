"""Worker process composition root.

Mirrors ``src.bootstrap.container``: one function builds every worker-process
singleton in dependency order onto a typed container, and nothing outside this
module constructs them directly. A worker container is deliberately much
smaller than ``AppContainer`` - no database, no settings table, no auth
manager - because the hard boundary that keeps a worker safe to run on rented
compute is that it cannot reach any of those.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.features.remote_execution.worker.config import WorkerConfig
from src.features.remote_execution.worker.coordinator import WorkerCoordinator
from src.features.remote_execution.worker.journal import WorkerJournal
from src.pipelines.catalog import PipeCatalog
from src.platform.observability.system_probe import SystemMonitor
from src.platform.runtime.gpu import GpuMonitor


@dataclass
class WorkerContainer:
    config: WorkerConfig
    pipe_catalog: PipeCatalog
    journal: WorkerJournal
    coordinator: WorkerCoordinator
    gpu_monitor: GpuMonitor
    system_monitor: SystemMonitor


def build_worker_container(config: WorkerConfig) -> WorkerContainer:
    pipe_catalog = PipeCatalog("src/pipelines/pipes", "pipes/custom")
    journal = WorkerJournal(config.work_dir)
    gpu_monitor = GpuMonitor()
    system_monitor = SystemMonitor()

    coordinator = WorkerCoordinator(
        worker_id=config.worker_id,
        pipe_catalog=pipe_catalog,
        journal=journal,
        artifacts_dir=config.artifacts_dir,
        device=config.device or _probe_default_device(),
        dtype=config.dtype or "fp16",
        vram_limit_gb=config.vram_limit_gb,
        build_id=config.build_id,
        gpu_monitor=gpu_monitor,
        system_monitor=system_monitor,
    )

    return WorkerContainer(
        config=config,
        pipe_catalog=pipe_catalog,
        journal=journal,
        coordinator=coordinator,
        gpu_monitor=gpu_monitor,
        system_monitor=system_monitor,
    )


def _probe_default_device() -> str:
    """Only a CUDA-availability check, never a model load - safe at boot on a
    CPU-only container."""
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"
