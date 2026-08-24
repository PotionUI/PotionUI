"""
System Monitor - Comprehensive system resource monitoring

Provides real-time monitoring of CPU, RAM, GPU, and VRAM usage.
Designed for memory management and resource allocation decisions.
"""

import psutil
from threading import Lock
from typing import Dict, Any, Optional
from pynvml import *

from src.platform.observability.logger import logger
from src.platform.runtime.system_memory import get_system_memory


class SystemMonitor:
    """
    Singleton service for monitoring system resources.

    Monitors:
    - CPU: usage percentage, core count
    - RAM: used, available, total
    - GPU: VRAM used, available, total, temperature, utilization

    Thread-safe with internal locking.
    """

    def __init__(self):
        """Initialize system monitor and NVML."""
        self.lock = Lock()
        self.gpu_available = False
        self.gpu_handle = None

        # Initialize NVML for GPU monitoring. Detection is NVML-only (no torch
        # import, no CUDA context init) so constructing a SystemMonitor never
        # pays torch's import cost or a cold-GPU context-init stall; the VRAM
        # methods below still consult torch.cuda for allocator stats, imported
        # locally at the point of use.
        try:
            nvmlInit()
            self.gpu_handle = nvmlDeviceGetHandleByIndex(0)
            self.gpu_available = True
            logger.info("[SYSTEM_MONITOR] GPU monitoring initialized")
        except Exception as e:
            logger.warning(f"[SYSTEM_MONITOR] Failed to initialize GPU monitoring: {e}")
            self.gpu_available = False

    def get_cpu_info(self) -> Dict[str, Any]:
        """
        Get CPU information.

        Returns:
            dict: {
                'usage_percent': float,  # 0-100
                'core_count': int,
                'core_count_physical': int
            }
        """
        with self.lock:
            try:
                return {
                    'usage_percent': psutil.cpu_percent(interval=0.1),
                    'core_count': psutil.cpu_count(logical=True),
                    'core_count_physical': psutil.cpu_count(logical=False)
                }
            except Exception as e:
                logger.error(f"[SYSTEM_MONITOR] Error getting CPU info: {e}")
                return {
                    'usage_percent': 0.0,
                    'core_count': 1,
                    'core_count_physical': 1
                }

    def get_ram_info(self) -> Dict[str, Any]:
        """
        Get RAM information.

        Returns:
            dict: {
                'total_gb': float,
                'available_gb': float,
                'used_gb': float,
                'usage_percent': float
            }
        """
        with self.lock:
            try:
                mem = get_system_memory()
                used_gb = mem.total_gb - mem.available_gb
                usage_percent = (used_gb / mem.total_gb * 100.0) if mem.total_gb > 0 else 0.0
                return {
                    'total_gb': mem.total_gb,
                    'available_gb': mem.available_gb,
                    'used_gb': used_gb,
                    'usage_percent': usage_percent
                }
            except Exception as e:
                logger.error(f"[SYSTEM_MONITOR] Error getting RAM info: {e}")
                return {
                    'total_gb': 0.0,
                    'available_gb': 0.0,
                    'used_gb': 0.0,
                    'usage_percent': 0.0
                }

    def get_vram_info(self) -> Dict[str, Any]:
        """
        Get VRAM information from GPU.

        Returns:
            dict: {
                'total_gb': float,
                'available_gb': float,  # free + reserved but unused
                'used_gb': float,
                'free_gb': float,       # truly free
                'reserved_gb': float,   # reserved by PyTorch
                'allocated_gb': float,  # actually allocated by PyTorch
                'usage_percent': float
            }
        """
        if not self.gpu_available:
            return {
                'total_gb': 0.0,
                'available_gb': 0.0,
                'used_gb': 0.0,
                'free_gb': 0.0,
                'reserved_gb': 0.0,
                'allocated_gb': 0.0,
                'usage_percent': 0.0
            }

        with self.lock:
            try:
                import torch

                # NVML stats
                mem_info = nvmlDeviceGetMemoryInfo(self.gpu_handle)
                total_gb = mem_info.total / (1024**3)
                free_gb = mem_info.free / (1024**3)
                used_gb = mem_info.used / (1024**3)

                # PyTorch CUDA stats
                allocated_gb = 0.0
                reserved_gb = 0.0
                if torch.cuda.is_available():
                    allocated_gb = torch.cuda.memory_allocated() / (1024**3)
                    reserved_gb = torch.cuda.memory_reserved() / (1024**3)

                # Available = free + (reserved - allocated)
                # This represents VRAM we can actually use
                available_gb = free_gb + (reserved_gb - allocated_gb)

                return {
                    'total_gb': total_gb,
                    'available_gb': available_gb,
                    'used_gb': used_gb,
                    'free_gb': free_gb,
                    'reserved_gb': reserved_gb,
                    'allocated_gb': allocated_gb,
                    'usage_percent': (used_gb / total_gb * 100) if total_gb > 0 else 0.0
                }
            except Exception as e:
                logger.error(f"[SYSTEM_MONITOR] Error getting VRAM info: {e}")
                return {
                    'total_gb': 0.0,
                    'available_gb': 0.0,
                    'used_gb': 0.0,
                    'free_gb': 0.0,
                    'reserved_gb': 0.0,
                    'allocated_gb': 0.0,
                    'usage_percent': 0.0
                }

    def get_gpu_info(self) -> Dict[str, Any]:
        """
        Get GPU information.

        Returns:
            dict: {
                'temperature_c': float,
                'utilization_percent': float,
                'name': str,
                'available': bool
            }
        """
        if not self.gpu_available:
            return {
                'temperature_c': 0.0,
                'utilization_percent': 0.0,
                'name': 'No GPU',
                'available': False
            }

        with self.lock:
            try:
                temp = nvmlDeviceGetTemperature(self.gpu_handle, NVML_TEMPERATURE_GPU)
                util = nvmlDeviceGetUtilizationRates(self.gpu_handle)
                name = nvmlDeviceGetName(self.gpu_handle)

                return {
                    'temperature_c': float(temp),
                    'utilization_percent': float(util.gpu),
                    'name': name.decode('utf-8') if isinstance(name, bytes) else str(name),
                    'available': True
                }
            except Exception as e:
                logger.error(f"[SYSTEM_MONITOR] Error getting GPU info: {e}")
                return {
                    'temperature_c': 0.0,
                    'utilization_percent': 0.0,
                    'name': 'Error',
                    'available': False
                }

    def get_system_snapshot(self) -> Dict[str, Any]:
        """
        Get comprehensive system snapshot.

        Returns:
            dict: {
                'cpu': dict,
                'ram': dict,
                'vram': dict,
                'gpu': dict
            }
        """
        return {
            'cpu': self.get_cpu_info(),
            'ram': self.get_ram_info(),
            'vram': self.get_vram_info(),
            'gpu': self.get_gpu_info()
        }

    def __del__(self):
        """Cleanup NVML on destruction."""
        try:
            if self.gpu_available:
                nvmlShutdown()
        except Exception as e:
            logger.debug(f"NVML cleanup error (can be ignored): {e}")
