"""
SDXL-specific application of the core MemoryPolicy.

The VRAM tier table itself lives in src.platform.runtime.model_lifecycle.memory_policy
(MemoryPolicy) — this module only applies those decisions to a diffusers
SDXL pipeline (offload, TF32, attention backend, VAE slicing/tiling).
"""

import torch
from src.pipelines.contracts import logger
from src.platform.runtime.model_lifecycle.memory_policy import MemoryPolicy


def apply_to_pipeline(pipe, policy: MemoryPolicy, offload_override: str = None,
                      use_xformers: bool = False) -> None:
    """
    Apply all memory optimizations to the pipeline based on the given policy.

    1. CPU offloading (sequential/model/none)
    2. PyTorch backend optimizations (TF32)
    3. Attention optimizations (xformers, PyTorch 2.0 SDPA)
    4. VAE optimizations (slicing, tiling)
    5. Attention slicing (max/auto/none)

    Args:
        offload_override: force the offload decision ("sequential", "model",
            "none") regardless of the policy's VRAM tier. None = policy decides.
        use_xformers: only attempt xformers when explicitly requested — it
            silently replaces the AttnProcessor2_0 backend set above, so it
            must stay opt-in.
    """
    logger.info(f"[MEMORY STRATEGY] Applying optimizations for {policy.vram_gb:.2f}GB VRAM")

    offload_strategy = offload_override or policy.get_offload_strategy()
    if offload_override:
        logger.debug(f"[MEMORY STRATEGY] Offload strategy forced by config: {offload_override}")
    if offload_strategy == "sequential":
        logger.debug("[MEMORY STRATEGY] Enabling sequential CPU offload (most aggressive)")
        pipe.enable_sequential_cpu_offload()
    elif offload_strategy == "model":
        logger.debug("[MEMORY STRATEGY] Enabling model CPU offload (balanced)")
        pipe.enable_model_cpu_offload()
    else:
        logger.debug("[MEMORY STRATEGY] Keeping model on GPU (best performance)")

    if policy.should_enable_tf32() and torch.cuda.is_available():
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        logger.debug("[MEMORY STRATEGY] Enabled TF32 for matmul and cuDNN")

    try:
        if hasattr(pipe, "unet") and hasattr(pipe.unet, "set_attn_processor"):
            from diffusers.models.attention_processor import AttnProcessor2_0
            pipe.unet.set_attn_processor(AttnProcessor2_0())
            logger.debug("[MEMORY STRATEGY] Using PyTorch 2.0 native attention (AttnProcessor2_0)")
    except Exception as e:
        logger.warning(f"[MEMORY STRATEGY] Could not enable PyTorch 2.0 attention: {e}")

    if use_xformers and policy.should_enable_xformers():
        if hasattr(pipe, "enable_xformers_memory_efficient_attention"):
            try:
                pipe.enable_xformers_memory_efficient_attention()
                logger.debug("[MEMORY STRATEGY] Enabled xformers memory efficient attention")
            except ImportError:
                logger.debug("[MEMORY STRATEGY] xformers not available, using PyTorch attention")
            except Exception as e:
                if "flash" in str(e).lower() or "invalid argument" in str(e).lower():
                    logger.warning(f"[MEMORY STRATEGY] Flash Attention error, skipping xformers: {e}")
                else:
                    logger.warning(f"[MEMORY STRATEGY] Could not enable xformers: {e}")

    if policy.should_enable_vae_slicing():
        if hasattr(pipe, "enable_vae_slicing"):
            try:
                pipe.enable_vae_slicing()
                logger.debug("[MEMORY STRATEGY] Enabled VAE slicing (reduces memory by ~50%)")
            except Exception as e:
                logger.warning(f"[MEMORY STRATEGY] Could not enable VAE slicing: {e}")

    if policy.should_enable_vae_tiling():
        if hasattr(pipe, "enable_vae_tiling"):
            try:
                pipe.enable_vae_tiling()
                logger.debug("[MEMORY STRATEGY] Enabled VAE tiling (enables large image processing)")
            except Exception as e:
                logger.warning(f"[MEMORY STRATEGY] Could not enable VAE tiling: {e}")

    attention_slicing = policy.get_attention_slicing()
    if attention_slicing == "max":
        if hasattr(pipe, "enable_attention_slicing"):
            try:
                pipe.enable_attention_slicing(slice_size="max")
                logger.debug("[MEMORY STRATEGY] Enabled max attention slicing (minimal memory)")
            except Exception as e:
                logger.warning(f"[MEMORY STRATEGY] Could not enable max attention slicing: {e}")
    elif attention_slicing == "auto":
        if hasattr(pipe, "enable_attention_slicing"):
            try:
                pipe.enable_attention_slicing(slice_size="auto")
                logger.debug("[MEMORY STRATEGY] Enabled auto attention slicing (balanced)")
            except Exception as e:
                logger.warning(f"[MEMORY STRATEGY] Could not enable auto attention slicing: {e}")
    else:
        logger.debug("[MEMORY STRATEGY] Attention slicing disabled (best performance)")

    logger.debug("[MEMORY STRATEGY] Memory optimizations applied successfully")
