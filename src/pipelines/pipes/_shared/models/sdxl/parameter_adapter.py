"""
SDXL Parameter Adapter

This module converts GenerationInput to diffusers pipeline parameters for SDXL models.
"""

import torch
from typing import Dict, Any, Optional
from src.pipelines.contracts import IOType
from src.pipelines.contracts import GenerationInput


class SDXLParameterAdapter:
    """
    Converts GenerationInput to diffusers pipeline parameters.

    This adapter handles the conversion of high-level generation inputs
    (from preset forms and pipe configurations) into the specific parameters
    required by the StableDiffusionXLKDiffusionPipeline.

    Responsibilities:
    - Sampler name mapping (PotionUI → k-diffusion)
    - Pipeline parameter construction for txt2img and img2img modes
    - Torch generator creation with proper device and seed

    Example:
        >>> adapter = SDXLParameterAdapter(generation_input)
        >>> params = adapter.build_pipeline_params(conditioning, mode="txt2img")
        >>> images = pipeline(**params)
    """

    # Mapping from PotionUI sampler names to k-diffusion sampler names
    SAMPLER_MAP = {
        "EULER": "euler",
        "EULER_A": "euler_ancestral",
        "HEUN": "heun",
        "DPM2": "dpm_2",
        "DPM2_A": "dpm_2_ancestral",
        "LMS": "lms",
        "DPMPP_2S_A": "dpmpp_2s_ancestral",
        "DPMPP_SDE": "dpmpp_sde",
        "DPMPP_2M": "dpmpp_2m",
        "DPMPP_2M_SDE": "dpmpp_2m_sde",
        "DPMPP_3M_SDE": "dpmpp_3m_sde",
        "LCM": "lcm"
    }

    def __init__(self, generation_input: GenerationInput):
        """
        Initialize the parameter adapter.

        Args:
            generation_input: The high-level generation input containing all
                            parameters from preset forms and pipe configurations
        """
        self.input = generation_input

    @property
    def sampler(self) -> str:
        """
        Map PotionUI sampler name to k-diffusion sampler name.

        Returns:
            str: k-diffusion sampler name (e.g., "dpmpp_2m")
        """
        sampler = self.input.get(IOType.SAMPLER, "DPMPP_2M")
        return self.SAMPLER_MAP.get(sampler, "dpmpp_2m")

    @property
    def scheduler(self) -> str:
        """
        Get the scheduler type for sigma schedule generation.

        Returns:
            str: Scheduler type (e.g., "karras", "exponential", "simple")
        """
        return self.input.get(IOType.SCHEDULER, "karras")

    def build_pipeline_params(
        self,
        conditioning: Any,
        mode: str = "txt2img"
    ) -> Dict[str, Any]:
        """
        Build complete parameter dictionary for diffusers pipeline.

        This method assembles all parameters required by the pipeline's __call__ method,
        including conditioning tensors, sampling parameters, and mode-specific inputs.

        Args:
            conditioning: Conditioning object containing:
                - embeds: role-keyed positive tensors ("embeds", "pooled")
                - n_embeds: role-keyed negative tensors ("embeds", "pooled")
            mode: Generation mode ("txt2img" or "img2img")

        Returns:
            dict: Complete parameter dictionary for pipeline execution

        Example:
            >>> params = adapter.build_pipeline_params(conditioning, mode="txt2img")
            >>> # params = {
            >>> #     "prompt_embeds": tensor(...),
            >>> #     "negative_prompt_embeds": tensor(...),
            >>> #     "num_inference_steps": 30,
            >>> #     "guidance_scale": 7.5,
            >>> #     "sampler": "dpmpp_2m",
            >>> #     "scheduler": "karras",
            >>> #     "generator": <torch.Generator>,
            >>> #     "width": 1024,
            >>> #     "height": 1024,
            >>> #     ...
            >>> # }
        """
        # Extract resolution from input
        resolution = self.input.get(IOType.RESOLUTION)
        if resolution and isinstance(resolution, (list, tuple)) and len(resolution) == 2:
            width, height = resolution
        else:
            # Fallback to default SDXL resolution
            width, height = 1024, 1024

        params = {
            # Core conditioning parameters
            "prompt_embeds": conditioning.embeds["embeds"],
            "negative_prompt_embeds": conditioning.n_embeds["embeds"],
            "pooled_prompt_embeds": conditioning.embeds["pooled"],
            "negative_pooled_prompt_embeds": conditioning.n_embeds["pooled"],

            # Sampling parameters
            "num_inference_steps": self.input[IOType.STEP],
            "guidance_scale": float(self.input[IOType.CFG]),
            "sampler": self.sampler,
            "scheduler": self.scheduler,

            # Resolution parameters (CRITICAL: must be passed to prevent default 2048x2048)
            "width": int(width),
            "height": int(height),

            # Generation parameters
            "generator": self._create_generator(),
            "output_type": "pil",
            "quantity_per_prompt": 1,  # CRITICAL: Number of images to generate per prompt
            "clip_skip": self.input.get(IOType.CLIP_SKIP, 2),
            "embedding_files": self.input.get(IOType.EMBEDDING, {}),  # CRITICAL: Embedding files for custom embeddings
        }

        # Mode-specific parameters
        if mode == "img2img":
            input_image = self.input[IOType.IMAGE]

            # DEBUG: Check if input image is black
            from src.platform.observability.logger import logger
            import numpy as np
            if hasattr(input_image, 'size'):  # PIL Image
                img_array = np.array(input_image)
                logger.debug(f"[PARAM_ADAPTER] img2img input: size={input_image.size}, mode={input_image.mode}, range=[{img_array.min()}, {img_array.max()}]")

            params.update({
                "image": input_image,
                "strength": float(self.input.get(IOType.DENOISE, 0.8)),
            })

            # Pass mask if present - just like the original code
            # The pipeline handles mask internally (used for blending during paste, not inpainting)
            mask = self.input.get(IOType.MASK)
            if mask is not None:
                params["mask_image"] = mask

        return params

    def _create_generator(self) -> Optional[torch.Generator]:
        """
        Create torch generator with proper device and seed.

        The generator ensures reproducible results by setting the random seed
        for all sampling operations. It's created on the appropriate device
        (CPU or CUDA) to match the pipeline's execution device.

        Returns:
            torch.Generator: Configured generator with seed set, or None if seed not provided

        Notes:
            - Seed is required for reproducible generation
            - Device should match the pipeline's device (usually "cuda")
            - The generator is passed to all random operations in the pipeline
        """
        seed = self.input.get(IOType.SEED)
        if seed is None:
            return None

        device = self.input.get_by_name("device", "cuda")
        return torch.Generator(device=device).manual_seed(int(seed))
