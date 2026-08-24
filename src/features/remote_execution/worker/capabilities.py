"""Probing this host's hardware/software for the worker handshake (WorkerInfoV1).

Every import here that isn't already a hard PotionUI dependency (torch,
psutil) is deferred into the function body so a CPU-only container can import
this module - and therefore the whole worker app - without paying for a CUDA
probe until a client actually asks for /v1/worker.
"""

from __future__ import annotations

import platform as platform_module
import shutil
from pathlib import Path

from src.platform.worker_protocol import GpuInfoV1, WorkerCapabilitiesV1


def probe_capabilities(work_dir: Path) -> WorkerCapabilitiesV1:
    import psutil

    try:
        free_disk_bytes = shutil.disk_usage(str(work_dir)).free
    except OSError:
        free_disk_bytes = 0

    torch_version = ""
    cuda_version = None
    gpus: list[GpuInfoV1] = []
    try:
        import torch

        torch_version = torch.__version__
        if torch.cuda.is_available():
            cuda_version = torch.version.cuda
            for index in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(index)
                free_bytes, total_bytes = torch.cuda.mem_get_info(index)
                gpus.append(
                    GpuInfoV1(
                        index=index,
                        name=props.name,
                        total_memory_bytes=total_bytes,
                        free_memory_bytes=free_bytes,
                        compute_capability=f"{props.major}.{props.minor}",
                    )
                )
    except Exception:
        pass  # a CPU-only host has no torch.cuda story to report

    return WorkerCapabilitiesV1(
        gpus=tuple(gpus),
        cpu_count=psutil.cpu_count() or 0,
        total_memory_bytes=psutil.virtual_memory().total,
        free_disk_bytes=free_disk_bytes,
        python_version=platform_module.python_version(),
        torch_version=torch_version,
        cuda_version=cuda_version,
        platform=platform_module.platform(),
        attention_backends=(),
        features=(),
    )
