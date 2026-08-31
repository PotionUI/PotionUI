"""Static GPU type catalog.

RunPod's REST API (v1) has no endpoint to list GPU types or pricing; that
exists only on the GraphQL API (`podGpuTypes`), which this plugin does not
speak.

The ids below are the `gpuTypeIds` enum RunPod's REST API accepts, so every
id here is guaranteed to work with `RunPodClient.create_pod`. `memory_gb` is
left `None` for newer Blackwell-generation cards the REST API doesn't
describe, rather than guessed. There is no live pricing field - do not add
one without a real source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class GpuType:
    id: str
    memory_gb: Optional[int]


STATIC_GPU_CATALOG: List[GpuType] = [
    GpuType("AMD Instinct MI300X OAM", 192),
    GpuType("NVIDIA A100 80GB PCIe", 80),
    GpuType("NVIDIA A100-SXM4-40GB", 40),
    GpuType("NVIDIA A100-SXM4-80GB", 80),
    GpuType("NVIDIA A40", 48),
    GpuType("NVIDIA B200", None),
    GpuType("NVIDIA B300 SXM6 AC", None),
    GpuType("NVIDIA B300 SXM6 AC MIG 1g.34gb", None),
    GpuType("NVIDIA GeForce RTX 3070", 8),
    GpuType("NVIDIA GeForce RTX 3080", 10),
    GpuType("NVIDIA GeForce RTX 3080 Ti", 12),
    GpuType("NVIDIA GeForce RTX 3090", 24),
    GpuType("NVIDIA GeForce RTX 3090 Ti", 24),
    GpuType("NVIDIA GeForce RTX 4070 Ti", 12),
    GpuType("NVIDIA GeForce RTX 4080", 16),
    GpuType("NVIDIA GeForce RTX 4080 SUPER", 16),
    GpuType("NVIDIA GeForce RTX 4090", 24),
    GpuType("NVIDIA GeForce RTX 5080", 16),
    GpuType("NVIDIA GeForce RTX 5090", 32),
    GpuType("NVIDIA H100 80GB HBM3", 80),
    GpuType("NVIDIA H100 NVL", 94),
    GpuType("NVIDIA H100 PCIe", 80),
    GpuType("NVIDIA H200", 141),
    GpuType("NVIDIA H200 NVL", 141),
    GpuType("NVIDIA L4", 24),
    GpuType("NVIDIA L40", 48),
    GpuType("NVIDIA L40S", 48),
    GpuType("NVIDIA RTX 2000 Ada Generation", 16),
    GpuType("NVIDIA RTX 4000 Ada Generation", 20),
    GpuType("NVIDIA RTX 4000 SFF Ada Generation", 20),
    GpuType("NVIDIA RTX 5000 Ada Generation", 32),
    GpuType("NVIDIA RTX 6000 Ada Generation", 48),
    GpuType("NVIDIA RTX A2000", 6),
    GpuType("NVIDIA RTX A4000", 16),
    GpuType("NVIDIA RTX A4500", 20),
    GpuType("NVIDIA RTX A5000", 24),
    GpuType("NVIDIA RTX A6000", 48),
    GpuType("NVIDIA RTX PRO 4000 Blackwell", None),
    GpuType("NVIDIA RTX PRO 4500 Blackwell", None),
    GpuType("NVIDIA RTX PRO 5000 Blackwell", None),
    GpuType("NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition", None),
    GpuType("NVIDIA RTX PRO 6000 Blackwell Server Edition", None),
    GpuType("NVIDIA RTX PRO 6000 Blackwell Workstation Edition", None),
    GpuType("Tesla V100-PCIE-16GB", 16),
    GpuType("Tesla V100-SXM2-16GB", 16),
]
