"""
Memory Policy - the single VRAM tier table used across the codebase.

MemoryPolicy is model-agnostic: it only reasons about a VRAM budget in GB and
returns strategy decisions. Model-specific application of those decisions
(e.g. calling `pipe.enable_sequential_cpu_offload()`) lives next to the model
code (see checkpoint_loader/sdxl/memory_strategy.py::apply_to_pipeline).
"""

import logging
from typing import Literal

logger = logging.getLogger(__name__)

OffloadStrategy = Literal["sequential", "model", "none"]
AttentionSlicing = Literal["max", "auto", "none"]
MemoryStrategyName = Literal["conservative", "balanced", "full_vram"]


class MemoryPolicy:
    """
    Determines memory optimization decisions based on available VRAM.

    VRAM tiers:
    - < 8GB:  aggressive  (sequential offload, max attention slicing, VAE slicing+tiling)
    - 8-12GB: balanced    (model offload, auto attention slicing, VAE slicing+tiling)
    - 12-16GB: light      (no offload, VAE slicing only)
    - >= 16GB: minimal    (GPU-resident, no VAE slicing/tiling)
    """

    def __init__(self, vram_gb: float):
        self.vram_gb = vram_gb

    def get_offload_strategy(self) -> OffloadStrategy:
        if self.vram_gb < 8:
            return "sequential"
        elif self.vram_gb < 12:
            return "model"
        return "none"

    def get_attention_slicing(self) -> AttentionSlicing:
        if self.vram_gb < 8:
            return "max"
        elif self.vram_gb < 12:
            return "auto"
        return "none"

    def should_enable_vae_slicing(self) -> bool:
        return self.vram_gb < 16

    def should_enable_vae_tiling(self) -> bool:
        return self.vram_gb < 12

    def should_enable_xformers(self) -> bool:
        return True

    def should_enable_tf32(self) -> bool:
        return True

    def get_memory_strategy(self) -> MemoryStrategyName:
        """Coarse strategy name, used by MemoryManager for tiling/batch-size math."""
        if self.vram_gb >= 24.0:
            return "full_vram"
        elif self.vram_gb >= 12.0:
            return "balanced"
        return "conservative"

    def should_use_cpu_offload(self, model_type: str = "sdxl") -> bool:
        strategy = self.get_memory_strategy()
        if strategy == "conservative":
            return True
        if strategy == "balanced":
            # Approximate resident footprint in GB (not params-in-billions) of
            # the diffusion model at its typically-deployed checkpoint
            # precision, used only for the `base_cost_gb > vram*0.5` offload
            # heuristic in this tier.
            base_costs_gb = {
                "sdxl": 4.0,
                "flux": 24.0,        # 12B DiT bf16, ~23.8GB Flux.1-dev safetensors
                "sd15": 2.0,
                "qwen_image": 20.0,  # bare-fp8 DiT, GPU-validated peak 19.37GB total
                # Native engine v2 families.
                "flux2": 18.0,       # 9B DiT bf16 (~9GB when fp8-scaled)
                "flux_klein": 18.0,  # same 9B Flux2 DiT checkpoint (Klein)
                "krea2": 26.0,       # ~12B DiT bf16 (features 6144 x 28 layers), 26GB on disk
                "wan22": 14.0,       # 14B expert resident at a time (video), fp8 default
                "ltx2": 27.0,        # LTX-2 AV DiT (19-22B)
            }
            base_cost_gb = base_costs_gb.get(model_type.lower(), 4.0)
            return base_cost_gb > (self.vram_gb * 0.5)
        return False

    def __repr__(self) -> str:
        return f"MemoryPolicy(vram_gb={self.vram_gb:.2f})"
