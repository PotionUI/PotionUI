# Copyright 2024 Katherine Crowson, The HuggingFace Team and The InstantX Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Derived from the HuggingFace diffusers community pipeline
# `StableDiffusionXLKDiffusionPipeline` (Apache-2.0). This is a first-party
# PotionUI derivative with substantial modifications: it wires PotionUI's SDXL
# components (input validator, conditioning builder, model wrapper, sampler
# config, post/image processors) and the vendored k-diffusion sampling path.

import inspect
import logging as std_logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union, Tuple

import torch
from transformers import (
    CLIPImageProcessor,
    CLIPTextModel,
    CLIPTextModelWithProjection,
    CLIPTokenizer,
    CLIPVisionModelWithProjection,
)

from diffusers import DiffusionPipeline
from diffusers.image_processor import PipelineImageInput, VaeImageProcessor
from diffusers.loaders import (
    FromSingleFileMixin,
    IPAdapterMixin,
    StableDiffusionXLLoraLoaderMixin,
    TextualInversionLoaderMixin,
)
from diffusers.models import AutoencoderKL, ImageProjection, UNet2DConditionModel
from diffusers.pipelines.pipeline_utils import StableDiffusionMixin
from diffusers.pipelines.stable_diffusion_xl.pipeline_output import StableDiffusionXLPipelineOutput
from diffusers.utils import (
    deprecate,
    logging,
    replace_example_docstring,
)
from diffusers.utils.torch_utils import randn_tensor

# Import SDXL feature classes
from src.pipelines.pipes.generator.sdxl.input_validator import SDXLInputValidator
from src.pipelines.pipes.generator.sdxl.conditioning_builder import SDXLConditioningBuilder
from src.pipelines.pipes.generator.sdxl.model_wrapper import (
    SDXLModelWrapper,
    ControlNetConfig,
    IPAdapterConfig,
    InpaintConfig,
)
from src.pipelines.pipes.generator.sdxl.sampler_config import SDXLSamplerConfig
from src.pipelines.pipes.generator.sdxl.post_processor import SDXLPostProcessor
from src.pipelines.pipes.generator.sdxl.image_processor import SDXLImageProcessor

from vendor.k_diffusion import sampling as k_sampling
from vendor.k_diffusion.external import CompVisDenoiser, CompVisVDenoiser

logger = logging.get_logger(__name__)

EXAMPLE_DOC_STRING = """
    Examples:
        ```py
        >>> import torch
        >>> from diffusers import StableDiffusionXLKDiffusionPipeline

        >>> pipe = StableDiffusionXLKDiffusionPipeline.from_pretrained(
        ...     "stabilityai/stable-diffusion-xl-base-1.0", torch_dtype=torch.float16, variant="fp16"
        ... )
        >>> pipe = pipe.to("cuda")
        >>> prompt = "a photo of an astronaut riding a horse on mars"
        >>> image = pipe(prompt).images[0]
        ```
"""


class StableDiffusionXLKDiffusionPipeline(
    DiffusionPipeline,
    StableDiffusionMixin,
    TextualInversionLoaderMixin,
    IPAdapterMixin,
    StableDiffusionXLLoraLoaderMixin,
    FromSingleFileMixin,
):
    r"""
    Pipeline for text-to-image generation using Stable Diffusion XL with k-diffusion.

    This model inherits from [`DiffusionPipeline`]. Check the superclass documentation for the generic methods
    implemented for all pipelines (downloading, saving, running on a particular device, etc.).

    The pipeline also inherits the following loading methods:
        - [`~loaders.TextualInversionLoaderMixin.load_textual_inversion`] for loading textual inversion embeddings
        - [`~loaders.FromSingleFileMixin.from_single_file`] for loading `.ckpt` files
        - [`~loaders.StableDiffusionXLLoraLoaderMixin.load_lora_weights`] for loading LoRA weights
        - [`~loaders.StableDiffusionXLLoraLoaderMixin.save_lora_weights`] for saving LoRA weights
        - [`~loaders.IPAdapterMixin.load_ip_adapter`] for loading IP Adapters

    Args:
        vae ([`AutoencoderKL`]):
            Variational Auto-Encoder (VAE) model to encode and decode images to and from latent representations.
        text_encoder ([`~transformers.CLIPTextModel`]):
            Frozen text-encoder ([clip-vit-large-patch14](https://huggingface.co/openai/clip-vit-large-patch14)).
        text_encoder_2 ([`~transformers.CLIPTextModelWithProjection`]):
            Second frozen text-encoder
            ([laion/CLIP-ViT-bigG-14-laion2B-39B-b160k](https://huggingface.co/laion/CLIP-ViT-bigG-14-laion2B-39B-b160k)).
        tokenizer ([`~transformers.CLIPTokenizer`]):
            A `CLIPTokenizer` to tokenize text.
        tokenizer_2 ([`~transformers.CLIPTokenizer`]):
            A `CLIPTokenizer` to tokenize text for the second text encoder.
        unet ([`UNet2DConditionModel`]):
            A `UNet2DConditionModel` to denoise the encoded image latents.
        scheduler ([`SchedulerMixin`]):
            A scheduler to be used in combination with `unet` to denoise the encoded image latents. Can be one of
            [`DDIMScheduler`], [`LMSDiscreteScheduler`], or [`PNDMScheduler`].
        image_encoder ([`CLIPVisionModelWithProjection`]):
            Frozen CLIP image-encoder ([laion/CLIP-ViT-H-14-laion2B-s32B-b79K](https://huggingface.co/laion/CLIP-ViT-H-14-laion2B-s32B-b79K)).
        feature_extractor ([`~transformers.CLIPImageProcessor`]):
            A `CLIPImageProcessor` to extract features from generated images; used as inputs to the `image_encoder`.
    """

    model_cpu_offload_seq = "text_encoder->text_encoder_2->unet->vae"
    _optional_components = [
        "tokenizer",
        "tokenizer_2",
        "text_encoder",
        "text_encoder_2",
        "image_encoder",
        "feature_extractor",
    ]
    _callback_tensor_inputs = [
        "latents",
        "prompt_embeds",
        "negative_prompt_embeds",
        "add_text_embeds",
        "add_time_ids",
        "negative_pooled_prompt_embeds",
        "negative_add_time_ids",
    ]

    def __init__(
        self,
        vae: AutoencoderKL,
        text_encoder: CLIPTextModel,
        text_encoder_2: CLIPTextModelWithProjection,
        tokenizer: CLIPTokenizer,
        tokenizer_2: CLIPTokenizer,
        unet: UNet2DConditionModel,
        scheduler: Any,
        controlnet: Union[Any, List[Any]] = None,
        image_encoder: CLIPVisionModelWithProjection = None,
        feature_extractor: CLIPImageProcessor = None,
        force_zeros_for_empty_prompt: bool = True,
        add_watermarker: Optional[bool] = None,
    ):
        super().__init__()

        self.register_modules(
            vae=vae,
            text_encoder=text_encoder,
            text_encoder_2=text_encoder_2,
            tokenizer=tokenizer,
            tokenizer_2=tokenizer_2,
            unet=unet,
            scheduler=scheduler,
            controlnet=controlnet,
            image_encoder=image_encoder,
            feature_extractor=feature_extractor,
        )

        # Ensure VAE is properly registered and available
        if self.vae is None:
            raise ValueError("VAE must be provided and cannot be None")
        self.register_to_config(force_zeros_for_empty_prompt=force_zeros_for_empty_prompt)
        self.vae_scale_factor = 2 ** (len(self.vae.config.block_out_channels) - 1)
        self.image_processor = VaeImageProcessor(vae_scale_factor=self.vae_scale_factor)
        # Control image processor does NOT normalize to [-1, 1] - ControlNet expects [0, 1]
        self.control_image_processor = VaeImageProcessor(
            vae_scale_factor=self.vae_scale_factor, do_convert_rgb=True, do_normalize=False
        )
        self.default_sample_size = self.unet.config.sample_size

        add_watermarker = add_watermarker if add_watermarker is not None else False
        if add_watermarker:
            try:
                from diffusers.pipelines.stable_diffusion_xl.watermark import StableDiffusionXLWatermarker
                self.watermark = StableDiffusionXLWatermarker()
            except ImportError:
                add_watermarker = False
                logger.warning(
                    "Cannot import `StableDiffusionXLWatermarker`. You have disabled the invisible watermark. We highly recommend to install the invisible-watermark library and use the watermark for any production or commercial purposes."
                )
        if not add_watermarker:
            self.watermark = None

    def encode_prompt(
        self,
        prompt: str,
        prompt_2: Optional[str] = None,
        device: Optional[torch.device] = None,
        num_images_per_prompt: int = 1,
        do_classifier_free_guidance: bool = True,
        negative_prompt: Optional[str] = None,
        negative_prompt_2: Optional[str] = None,
        prompt_embeds: Optional[torch.FloatTensor] = None,
        negative_prompt_embeds: Optional[torch.FloatTensor] = None,
        pooled_prompt_embeds: Optional[torch.FloatTensor] = None,
        negative_pooled_prompt_embeds: Optional[torch.FloatTensor] = None,
        lora_scale: Optional[float] = None,
        clip_skip: Optional[int] = None,
    ):
        r"""
        This method is kept for API compatibility but is not used in the PotionUI pipeline.
        Prompt encoding is handled by the PromptEncoderPipe using the CLIP class.

        When using this pipeline with PotionUI, always pass pre-computed embeddings:
        - prompt_embeds
        - negative_prompt_embeds
        - pooled_prompt_embeds
        - negative_pooled_prompt_embeds
        """
        # If embeddings are provided, just process them for the expected format
        if prompt_embeds is not None and negative_prompt_embeds is not None:
            device = device or self._execution_device

            # Ensure correct dtype and device
            prompt_embeds = prompt_embeds.to(dtype=self.text_encoder_2.dtype, device=device)
            negative_prompt_embeds = negative_prompt_embeds.to(dtype=self.text_encoder_2.dtype, device=device)

            # Handle batch size repetition for num_images_per_prompt
            bs_embed, seq_len, _ = prompt_embeds.shape
            prompt_embeds = prompt_embeds.repeat(1, num_images_per_prompt, 1)
            prompt_embeds = prompt_embeds.view(bs_embed * num_images_per_prompt, seq_len, -1)

            if do_classifier_free_guidance:
                seq_len = negative_prompt_embeds.shape[1]
                negative_prompt_embeds = negative_prompt_embeds.repeat(1, num_images_per_prompt, 1)
                negative_prompt_embeds = negative_prompt_embeds.view(bs_embed * num_images_per_prompt, seq_len, -1)

            # Handle pooled embeddings
            if pooled_prompt_embeds is not None:
                pooled_prompt_embeds = pooled_prompt_embeds.repeat(1, num_images_per_prompt).view(
                    bs_embed * num_images_per_prompt, -1
                )
            if negative_pooled_prompt_embeds is not None and do_classifier_free_guidance:
                negative_pooled_prompt_embeds = negative_pooled_prompt_embeds.repeat(1, num_images_per_prompt).view(
                    bs_embed * num_images_per_prompt, -1
                )

            return prompt_embeds, negative_prompt_embeds, pooled_prompt_embeds, negative_pooled_prompt_embeds
        else:
            raise ValueError(
                "This pipeline requires pre-computed embeddings. "
                "Please use the PromptEncoderPipe to encode your prompts before calling this pipeline."
            )

    # Copied from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl.StableDiffusionXLPipeline._get_add_time_ids
    def _get_add_time_ids(
        self, original_size, crops_coords_top_left, target_size, dtype, text_encoder_projection_dim=None
    ):
        return SDXLConditioningBuilder.build_time_ids_with_validation(
            original_size=original_size,
            crops_coords_top_left=crops_coords_top_left,
            target_size=target_size,
            dtype=dtype,
            unet_config=self.unet.config,
            text_encoder_projection_dim=text_encoder_projection_dim
        )

    # Copied from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl.StableDiffusionXLPipeline.upcast_vae
    def upcast_vae(self):
        dtype = self.vae.dtype
        self.vae.to(dtype=torch.float32)
        use_torch_2_0_or_xformers = isinstance(
            self.vae.decoder.mid_block.attentions[0].processor,
            (
                AttnProcessor2_0,
                XFormersAttnProcessor,
            ),
        )
        # if xformers or torch_2_0 is used attention block does not need
        # to be in float32 which can save lots of memory
        if use_torch_2_0_or_xformers:
            self.vae.post_quant_conv.to(dtype)
            self.vae.decoder.conv_in.to(dtype)
            self.vae.decoder.mid_block.to(dtype)

    # Copied from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl.StableDiffusionXLPipeline.check_inputs
    def check_inputs(
        self,
        prompt,
        prompt_2,
        height,
        width,
        callback_steps,
        negative_prompt=None,
        negative_prompt_2=None,
        prompt_embeds=None,
        negative_prompt_embeds=None,
        pooled_prompt_embeds=None,
        negative_pooled_prompt_embeds=None,
        callback_on_step_end_tensor_inputs=None,
    ):
        SDXLInputValidator.validate_pipeline_inputs(
            self._callback_tensor_inputs,
            prompt,
            prompt_2,
            height,
            width,
            callback_steps,
            negative_prompt,
            negative_prompt_2,
            prompt_embeds,
            negative_prompt_embeds,
            pooled_prompt_embeds,
            negative_pooled_prompt_embeds,
            callback_on_step_end_tensor_inputs,
        )

    # Copied from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl.StableDiffusionXLPipeline.prepare_latents
    def prepare_latents(self, batch_size, num_channels_latents, height, width, dtype, device, generator, latents=None):
        return SDXLConditioningBuilder.prepare_initial_latents(
            batch_size=batch_size,
            num_channels_latents=num_channels_latents,
            height=height,
            width=width,
            vae_scale_factor=self.vae_scale_factor,
            dtype=dtype,
            device=device,
            generator=generator,
            latents=latents,
            scheduler_init_noise_sigma=self.scheduler.init_noise_sigma
        )

    @property
    def guidance_scale(self):
        return self._guidance_scale

    @property
    def guidance_rescale(self):
        return self._guidance_rescale

    @property
    def clip_skip(self):
        return self._clip_skip

    @property
    def do_classifier_free_guidance(self):
        return self._guidance_scale > 1 and self._guidance_scale is not None

    @property
    def cross_attention_kwargs(self):
        return self._cross_attention_kwargs

    @property
    def denoising_end(self):
        return self._denoising_end

    @property
    def num_timesteps(self):
        return self._num_timesteps

    @torch.no_grad()
    @replace_example_docstring(EXAMPLE_DOC_STRING)
    def __call__(
        self,
        prompt: Union[str, List[str]] = None,
        prompt_2: Optional[Union[str, List[str]]] = None,
        image: Optional[PipelineImageInput] = None,
        mask_image: Optional[PipelineImageInput] = None,
        control_image: Optional[Union[PipelineImageInput, List[PipelineImageInput]]] = None,
        height: Optional[int] = None,
        width: Optional[int] = None,
        strength: float = 0.8,
        num_inference_steps: int = 50,
        timesteps: List[int] = None,
        sigmas: List[float] = None,
        denoising_end: Optional[float] = None,
        guidance_scale: float = 5.0,
        negative_prompt: Optional[Union[str, List[str]]] = None,
        negative_prompt_2: Optional[Union[str, List[str]]] = None,
        num_images_per_prompt: Optional[int] = 1,
        eta: float = 0.0,
        generator: Optional[Union[torch.Generator, List[torch.Generator]]] = None,
        latents: Optional[torch.FloatTensor] = None,
        prompt_embeds: Optional[torch.FloatTensor] = None,
        negative_prompt_embeds: Optional[torch.FloatTensor] = None,
        pooled_prompt_embeds: Optional[torch.FloatTensor] = None,
        negative_pooled_prompt_embeds: Optional[torch.FloatTensor] = None,
        ip_adapter_image: Optional[PipelineImageInput] = None,
        ip_adapter_image_embeds: Optional[List[torch.FloatTensor]] = None,
        controlnet_conditioning_scale: Union[float, List[float]] = 1.0,
        control_guidance_start: Union[float, List[float]] = 0.0,
        control_guidance_end: Union[float, List[float]] = 1.0,
        output_type: Optional[str] = "pil",
        return_dict: bool = True,
        cross_attention_kwargs: Optional[Dict[str, Any]] = None,
        guidance_rescale: float = 0.0,
        original_size: Optional[Tuple[int, int]] = None,
        crops_coords_top_left: Tuple[int, int] = (0, 0),
        target_size: Optional[Tuple[int, int]] = None,
        negative_original_size: Optional[Tuple[int, int]] = None,
        negative_crops_coords_top_left: Tuple[int, int] = (0, 0),
        negative_target_size: Optional[Tuple[int, int]] = None,
        clip_skip: Optional[int] = None,
        callback_on_step_end: Optional[Callable[[int, int, Dict], None]] = None,
        callback_on_step_end_tensor_inputs: List[str] = ["latents"],
        sampler: str = "dpmpp_2m",
        scheduler: str = "karras",
        **kwargs,
    ):
        r"""
        Function invoked when calling the pipeline for generation.

        Args:
            prompt (`str` or `List[str]`, *optional*):
                The prompt or prompts to guide the image generation. If not defined, one has to pass `prompt_embeds`.
                instead.
            prompt_2 (`str` or `List[str]`, *optional*):
                The prompt or prompts to be sent to the `tokenizer_2` and `text_encoder_2`. If not defined, `prompt` is
                used in both text-encoders
            image (`PipelineImageInput`, *optional*):
                `Image`, or tensor representing an image batch, that will be used as the starting point for the
                process. If provided, the pipeline will perform image-to-image generation.
            height (`int`, *optional*, defaults to self.unet.config.sample_size * self.vae_scale_factor):
                The height in pixels of the generated image. This is set to 1024 by default for the best results.
                Anything below 512 pixels won't work well for
                [stabilityai/stable-diffusion-xl-base-1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)
                and checkpoints that are not specifically fine-tuned on low resolutions.
            width (`int`, *optional*, defaults to self.unet.config.sample_size * self.vae_scale_factor):
                The width in pixels of the generated image. This is set to 1024 by default for the best results.
                Anything below 512 pixels won't work well for
                [stabilityai/stable-diffusion-xl-base-1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)
                and checkpoints that are not specifically fine-tuned on low resolutions.
            strength (`float`, *optional*, defaults to 0.8):
                Conceptually, indicates how much to transform the reference `image`. Must be between 0 and 1.
                `image` will be used as a starting point, adding more noise to it the larger the `strength`. The
                number of denoising steps depends on the amount of noise initially added. When `strength` is 1, added
                noise will be maximum and the denoising process will run for the full number of iterations specified in
                `num_inference_steps`. A value of 1, therefore, essentially ignores `image`. Only used when `image` is provided.
            num_inference_steps (`int`, *optional*, defaults to 50):
                The number of denoising steps. More denoising steps usually lead to a higher quality image at the
                expense of slower inference.
            timesteps (`List[int]`, *optional*):
                Custom timesteps to use for the denoising process with schedulers which support a `timesteps` argument
                in their `set_timesteps` method. If not defined, the default behavior when `num_inference_steps` is
                passed will be used. Must be in descending order.
            sigmas (`List[float]`, *optional*):
                Custom sigmas to use for the denoising process with schedulers which support a `sigmas` argument in
                their `set_timesteps` method. If not defined, the default behavior when `num_inference_steps` is passed
                will be used.
            denoising_end (`float`, *optional*):
                When to end the denoising process with schedulers which support a `denoising_end` argument in their
                `set_timesteps` method. Must be between 0 and 1 and corresponds to the proportion of the original
                denoising process to be bypassed.
            guidance_scale (`float`, *optional*, defaults to 5.0):
                Guidance scale as defined in [Classifier-Free Diffusion Guidance](https://arxiv.org/abs/2207.12598).
                `guidance_scale` is defined as `w` of equation 2. of [Imagen
                Paper](https://arxiv.org/pdf/2205.11487.pdf). Guidance scale is enabled by setting `guidance_scale >
                1`. Higher guidance scale encourages to generate images that are closely linked to the text `prompt`,
                usually at the expense of lower image quality.
            negative_prompt (`str` or `List[str]`, *optional*):
                The prompt or prompts not to guide the image generation. If not defined, one has to pass
                `negative_prompt_embeds` instead. Ignored when not using guidance (i.e., ignored if `guidance_scale` is
                less than `1`).
            negative_prompt_2 (`str` or `List[str]`, *optional*):
                The prompt or prompts not to guide the image generation to be sent to `tokenizer_2` and
                `text_encoder_2`. If not defined, `negative_prompt` is used in both text-encoders.
            num_images_per_prompt (`int`, *optional*, defaults to 1):
                The number of images to generate per prompt.
            eta (`float`, *optional*, defaults to 0.0):
                Corresponds to parameter eta (η) in the DDIM paper: https://arxiv.org/abs/2010.02502. Only applies to
                [`schedulers.DDIMScheduler`], will be ignored for others.
            generator (`torch.Generator` or `List[torch.Generator]`, *optional*):
                One or a list of [torch generator(s)](https://pytorch.org/docs/stable/generated/torch.Generator.html)
                to make generation deterministic.
            latents (`torch.FloatTensor`, *optional*):
                Pre-generated noisy latents, sampled from a Gaussian distribution, to be used as inputs for image
                generation. Can be used to tweak the same generation with different prompts. If not provided, a latents
                tensor will be generated by sampling using the supplied random `generator`.
            prompt_embeds (`torch.FloatTensor`, *optional*):
                Pre-generated text embeddings. Can be used to easily tweak text inputs, *e.g.* prompt weighting. If not
                provided, text embeddings will be generated from `prompt` input argument.
            negative_prompt_embeds (`torch.FloatTensor`, *optional*):
                Pre-generated negative text embeddings. Can be used to easily tweak text inputs, *e.g.* prompt
                weighting. If not provided, negative_prompt_embeds will be generated from `negative_prompt` input
                argument.
            pooled_prompt_embeds (`torch.FloatTensor`, *optional*):
                Pre-generated pooled text embeddings. Can be used to easily tweak text inputs, *e.g.* prompt weighting.
                If not provided, pooled text embeddings will be generated from `prompt` input argument.
            negative_pooled_prompt_embeds (`torch.FloatTensor`, *optional*):
                Pre-generated negative pooled text embeddings. Can be used to easily tweak text inputs, *e.g.* prompt
                weighting. If not provided, pooled negative_prompt_embeds will be generated from `negative_prompt`
                input argument.
            ip_adapter_image: (`PipelineImageInput`, *optional*): Optional image input to work with IP Adapters.
            ip_adapter_image_embeds (`List[torch.FloatTensor]`, *optional*):
                Pre-generated image embeddings for IP-Adapter. It should be a list of length same as number of
                IP-adapters. Each element should be a tensor of shape `(batch_size, num_images, emb_dim)`. It should
                contain the negative image embedding if `do_classifier_free_guidance` is set to `True`. If not
                provided, embeddings are computed from the `ip_adapter_image` input argument.
            output_type (`str`, *optional*, defaults to `"pil"`):
                The output format of the generate image. Choose between
                [PIL](https://pillow.readthedocs.io/en/stable/): `PIL.Image.Image` or `np.array`.
            return_dict (`bool`, *optional*, defaults to `True`):
                Whether or not to return a [`~pipelines.stable_diffusion_xl.StableDiffusionXLPipelineOutput`] instead
                of a plain tuple.
            cross_attention_kwargs (`dict`, *optional*):
                A kwargs dictionary that if specified is passed along to the `AttentionProcessor` as defined under
                `self.processor` in
                [diffusers.models.attention_processor](https://github.com/huggingface/diffusers/blob/main/src/diffusers/models/attention_processor.py).
            guidance_rescale (`float`, *optional*, defaults to 0.0):
                Guidance rescale factor proposed by [Common Diffusion Noise Schedules and Sample Steps are
                Flawed](https://arxiv.org/pdf/2305.08891.pdf) `guidance_scale` is defined as `φ` in equation 16. of
                [Common Diffusion Noise Schedules and Sample Steps are Flawed](https://arxiv.org/pdf/2305.08891.pdf).
                Guidance rescale factor should fix overexposure when using zero terminal SNR.
            original_size (`Tuple[int]`, *optional*, defaults to (1024, 1024)):
                If `original_size` is not the same as `target_size` the image will appear to be down- or upsampled.
                `original_size` defaults to `(height, width)` if not specified. Part of SDXL's micro-conditioning as
                explained in section 2.2 of
                [https://huggingface.co/papers/2307.01952](https://huggingface.co/papers/2307.01952).
            crops_coords_top_left (`Tuple[int]`, *optional*, defaults to (0, 0)):
                `crops_coords_top_left` can be used to generate an image that appears to be "cropped" from the position
                `crops_coords_top_left` downwards. Favorable, well-centered images are usually achieved by setting
                `crops_coords_top_left` to (0, 0). Part of SDXL's micro-conditioning as explained in section 2.2 of
                [https://huggingface.co/papers/2307.01952](https://huggingface.co/papers/2307.01952).
            target_size (`Tuple[int]`, *optional*, defaults to (1024, 1024)):
                For most cases, `target_size` should be set to the desired height and width of the generated image. If
                not specified it will default to `(height, width)`. Part of SDXL's micro-conditioning as explained in
                section 2.2 of [https://huggingface.co/papers/2307.01952](https://huggingface.co/papers/2307.01952).
            negative_original_size (`Tuple[int]`, *optional*, defaults to (1024, 1024)):
                To negatively condition the generation process based on a specific image resolution. Part of SDXL's
                micro-conditioning as explained in section 2.2 of
                [https://huggingface.co/papers/2307.01952](https://huggingface.co/papers/2307.01952). For more
                information, refer to this issue thread: https://github.com/huggingface/diffusers/issues/4208.
            negative_crops_coords_top_left (`Tuple[int]`, *optional*, defaults to (0, 0)):
                To negatively condition the generation process based on a specific crop coordinates. Part of SDXL's
                micro-conditioning as explained in section 2.2 of
                [https://huggingface.co/papers/2307.01952](https://huggingface.co/papers/2307.01952). For more
                information, refer to this issue thread: https://github.com/huggingface/diffusers/issues/4208.
            negative_target_size (`Tuple[int]`, *optional*, defaults to (1024, 1024)):
                To negatively condition the generation process based on a target image resolution. It should be as same
                as the `target_size` for most cases. Part of SDXL's micro-conditioning as explained in section 2.2 of
                [https://huggingface.co/papers/2307.01952](https://huggingface.co/papers/2307.01952). For more
                information, refer to this issue thread: https://github.com/huggingface/diffusers/issues/4208.
            clip_skip (`int`, *optional*):
                Number of layers to be skipped from CLIP while computing the prompt embeddings. A value of 1 means that
                the output of the pre-final layer will be used for computing the prompt embeddings.
            callback_on_step_end (`Callable`, *optional*):
                A function that calls at the end of each denoising step during the inference. The function is called
                with the following arguments: `callback_on_step_end(self, step, timestep, callback_kwargs)`.
                `callback_kwargs` will include a list of all tensors as specified by `callback_on_step_end_tensor_inputs`.
            callback_on_step_end_tensor_inputs (`List`, *optional*):
                The list of tensor inputs for the `callback_on_step_end` function. The tensors specified in the list
                will be passed as `callback_kwargs` argument. You will only be able to include variables listed in the
                `._callback_tensor_inputs` attribute of your pipeline class.
            sampler (`str`, *optional*, defaults to `"dpmpp_2m"`):
                Choose the sampler to use for generation. Available options are "euler", "euler_ancestral", "heun",
                "dpm_2", "dpm_2_ancestral", "lms", "dpmpp_2s_ancestral", "dpmpp_sde", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m_sde".

        Examples:

        Returns:
            [`~pipelines.stable_diffusion_xl.StableDiffusionXLPipelineOutput`] or `tuple`:
            [`~pipelines.stable_diffusion_xl.StableDiffusionXLPipelineOutput`] if `return_dict` is True, otherwise a
            `tuple`. When returning a tuple, the first element is a list with the generated images.
        """

        callback = kwargs.pop("callback", None)
        callback_steps = kwargs.pop("callback_steps", None)

        # Extract hooks
        hooks = kwargs.pop("hooks", [])

        if callback is not None:
            deprecate(
                "callback",
                "1.0.0",
                "Passing `callback` as an input argument to `__call__` is deprecated, consider using `callback_on_step_end`",
            )
        if callback_steps is not None:
            deprecate(
                "callback_steps",
                "1.0.0",
                "Passing `callback_steps` as an input argument to `__call__` is deprecated, consider using `callback_on_step_end`",
            )

        # 0. Default height and width to unet
        height = height or self.default_sample_size * self.vae_scale_factor
        width = width or self.default_sample_size * self.vae_scale_factor

        original_size = original_size or (height, width)
        target_size = target_size or (height, width)

        # 1. Check inputs. Raise error if not correct
        self.check_inputs(
            prompt,
            prompt_2,
            height,
            width,
            callback_steps,
            negative_prompt,
            negative_prompt_2,
            prompt_embeds,
            negative_prompt_embeds,
            pooled_prompt_embeds,
            negative_pooled_prompt_embeds,
            callback_on_step_end_tensor_inputs,
        )

        self._guidance_scale = guidance_scale
        self._guidance_rescale = guidance_rescale
        self._clip_skip = clip_skip
        self._cross_attention_kwargs = cross_attention_kwargs
        self._denoising_end = denoising_end

        # 2. Define call parameters
        if prompt is not None and isinstance(prompt, str):
            batch_size = 1
        elif prompt is not None and isinstance(prompt, list):
            batch_size = len(prompt)
        else:
            batch_size = prompt_embeds.shape[0]

        device = self._execution_device

        # 3. Process embeddings (encoding should be done by PromptEncoderPipe)
        if prompt_embeds is None or negative_prompt_embeds is None:
            raise ValueError(
                "This pipeline requires pre-computed embeddings. "
                "Please use the PromptEncoderPipe to encode your prompts before calling this pipeline."
            )

        # Ensure embeddings are properly formatted
        lora_scale = (
            self.cross_attention_kwargs.get("scale", None) if self.cross_attention_kwargs is not None else None
        )

        (
            prompt_embeds,
            negative_prompt_embeds,
            pooled_prompt_embeds,
            negative_pooled_prompt_embeds,
        ) = self.encode_prompt(
            prompt=prompt,
            prompt_2=prompt_2,
            device=device,
            num_images_per_prompt=num_images_per_prompt,
            do_classifier_free_guidance=self.do_classifier_free_guidance,
            negative_prompt=negative_prompt,
            negative_prompt_2=negative_prompt_2,
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            pooled_prompt_embeds=pooled_prompt_embeds,
            negative_pooled_prompt_embeds=negative_pooled_prompt_embeds,
            lora_scale=lora_scale,
            clip_skip=self.clip_skip,
        )

        # 4. Prepare timesteps
        timesteps, num_inference_steps = retrieve_timesteps(self.scheduler, num_inference_steps, device, timesteps, sigmas)

        # 5. Prepare latent variables
        num_channels_latents = self.unet.config.in_channels

        # Process input image for img2img if provided
        init_latents = None
        if image is not None:
            init_latents = SDXLImageProcessor.preprocess_image_for_img2img(
                image, self.vae, self.image_processor, height, width,
                device, prompt_embeds.dtype, generator,
                batch_size, num_images_per_prompt
            )

        # Process mask for inpainting if provided
        mask_latents = None
        if mask_image is not None:
            mask_latents = SDXLImageProcessor.preprocess_mask_for_inpainting(
                mask_image, height, width, self.vae_scale_factor,
                device, prompt_embeds.dtype, batch_size, num_images_per_prompt
            )

        latents = self.prepare_latents(
            batch_size * num_images_per_prompt,
            num_channels_latents,
            height,
            width,
            prompt_embeds.dtype,
            device,
            generator,
            latents,
        )

        # 6. Prepare added time ids & embeddings
        add_text_embeds = pooled_prompt_embeds
        if self.text_encoder_2 is None:
            text_encoder_projection_dim = int(pooled_prompt_embeds.shape[-1])
        else:
            text_encoder_projection_dim = self.text_encoder_2.config.projection_dim

        add_time_ids = self._get_add_time_ids(
            original_size,
            crops_coords_top_left,
            target_size,
            dtype=prompt_embeds.dtype,
            text_encoder_projection_dim=text_encoder_projection_dim,
        )
        if negative_original_size is not None and negative_target_size is not None:
            negative_add_time_ids = self._get_add_time_ids(
                negative_original_size,
                negative_crops_coords_top_left,
                negative_target_size,
                dtype=prompt_embeds.dtype,
                text_encoder_projection_dim=text_encoder_projection_dim,
            )
        else:
            negative_add_time_ids = add_time_ids

        if self.do_classifier_free_guidance:
            cfg_dict = SDXLConditioningBuilder.prepare_for_cfg(
                prompt_embeds, negative_prompt_embeds, add_text_embeds,
                negative_pooled_prompt_embeds, add_time_ids, negative_add_time_ids
            )
            prompt_embeds, add_text_embeds, add_time_ids = (
                cfg_dict["prompt_embeds"], cfg_dict["pooled_prompt_embeds"], cfg_dict["time_ids"]
            )

        prompt_embeds = prompt_embeds.to(device)
        add_text_embeds = add_text_embeds.to(device)
        add_time_ids = add_time_ids.to(device).repeat(batch_size * num_images_per_prompt, 1)

        if ip_adapter_image is not None or ip_adapter_image_embeds is not None:
            image_embeds = self.prepare_ip_adapter_image_embeds(
                ip_adapter_image,
                ip_adapter_image_embeds,
                device,
                batch_size * num_images_per_prompt,
                self.do_classifier_free_guidance,
            )
        else:
            image_embeds = None

        # 6.5 Prepare ControlNet control images if provided
        # Store on CPU to save VRAM - will be moved to GPU only when needed
        control_image_tensors = None
        if self.controlnet is not None and control_image is not None:
            # Process control images to tensors
            if not isinstance(control_image, list):
                control_image = [control_image]

            control_image_tensors = []
            for ctrl_img in control_image:
                # If PIL Image, convert to tensor using control_image_processor (no normalization!)
                if hasattr(ctrl_img, 'convert'):
                    ctrl_img = self.control_image_processor.preprocess(ctrl_img, height=height, width=width)

                # Store on CPU with proper dtype - will be moved to GPU during inference
                ctrl_img = ctrl_img.to(device='cpu', dtype=prompt_embeds.dtype)
                control_image_tensors.append(ctrl_img)

            # For single controlnet, use single image; for multi-controlnet, use list
            if len(control_image_tensors) == 1:
                control_image_tensors = control_image_tensors[0]

        # Note: Don't convert conditioning scales to lists here!
        # Single ControlNet expects float, MultiControlNet expects list.
        # The model code (txt2img_controlnet) already handles this correctly.

        # 7. k-diffusion sampling
        # Prepare inpaint head model path if inpainting is enabled
        inpaint_head_model_path = None
        if mask_latents is not None and init_latents is not None:
            # Fooocus inpaint head model - lives under the admin-configured model
            # depot ("models_dir" setting), never a path relative to this source
            # file (that used to resolve into the checkout itself, and - since
            # `os` was never imported in this module - raised NameError before it
            # even got that far). `generator/sdxl` fetched it into exactly this
            # path before starting the generation.
            from src.platform.settings.repository import SettingRepository
            from src.pipelines.pipes.generator.sdxl.inpaint_head import inpaint_head_path

            model_dir_setting = SettingRepository().get_setting_by_key('models_dir')
            models_dir = model_dir_setting.get_typed_value() if model_dir_setting else "models"
            inpaint_head_model_path = str(inpaint_head_path(models_dir))

        # Create model wrapper with grouped configuration objects
        model_wrapper = SDXLModelWrapper(
            unet=self.unet,
            scheduler=self.scheduler,
            prompt_embeds=prompt_embeds,
            add_text_embeds=add_text_embeds,
            add_time_ids=add_time_ids,
            num_inference_steps=num_inference_steps,
            guidance_scale=self.guidance_scale,
            guidance_rescale=self.guidance_rescale,
            do_classifier_free_guidance=self.do_classifier_free_guidance,
            cross_attention_kwargs=self.cross_attention_kwargs,
            controlnet_config=ControlNetConfig(
                controlnet=self.controlnet,
                control_image=control_image_tensors,
                controlnet_conditioning_scale=controlnet_conditioning_scale,
                control_guidance_start=control_guidance_start,
                control_guidance_end=control_guidance_end,
            ) if self.controlnet is not None else None,
            ip_adapter_config=IPAdapterConfig(
                ip_adapter_image=ip_adapter_image,
                ip_adapter_image_embeds=ip_adapter_image_embeds,
                image_embeds=image_embeds,
            ) if (ip_adapter_image is not None or ip_adapter_image_embeds is not None) else None,
            inpaint_config=InpaintConfig(
                init_latents=init_latents,
                mask_image=mask_latents,
                noise=None,
                inpaint_head_model_path=inpaint_head_model_path,
            ) if (mask_latents is not None and init_latents is not None) else None,
            hooks=hooks,
        )

        # Create k-diffusion model wrapper
        # quantize=True matches ComfyUI behavior: snap sigma→timestep to nearest integer
        # rather than using continuous interpolation. This prevents subtle numerical
        # drift in the UNet's sinusoidal embeddings that can cause oversaturation.
        if hasattr(self.scheduler.config, 'prediction_type') and self.scheduler.config.prediction_type == "v_prediction":
            model = CompVisVDenoiser(model_wrapper, quantize=True)
        else:
            model = CompVisDenoiser(model_wrapper, quantize=True)

        logger.debug(f"[PIPELINE] Denoiser: {type(model).__name__}, "
                    f"sigma_min={model.sigma_min:.4f}, sigma_max={model.sigma_max:.4f}")

        # Wrap with Fooocus-style inpainting if mask is provided
        model = SDXLSamplerConfig.wrap_model_for_inpainting(
            model, init_latents, mask_latents, generator, device, prompt_embeds.dtype
        )

        # Move model to correct device to ensure log_sigmas buffer is on same device as inputs
        model = model.to(device)

        # Setup callback wrapper for diffusers format
        k_callback = SDXLSamplerConfig.create_k_callback(
            callback_on_step_end, callback_on_step_end_tensor_inputs, self,
            prompt_embeds, negative_prompt_embeds, add_text_embeds, add_time_ids
        )

        # Generate sigma schedule
        sigmas = SDXLSamplerConfig.generate_sigmas(scheduler, num_inference_steps, model, device)

        # Correct noise scaling for k-diffusion sampling.
        # ComfyUI explicitly scales initial noise by sigmas[0] (sigma_max).
        # Our prepare_latents() scales by scheduler.init_noise_sigma, which differs:
        #   - EulerDiscreteScheduler: sqrt(sigma_max^2 + 1) — always ~0.23% high
        #   - DDIMScheduler/DDPMScheduler: hardcoded 1.0 (WRONG for k-diffusion!)
        # The k-diffusion convention: x_T = noise * sigma_max, where x = clean + noise * sigma.
        # Rescale exactly and unconditionally (txt2img only — img2img handles its
        # own noise scaling via adjust_sigmas).
        init_sigma = float(self.scheduler.init_noise_sigma)
        target_sigma = float(sigmas[0])
        logger.debug(f"[PIPELINE] Scheduler: {type(self.scheduler).__name__}, "
                    f"init_noise_sigma={init_sigma:.6f}, "
                    f"k-diffusion sigmas[0]={target_sigma:.6f}")
        # Latent tensor stats gated on DEBUG — the reductions force GPU syncs
        # and this runs per generation / per detailer tile.
        _debug = logger.isEnabledFor(std_logging.DEBUG)
        if _debug:
            logger.debug(f"[PIPELINE] Initial latents (before correction): min={latents.min():.4f}, "
                         f"max={latents.max():.4f}, std={latents.std():.4f}")

        if image is None and target_sigma > 0 and init_sigma > 0 and init_sigma != target_sigma:
            latents = latents * (target_sigma / init_sigma)
            logger.debug(f"[PIPELINE] Noise scale corrected exactly: {init_sigma:.6f} → {target_sigma:.6f}")

        # Log full sigma schedule for ComfyUI comparison
        sigma_list = [f"{float(s):.4f}" for s in sigmas[:5]]
        sigma_end = [f"{float(s):.4f}" for s in sigmas[-3:]]
        logger.debug(f"[PIPELINE] Sigma schedule ({len(sigmas)} values): "
                    f"[{', '.join(sigma_list)}, ..., {', '.join(sigma_end)}]")

        # Cast latents to float32 for k-diffusion sampling precision.
        # ComfyUI runs sampling math (derivatives, noise, sigma scaling) in float32.
        # The UNet still runs in its native dtype (float16) - model_wrapper handles casting.
        original_dtype = latents.dtype
        latents = latents.float()
        if _debug:
            logger.debug(f"[PIPELINE] Latents after correction+cast: std={latents.std():.4f}, dtype={latents.dtype} (was {original_dtype})")

        # For img2img, adjust sigmas based on strength and add noise to init_latents
        if image is not None and init_latents is not None:
            sigmas, latents = SDXLSamplerConfig.adjust_sigmas_for_img2img(
                sigmas, init_latents, strength, num_inference_steps,
                generator, device, prompt_embeds.dtype, model
            )

        # Run k-diffusion sampling with the chosen sampler
        sampler_func = getattr(k_sampling, f"sample_{sampler}", k_sampling.sample_dpmpp_2m)
        latents = sampler_func(
            model,
            latents,
            sigmas,
            callback=k_callback,
            disable=False
        )

        # Log final latent stats after sampling (before VAE decode)
        if _debug:
            logger.debug(f"[PIPELINE] Sampling complete. Final latents: min={latents.min():.4f}, "
                         f"max={latents.max():.4f}, std={latents.std():.4f}")

        # Clean up ControlNet-related tensors after generation
        model_wrapper.cleanup()

        # 8. Post-processing
        image = SDXLPostProcessor.decode_latents(
            vae=self.vae,
            latents=latents,
            output_type=output_type,
            watermark=self.watermark,
            image_processor=self.image_processor,
            upcast_vae_func=self.upcast_vae,
        )

        # Offload all models
        self.maybe_free_model_hooks()

        if not return_dict:
            return (image,)

        return StableDiffusionXLPipelineOutput(images=image)

    def img2img(
        self,
        prompt: Union[str, List[str]] = None,
        prompt_2: Optional[Union[str, List[str]]] = None,
        image: Optional[PipelineImageInput] = None,
        height: Optional[int] = None,
        width: Optional[int] = None,
        strength: float = 0.8,
        num_inference_steps: int = 50,
        timesteps: List[int] = None,
        sigmas: List[float] = None,
        denoising_end: Optional[float] = None,
        guidance_scale: float = 5.0,
        negative_prompt: Optional[Union[str, List[str]]] = None,
        negative_prompt_2: Optional[Union[str, List[str]]] = None,
        num_images_per_prompt: Optional[int] = 1,
        eta: float = 0.0,
        generator: Optional[Union[torch.Generator, List[torch.Generator]]] = None,
        latents: Optional[torch.FloatTensor] = None,
        prompt_embeds: Optional[torch.FloatTensor] = None,
        negative_prompt_embeds: Optional[torch.FloatTensor] = None,
        pooled_prompt_embeds: Optional[torch.FloatTensor] = None,
        negative_pooled_prompt_embeds: Optional[torch.FloatTensor] = None,
        ip_adapter_image: Optional[PipelineImageInput] = None,
        ip_adapter_image_embeds: Optional[List[torch.FloatTensor]] = None,
        output_type: Optional[str] = "pil",
        return_dict: bool = True,
        cross_attention_kwargs: Optional[Dict[str, Any]] = None,
        guidance_rescale: float = 0.0,
        original_size: Optional[Tuple[int, int]] = None,
        crops_coords_top_left: Tuple[int, int] = (0, 0),
        target_size: Optional[Tuple[int, int]] = None,
        negative_original_size: Optional[Tuple[int, int]] = None,
        negative_crops_coords_top_left: Tuple[int, int] = (0, 0),
        negative_target_size: Optional[Tuple[int, int]] = None,
        clip_skip: Optional[int] = None,
        callback_on_step_end: Optional[Callable[[int, int, Dict], None]] = None,
        callback_on_step_end_tensor_inputs: List[str] = ["latents"],
        sampler: str = "dpmpp_2m",
        scheduler: str = "karras",
        **kwargs,
    ):
        r"""
        Function invoked when calling the pipeline for image-to-image generation.

        Refer to the documentation of the `__call__` method for parameter descriptions.
        """
        return self.__call__(
            prompt=prompt,
            prompt_2=prompt_2,
            image=image,
            height=height,
            width=width,
            strength=strength,
            num_inference_steps=num_inference_steps,
            timesteps=timesteps,
            sigmas=sigmas,
            denoising_end=denoising_end,
            guidance_scale=guidance_scale,
            negative_prompt=negative_prompt,
            negative_prompt_2=negative_prompt_2,
            num_images_per_prompt=num_images_per_prompt,
            eta=eta,
            generator=generator,
            latents=latents,
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            pooled_prompt_embeds=pooled_prompt_embeds,
            negative_pooled_prompt_embeds=negative_pooled_prompt_embeds,
            ip_adapter_image=ip_adapter_image,
            ip_adapter_image_embeds=ip_adapter_image_embeds,
            output_type=output_type,
            return_dict=return_dict,
            cross_attention_kwargs=cross_attention_kwargs,
            guidance_rescale=guidance_rescale,
            original_size=original_size,
            crops_coords_top_left=crops_coords_top_left,
            target_size=target_size,
            negative_original_size=negative_original_size,
            negative_crops_coords_top_left=negative_crops_coords_top_left,
            negative_target_size=negative_target_size,
            clip_skip=clip_skip,
            callback_on_step_end=callback_on_step_end,
            callback_on_step_end_tensor_inputs=callback_on_step_end_tensor_inputs,
            sampler=sampler,
            scheduler=scheduler,
            **kwargs,
        )


# Import necessary dependencies that might be missing
try:
    from diffusers.utils import USE_PEFT_BACKEND
except ImportError:
    USE_PEFT_BACKEND = False

try:
    from diffusers.pipelines.pipeline_utils import retrieve_timesteps
except ImportError:
    def retrieve_timesteps(scheduler, num_inference_steps, device, timesteps=None, sigmas=None):
        if timesteps is not None:
            return timesteps, len(timesteps)
        elif sigmas is not None:
            scheduler.set_timesteps(sigmas=sigmas, device=device)
            return scheduler.timesteps, len(scheduler.timesteps)
        else:
            scheduler.set_timesteps(num_inference_steps, device=device)
            return scheduler.timesteps, num_inference_steps

try:
    from diffusers.models.attention_processor import AttnProcessor2_0, XFormersAttnProcessor
except ImportError:
    # Fallback empty classes
    class AttnProcessor2_0:
        pass
    class XFormersAttnProcessor:
        pass

