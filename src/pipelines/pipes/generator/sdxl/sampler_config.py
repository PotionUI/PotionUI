"""
SDXL K-Diffusion Sampler Configuration

This module handles k-diffusion sampler configuration, sigma schedule generation,
and inpainting wrapper setup for the SDXL pipeline.
"""

import torch
from typing import Optional, Callable
from diffusers.utils import logging

from vendor.k_diffusion import sampling as k_sampling

logger = logging.get_logger(__name__)


class KSamplerX0Inpaint(torch.nn.Module):
    """
    Wrapper for Fooocus-style inpainting that blends masked and unmasked regions during sampling.
    Based on: https://github.com/lllyasviel/Fooocus/blob/main/ldm_patched/modules/samplers.py (KSamplerX0Inpaint)
    """
    def __init__(self, model, latent_image=None, mask=None, noise=None):
        super().__init__()
        self.inner_model = model  # CompVisDenoiser instance
        self.latent_image = latent_image  # Original latent (unmasked image)
        self.mask = mask  # Mask in latent space (1 = inpaint/denoise, 0 = keep original)
        self.noise = noise  # Noise tensor for blending

    def __call__(self, x, sigma, **kwargs):
        """
        Apply Fooocus-style inpainting blending during denoising.

        Args:
            x: Noisy latent tensor
            sigma: Noise level (sigma value from k-diffusion)
            **kwargs: Additional arguments passed to inner model

        Returns:
            Denoised latent with inpainting blend applied
        """
        if self.mask is not None and self.latent_image is not None and self.noise is not None:
            # Invert mask: Fooocus uses 1=inpaint, 0=keep, so latent_mask inverts it
            latent_mask = 1. - self.mask

            # Debug logging for first call
            if not hasattr(self, '_logged'):
                logger.debug(f"[KSAMPLER INPAINT] mask shape={self.mask.shape}, "
                           f"mask range=[{self.mask.min():.3f}, {self.mask.max():.3f}], "
                           f"mask mean={self.mask.mean():.3f}")
                logger.debug(f"[KSAMPLER INPAINT] init_latents shape={self.latent_image.shape}, "
                           f"range=[{self.latent_image.min():.3f}, {self.latent_image.max():.3f}]")
                # Count masked pixels
                masked_pixels = (self.mask > 0.5).float().sum()
                total_pixels = self.mask.numel()
                logger.debug(f"[KSAMPLER INPAINT] Masked pixels: {masked_pixels}/{total_pixels} "
                           f"({100 * masked_pixels / total_pixels:.1f}%)")
                self._logged = True

            # BEFORE DENOISING: Blend noisy latent with original image + scaled noise
            # Masked areas (mask=1, latent_mask=0): Use noisy latent x for denoising
            # Unmasked areas (mask=0, latent_mask=1): Use original + noise scaled by sigma
            # This ensures unmasked areas match the noise level of the denoising process
            sigma_reshaped = sigma.reshape([sigma.shape[0]] + [1] * (len(self.noise.shape) - 1))
            x = x * self.mask + (self.latent_image + self.noise * sigma_reshaped) * latent_mask

        # Run denoising through CompVisDenoiser
        out = self.inner_model(x, sigma, **kwargs)

        if self.mask is not None and self.latent_image is not None:
            # AFTER DENOISING: Blend denoised output with original image
            # Masked areas: Use denoised output
            # Unmasked areas: Keep original image unchanged
            latent_mask = 1. - self.mask
            out = out * self.mask + self.latent_image * latent_mask

        return out


class SDXLSamplerConfig:
    """Handles k-diffusion sampler configuration and sigma schedule generation"""

    @staticmethod
    def wrap_model_for_inpainting(
        model,
        init_latents: Optional[torch.Tensor],
        mask_latents: Optional[torch.Tensor],
        generator: Optional[torch.Generator],
        device: torch.device,
        dtype: torch.dtype
    ):
        """
        Wrap a CompVisDenoiser model with Fooocus-style inpainting if mask is provided.

        Args:
            model: CompVisDenoiser instance
            init_latents: Original image latents for inpainting
            mask_latents: Mask tensor in latent space
            generator: Random generator for noise
            device: Device to generate noise on
            dtype: Data type for noise tensor

        Returns:
            Wrapped model (KSamplerX0Inpaint if inpainting, otherwise original model)
        """
        if mask_latents is not None and init_latents is not None:
            # Generate noise for inpainting blending
            noise_for_inpaint = torch.randn(
                init_latents.shape,
                generator=generator,
                device=device,
                dtype=dtype
            )

            # Wrap the CompVisDenoiser with KSamplerX0Inpaint
            wrapped_model = KSamplerX0Inpaint(
                model=model,
                latent_image=init_latents,
                mask=mask_latents,
                noise=noise_for_inpaint
            )
            logger.debug("[INPAINTING] Wrapped CompVisDenoiser with Fooocus-style inpainting logic")
            return wrapped_model

        return model

    @staticmethod
    def create_k_callback(
        callback_on_step_end: Optional[Callable],
        callback_on_step_end_tensor_inputs: list,
        pipeline_self,
        prompt_embeds: torch.Tensor,
        negative_prompt_embeds: torch.Tensor,
        add_text_embeds: torch.Tensor,
        add_time_ids: torch.Tensor
    ) -> Optional[Callable]:
        """
        Create a k-diffusion callback wrapper for diffusers format.

        Args:
            callback_on_step_end: Optional callback function from pipeline
            callback_on_step_end_tensor_inputs: List of tensor inputs to pass to callback
            pipeline_self: Pipeline instance
            prompt_embeds: Prompt embeddings tensor
            negative_prompt_embeds: Negative prompt embeddings tensor
            add_text_embeds: Additional text embeddings tensor
            add_time_ids: Time IDs tensor

        Returns:
            Callback function compatible with k-diffusion samplers, or None
        """
        if callback_on_step_end is None:
            return None

        def k_callback(info):
            callback_kwargs = {}
            for k in callback_on_step_end_tensor_inputs:
                if k == "latents":
                    callback_kwargs[k] = info["denoised"]
                elif k == "prompt_embeds":
                    callback_kwargs[k] = prompt_embeds
                elif k == "negative_prompt_embeds":
                    callback_kwargs[k] = negative_prompt_embeds
                elif k == "add_text_embeds":
                    callback_kwargs[k] = add_text_embeds
                elif k == "add_time_ids":
                    callback_kwargs[k] = add_time_ids

            step = info["i"]
            timestep = int(info["sigma"] * 1000)  # Rough conversion
            callback_outputs = callback_on_step_end(pipeline_self, step, timestep, callback_kwargs)

        return k_callback

    @staticmethod
    def generate_sigmas(
        scheduler_type: str,
        num_inference_steps: int,
        model,
        device: torch.device
    ) -> torch.Tensor:
        """
        Generate sigma schedule for k-diffusion sampling.

        Args:
            scheduler_type: Type of scheduler ("karras", "normal", "exponential", etc.)
            num_inference_steps: Number of denoising steps
            model: CompVisDenoiser model (for sigma_min/max)
            device: Device to generate sigmas on

        Returns:
            Tensor of sigma values for sampling
        """
        logger.debug(f"[K-DIFFUSION] Generating sigmas: scheduler={scheduler_type}, steps={num_inference_steps}")

        # Get sigma range from model
        sigma_min = model.sigma_min.item() if hasattr(model, 'sigma_min') else 0.0292
        sigma_max = model.sigma_max.item() if hasattr(model, 'sigma_max') else 14.6146

        # Generate sigmas based on scheduler type
        if scheduler_type == "simple":
            # ComfyUI-compatible simple schedule: evenly sample from discrete sigma array
            # This matches ComfyUI's simple_scheduler exactly, using integer indexing
            # from the end of the sigma buffer (large→small) rather than linspace interpolation.
            model_sigmas = model.sigmas  # ascending: sigma_min → sigma_max
            total = len(model_sigmas)
            ss = total / num_inference_steps
            sigs = []
            for x in range(num_inference_steps):
                sigs.append(float(model_sigmas[-(1 + int(x * ss))]))
            sigs.append(0.0)
            sigmas = torch.FloatTensor(sigs).to(device)
            logger.debug(f"[K-DIFFUSION] Using simple (ComfyUI-compatible) schedule")
        elif scheduler_type == "normal":
            # Original k-diffusion schedule using linspace + log-sigma interpolation
            sigmas = model.get_sigmas(num_inference_steps)
            logger.debug(f"[K-DIFFUSION] Using normal (k-diffusion) schedule")
        elif scheduler_type == "karras":
            # Karras schedule - better for advanced samplers like DPM++
            sigmas = k_sampling.get_sigmas_karras(num_inference_steps, sigma_min, sigma_max, rho=7., device=device)
            logger.debug(f"[K-DIFFUSION] Using Karras schedule (rho=7.0)")
        elif scheduler_type == "exponential":
            # Exponential schedule
            sigmas = k_sampling.get_sigmas_exponential(num_inference_steps, sigma_min, sigma_max, device=device)
            logger.debug(f"[K-DIFFUSION] Using exponential schedule")
        elif scheduler_type == "sgm_uniform":
            # SGM uniform schedule (used in some Stability AI models)
            sigmas = k_sampling.get_sigmas_vp(num_inference_steps, device=device)
            logger.debug(f"[K-DIFFUSION] Using SGM uniform schedule")
        elif scheduler_type == "ddim_uniform":
            # DDIM-style uniform schedule
            sigmas = torch.linspace(sigma_max, sigma_min, num_inference_steps + 1, device=device)
            logger.debug(f"[K-DIFFUSION] Using DDIM uniform schedule")
        else:
            # Default fallback to karras
            logger.warning(f"[K-DIFFUSION] Unknown scheduler '{scheduler_type}', falling back to karras")
            sigmas = k_sampling.get_sigmas_karras(num_inference_steps, sigma_min, sigma_max, rho=7., device=device)

        # Log the sigma schedule for debugging
        logger.debug(f"[K-DIFFUSION] Generated sigmas: range=[{sigma_min:.4f}, {sigma_max:.4f}], "
                   f"actual=[{sigmas.min():.6f}, {sigmas.max():.6f}], "
                   f"first={sigmas[0]:.6f}, last={sigmas[-1]:.6f}, total={len(sigmas)}")

        return sigmas

    @staticmethod
    def adjust_sigmas_for_img2img(
        sigmas: torch.Tensor,
        init_latents: torch.Tensor,
        strength: float,
        num_inference_steps: int,
        generator: Optional[torch.Generator],
        device: torch.device,
        dtype: torch.dtype,
        model
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Adjust sigmas and add noise to init_latents for img2img generation.

        Args:
            sigmas: Full sigma schedule
            init_latents: Initial latent tensor from encoded image
            strength: img2img strength (0.0 = no change, 1.0 = full denoising)
            num_inference_steps: Total number of inference steps
            generator: Random generator for noise
            device: Device for tensors
            dtype: Data type for tensors
            model: CompVisDenoiser model (for fallback sigma generation)

        Returns:
            Tuple of (adjusted_sigmas, noisy_latents)
        """
        # Calculate the number of steps to skip based on strength
        init_timestep = min(int(num_inference_steps * strength), num_inference_steps)
        t_start = max(num_inference_steps - init_timestep, 0)

        # Ensure we always have at least 2 steps for img2img (required by k-diffusion)
        effective_steps = num_inference_steps - t_start
        if effective_steps < 2:
            t_start = max(0, num_inference_steps - 2)
            effective_steps = num_inference_steps - t_start
            logger.debug(f"[K-DIFFUSION] Adjusted t_start to {t_start} to ensure minimum 2 steps for img2img")

        # Truncate sigmas to start from the appropriate step
        adjusted_sigmas = sigmas[t_start:]

        # Verify we have valid sigmas
        if len(adjusted_sigmas) < 2:
            logger.error(f"[K-DIFFUSION] img2img failed: only {len(adjusted_sigmas)} sigma(s) available. "
                       f"Need at least 2. Using last 2 sigmas as fallback.")
            sigma_min = model.sigma_min.item() if hasattr(model, 'sigma_min') else 0.0292
            sigma_max = model.sigma_max.item() if hasattr(model, 'sigma_max') else 14.6146
            full_sigmas = k_sampling.get_sigmas_karras(num_inference_steps, sigma_min, sigma_max, rho=7., device=device)
            adjusted_sigmas = full_sigmas[-2:]

        # Check if we have any non-zero sigmas
        if (adjusted_sigmas > 0).sum() == 0:
            logger.error(f"[K-DIFFUSION] All sigmas are zero after truncation. "
                       f"This is a bug. Using minimal denoising with last 2 sigmas.")
            sigma_min = model.sigma_min.item() if hasattr(model, 'sigma_min') else 0.0292
            sigma_max = model.sigma_max.item() if hasattr(model, 'sigma_max') else 14.6146
            full_sigmas = k_sampling.get_sigmas_karras(num_inference_steps, sigma_min, sigma_max, rho=7., device=device)
            adjusted_sigmas = full_sigmas[-2:]

        # Add noise to init_latents based on the starting sigma
        noise = torch.randn(init_latents.shape, generator=generator, device=device, dtype=dtype)
        start_sigma = adjusted_sigmas[0]
        noisy_latents = init_latents + noise * start_sigma

        logger.debug(f"[K-DIFFUSION] img2img mode: strength={strength:.2f}, using {len(adjusted_sigmas)} sigmas, "
                   f"start_sigma={start_sigma:.4f}, t_start={t_start}/{num_inference_steps}")

        return adjusted_sigmas, noisy_latents
