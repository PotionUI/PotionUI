"""
Memory Manager - Intelligent memory allocation and resource management

Provides intelligent VRAM allocation, tiling strategies, and memory optimization
for image generation pipelines.
"""

import math
import numpy as np
from typing import Tuple, Dict, Any, Optional, Literal

from src.platform.observability.logger import logger
from src.platform.util.dimensions import round_to_multiple
from src.platform.runtime.model_lifecycle.memory_policy import MemoryPolicy


class MemoryAdvisor:
    """
    Singleton service for intelligent memory management and resource allocation.

    Provides:
    - Dynamic tiling strategies based on VRAM budget
    - Memory optimization recommendations
    - Resource allocation decisions for pipeline components
    """

    def __init__(self, gpu_monitor: 'GpuMonitor', settings: 'Settings'):
        """
        Initialize memory manager.

        Args:
            gpu_monitor: GPU manager for VRAM monitoring (injected)
            settings: Settings manager for configuration (injected)
        """
        self.gpu_monitor = gpu_monitor
        self.settings = settings
        logger.info("[MEMORY_MANAGER] Initialized")

    def get_memory_strategy(self, vram_budget: Optional[float] = None) -> Literal["full_vram", "balanced", "conservative"]:
        """
        Get recommended memory strategy based on VRAM budget.

        Delegates to MemoryPolicy (src.platform.runtime.model_lifecycle.memory_policy) —
        the single VRAM tier table shared with checkpoint loading.

        Strategies:
        - "full_vram": 24GB+ - Keep everything on GPU, maximum performance
        - "balanced": 12-24GB - Partial offloading, balanced performance
        - "conservative": <12GB - Aggressive offloading, prioritize stability

        Args:
            vram_budget: VRAM budget in GB (if None, will get from gpu_monitor)

        Returns:
            str: Memory strategy name
        """
        if vram_budget is None:
            vram_budget = self.gpu_monitor.get_vram_budget()

        strategy = MemoryPolicy(vram_budget).get_memory_strategy()

        logger.debug(f"[MEMORY_MANAGER] Memory strategy for {vram_budget:.1f}GB: {strategy}")
        return strategy

    def calculate_optimal_tile_size(
        self,
        image_size: Tuple[int, int],
        vram_budget: Optional[float] = None,
        model_type: str = "sdxl",
        min_tile_size: int = 896
    ) -> Tuple[int, int]:
        """
        Calculate optimal tile size for given image and VRAM budget.

        Args:
            image_size: (width, height) tuple
            vram_budget: Available VRAM in GB (if None, will get from gpu_monitor)
            model_type: Model type for VRAM estimation
            min_tile_size: Minimum tile size in pixels

        Returns:
            Tuple[int, int]: (tile_width, tile_height)
        """
        width, height = image_size

        if vram_budget is None:
            vram_budget = self.gpu_monitor.get_vram_budget()

        # Check if full image fits
        full_image_vram = self.gpu_monitor.estimate_vram_usage(width, height, model_type)
        if full_image_vram <= vram_budget * 0.85:  # 85% safety margin
            logger.debug(
                f"[MEMORY_MANAGER] Full image {width}x{height} fits in "
                f"{vram_budget:.1f}GB budget (estimated {full_image_vram:.1f}GB)"
            )
            return width, height

        # Determine test tile sizes based on VRAM budget
        if vram_budget >= 24:  # 24GB+ cards (3090, 4090, A5000, etc.)
            test_sizes = [3072, 2816, 2560, 2304, 2048, 1920, 1792, 1664, 1536, 1344, 1216, 1152, 1024, 896]
        elif vram_budget >= 16:  # 16GB+ cards
            test_sizes = [2304, 2048, 1920, 1792, 1664, 1536, 1344, 1216, 1152, 1024, 896]
        elif vram_budget >= 12:  # 12GB+ cards
            test_sizes = [1792, 1664, 1536, 1344, 1216, 1152, 1024, 896]
        else:  # 8GB and below
            test_sizes = [1344, 1216, 1152, 1024, 896, 768]

        # Find largest tile size that fits
        max_tile_size = min_tile_size
        for test_size in test_sizes:
            estimated_vram = self.gpu_monitor.estimate_vram_usage(test_size, test_size, model_type)
            if estimated_vram <= vram_budget * 0.85:  # 85% safety margin
                max_tile_size = test_size
                logger.debug(
                    f"[MEMORY_MANAGER] Selected tile size {test_size}x{test_size} "
                    f"(estimated {estimated_vram:.1f}GB)"
                )
                break

        # Ensure minimum tile size
        max_tile_size = max(min_tile_size, max_tile_size)

        # Calculate aspect-aware tile size
        aspect_ratio = width / height
        if aspect_ratio > 1.5:  # Wide image
            tile_width = max_tile_size
            tile_height = round_to_multiple(int(max_tile_size / aspect_ratio))
        elif aspect_ratio < 0.67:  # Tall image
            tile_width = round_to_multiple(int(max_tile_size * aspect_ratio))
            tile_height = max_tile_size
        else:  # Roughly square
            tile_width = tile_height = max_tile_size

        # Ensure tile size doesn't exceed image size
        tile_width = min(tile_width, width)
        tile_height = min(tile_height, height)

        # Ensure minimum and multiple of 8
        tile_width = max(min_tile_size, round_to_multiple(tile_width))
        tile_height = max(min_tile_size, round_to_multiple(tile_height))

        logger.debug(
            f"[MEMORY_MANAGER] Optimal tile size for {width}x{height} image: "
            f"{tile_width}x{tile_height} (budget: {vram_budget:.1f}GB)"
        )

        return tile_width, tile_height

    def calculate_optimal_tile_count(
        self,
        image_size: Tuple[int, int],
        vram_budget: Optional[float] = None,
        model_type: str = "sdxl",
        overlap: int = 128,
        min_tile_size: int = 896
    ) -> Tuple[int, int, int, int]:
        """
        Calculate optimal tile grid for image processing.

        Returns tile grid configuration that maximizes tile size while
        fitting in VRAM budget.

        Args:
            image_size: (width, height) tuple
            vram_budget: Available VRAM in GB (if None, will get from gpu_monitor)
            model_type: Model type for VRAM estimation
            overlap: Overlap between tiles in pixels
            min_tile_size: Minimum tile size in pixels

        Returns:
            Tuple[int, int, int, int]: (tiles_x, tiles_y, tile_width, tile_height)
        """
        width, height = image_size

        if vram_budget is None:
            vram_budget = self.gpu_monitor.get_vram_budget()

        # Get optimal tile size
        tile_width, tile_height = self.calculate_optimal_tile_size(
            image_size, vram_budget, model_type, min_tile_size
        )

        # If full image fits, return single tile
        if tile_width >= width and tile_height >= height:
            return 1, 1, width, height

        # Calculate number of tiles needed
        def calculate_tiles_needed(dimension: int, tile_size: int) -> int:
            """Calculate how many tiles needed to cover dimension with overlap."""
            if dimension <= tile_size:
                return 1

            # Calculate with overlap
            effective_tile_size = tile_size - overlap
            tiles_needed = max(1, math.ceil((dimension - overlap) / effective_tile_size))

            return tiles_needed

        tiles_x = calculate_tiles_needed(width, tile_width)
        tiles_y = calculate_tiles_needed(height, tile_height)

        # Adjust tile size for even distribution
        if tiles_x > 1:
            actual_tile_width = (width + overlap * (tiles_x - 1)) // tiles_x
            tile_width = max(min_tile_size, round_to_multiple(actual_tile_width))

        if tiles_y > 1:
            actual_tile_height = (height + overlap * (tiles_y - 1)) // tiles_y
            tile_height = max(min_tile_size, round_to_multiple(actual_tile_height))

        logger.debug(
            f"[MEMORY_MANAGER] Optimal tile grid for {width}x{height}: "
            f"{tiles_x}x{tiles_y} tiles @ {tile_width}x{tile_height} "
            f"(budget: {vram_budget:.1f}GB, estimated per tile: "
            f"{self.gpu_monitor.estimate_vram_usage(tile_width, tile_height, model_type):.1f}GB)"
        )

        return tiles_x, tiles_y, tile_width, tile_height

    def should_use_cpu_offload(
        self,
        model_type: str = "sdxl",
        vram_budget: Optional[float] = None
    ) -> bool:
        """
        Determine if CPU offload should be used for model loading.

        Args:
            model_type: Model type (affects base VRAM cost)
            vram_budget: Available VRAM in GB (if None, will get from gpu_monitor)

        Returns:
            bool: True if CPU offload recommended, False otherwise
        """
        if vram_budget is None:
            vram_budget = self.gpu_monitor.get_vram_budget()

        return MemoryPolicy(vram_budget).should_use_cpu_offload(model_type)

    def get_batch_size_recommendation(
        self,
        image_size: Tuple[int, int],
        model_type: str = "sdxl",
        vram_budget: Optional[float] = None
    ) -> int:
        """
        Recommend batch size based on image size and VRAM budget.

        Args:
            image_size: (width, height) tuple
            model_type: Model type for VRAM estimation
            vram_budget: Available VRAM in GB (if None, will get from gpu_monitor)

        Returns:
            int: Recommended batch size (1-4)
        """
        width, height = image_size

        if vram_budget is None:
            vram_budget = self.gpu_monitor.get_vram_budget()

        # Estimate VRAM per image
        per_image_vram = self.gpu_monitor.estimate_vram_usage(width, height, model_type)

        # Calculate how many fit with safety margin
        safe_budget = vram_budget * 0.85  # 85% safety margin
        max_batch = int(safe_budget / per_image_vram)

        # Clamp to reasonable range
        batch_size = max(1, min(4, max_batch))

        logger.debug(
            f"[MEMORY_MANAGER] Batch size recommendation for {width}x{height}: "
            f"{batch_size} (budget: {vram_budget:.1f}GB, per image: {per_image_vram:.1f}GB)"
        )

        return batch_size

    def log_memory_recommendation(
        self,
        image_size: Tuple[int, int],
        model_type: str = "sdxl",
        context: str = ""
    ):
        """
        Log comprehensive memory recommendations for debugging.

        Args:
            image_size: (width, height) tuple
            model_type: Model type for estimation
            context: Optional context string
        """
        vram_budget = self.gpu_monitor.get_vram_budget()
        strategy = self.get_memory_strategy(vram_budget)
        should_offload = self.should_use_cpu_offload(model_type, vram_budget)
        batch_size = self.get_batch_size_recommendation(image_size, model_type, vram_budget)
        tiles_x, tiles_y, tile_w, tile_h = self.calculate_optimal_tile_count(image_size, vram_budget, model_type)

        context_str = f"[{context}] " if context else ""

        logger.debug(
            f"[MEMORY_MANAGER] {context_str}Memory Recommendations:\n"
            f"  Image: {image_size[0]}x{image_size[1]}\n"
            f"  VRAM Budget: {vram_budget:.2f}GB\n"
            f"  Strategy: {strategy}\n"
            f"  CPU Offload: {'Yes' if should_offload else 'No'}\n"
            f"  Batch Size: {batch_size}\n"
            f"  Tiling: {tiles_x}x{tiles_y} grid @ {tile_w}x{tile_h} per tile"
        )
