from typing import Dict, Any, List

from src.pipelines.models import Model
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.contracts import GenerationInput, GenerationInputItem
from src.pipelines.pipes._shared.generation.seed_plan import plan_seeds
from src.pipelines.pipes._shared.generation.generator_base import emit_gallery

from src.pipelines.pipes.generator.sdxl.input_validator import SDXLInputValidator
from src.pipelines.pipes.generator.sdxl.inpaint_head import (
    INPAINT_HEAD_FILENAME,
    INPAINT_HEAD_SUBDIR,
    INPAINT_HEAD_URL,
)


class GeneratorSDXLPipe(BasePipe):
    """SDXL-optimized generator pipe with modern refactored architecture"""

    name = "generator"
    description = "SDXL-optimized generator pipe"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "mode": "txt2img",
            "model": None,
            "vae": None,
            "steps": 25,
            "cfg": 6.0,
            "sampler": "DPMPP_2M",
            "scheduler": "karras",
            "seed": -1,
            "resolution": "1024x1024",
            "quantity": 1,
            "clip_skip": 2,
            "denoise": 0.8,
            "embeddings": {},
            # Guidance rescale
            "guidance_rescale": 0.0,
            # Inpainting parameters
            "inpaint_mode": False,
            "mask_blur": 4,
        }

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable
    ) -> PipeOutput:
        """
        Process SDXL generation using refactored modular architecture.

        This implementation uses extracted modules for:
        - Input validation
        - Parameter adaptation
        - ControlNet orchestration
        """
        # Extract and normalize inputs
        model: Model = pipe_input.input["model"]
        image = pipe_input.input.get("image", [])
        conditioning = pipe_input.input.get("conditioning", [])
        seeds = pipe_input.input.get("seed", [])
        controlnets = pipe_input.input.get("controlnet", [])
        control_images = pipe_input.input.get("control_image", [])

        # Normalize to lists
        if image and not isinstance(image, list):
            image = [image]
        if conditioning and not isinstance(conditioning, list):
            conditioning = [conditioning]
        if seeds and not isinstance(seeds, list):
            seeds = [seeds]

        # Validate conditioning FIRST - fail fast if missing
        if not conditioning:
            raise ValueError(
                "Conditioning is required but was not provided. "
                "Ensure the prompt encoder pipe ran successfully before the generator pipe."
            )

        # Validate conditioning structure
        SDXLInputValidator.validate_conditioning(conditioning[0])

        # Determine mode and setup
        mode = "img2img" if image else "txt2img"
        using_controlnet = controlnets and len(controlnets) > 0

        logger.info(f"[GENERATOR SDXL] Mode: {mode}, Using ControlNet: {using_controlnet}")

        # Setup ControlNet if needed
        if using_controlnet:
            valid_control_images = [img for img in control_images if img is not None]
            if not valid_control_images:
                raise ValueError("ControlNet requires control images")
            control_images = valid_control_images
            logger.info(f"[GENERATOR SDXL] Using {len(controlnets)} ControlNet(s) with {len(control_images)} control image(s)")
            model.load_with_controlnet(controlnets, mode=mode)

        # Generate images based on mode
        if mode == "img2img":
            mask = pipe_input.input.get("mask")
            self._ensure_inpaint_head(pipe_input.input.get("ASSETS"), mask)
            images = self._generate_img2img(
                model, image, conditioning, seeds,
                controlnets, control_images,
                mask,
                generation_outputs, using_controlnet
            )
        else:
            images = self._generate_txt2img(
                model, conditioning, seeds,
                controlnets, control_images,
                generation_outputs, using_controlnet
            )

        emit_gallery(generation_outputs, images)

        # One aggressive cleanup per pipe run; per-image cleanups inside
        # model.txt2img/img2img are light (no sync/multi-GC) by design.
        self._aggressive_cleanup(model)

        return PipeOutput(output={"image": [img.image for img in images]})

    @staticmethod
    def _ensure_inpaint_head(assets, mask) -> None:
        """Fetch the Fooocus inpaint head before a masked generation starts.

        The pre-flight for `InpaintHeadLoader.load_inpaint_head`, which only
        loads: by the time the k-diffusion pipeline reaches it, it is deep
        inside `SDXLModelWrapper` construction where no service is in scope.
        A missing ASSETS service is not fatal here - it fails at load with a
        path in the message, which beats masking that with a service error.
        """
        if mask is None or assets is None:
            return
        assets.ensure_asset_file(
            INPAINT_HEAD_URL,
            subdir=INPAINT_HEAD_SUBDIR,
            filename=INPAINT_HEAD_FILENAME,
        )

    @staticmethod
    def _aggressive_cleanup(model):
        from src.platform.runtime.model_lifecycle.manager import get_model_lifecycle_manager
        models = get_model_lifecycle_manager()
        if models is not None:
            models.cleanup(aggressive=True)
        elif hasattr(model, "clear_cuda_cache"):
            model.clear_cuda_cache(aggressive=True)

    def _generate_img2img(
            self,
            model: Model,
            images: list,
            conditioning: list,
            seeds: list,
            controlnets: list,
            control_images: list,
            mask,
            generation_outputs: callable,
            using_controlnet: bool
    ) -> list:
        """Generate images using img2img mode"""
        inpaint_mode = self.config.get("inpaint_mode", False)

        if inpaint_mode and mask is not None:
            logger.debug("[GENERATOR SDXL] Inpainting mode")
        else:
            logger.debug("[GENERATOR SDXL] img2img mode")

        results = []
        for img in images:
            width, height = img.size

            # Build generation input
            g_input = self._build_generation_input_img2img(
                img, mask, width, height, seeds, conditioning
            )

            # Execute generation
            if using_controlnet:
                output = model.img2img_controlnet(g_input, control_images, generation_outputs)
            else:
                output = model.img2img(g_input, generation_outputs)

            results.append(output)

        return results

    def _generate_txt2img(
            self,
            model: Model,
            conditioning: list,
            seeds: list,
            controlnets: list,
            control_images: list,
            generation_outputs: callable,
            using_controlnet: bool
    ) -> list:
        """Generate images using txt2img mode"""
        logger.debug("[GENERATOR SDXL] txt2img mode")
        quantity = int(self.config.get("quantity", 1))
        planned_seeds = plan_seeds(seeds, int(self.config.get("seed", -1)), quantity)

        results = []
        for idx in range(quantity):
            # Build generation input
            g_input = self._build_generation_input_txt2img(
                idx, planned_seeds[idx], conditioning, quantity
            )

            # Execute generation
            if using_controlnet:
                output = model.txt2img_controlnet(g_input, control_images, generation_outputs)
            else:
                output = model.txt2img(g_input, generation_outputs)

            results.append(output)

        return results

    def _build_generation_input_txt2img(
            self,
            idx: int,
            seed: int,
            conditioning: list,
            quantity: int
    ) -> GenerationInput:
        """Build GenerationInput for txt2img mode"""
        resolution = self.config.get("resolution", "1024x1024").split("x")

        return GenerationInput(
            input=[
                GenerationInputItem(name="seed", value=seed, io_type=IOType.SEED),
                GenerationInputItem(name="sampler", value=self.config.get("sampler", "DPMPP_2M"), io_type=IOType.SAMPLER),
                GenerationInputItem(name="scheduler", value=self.config.get("scheduler", "karras"), io_type=IOType.SCHEDULER),
                GenerationInputItem(name="quantity", value=quantity, io_type=IOType.INT),
                GenerationInputItem(name="resolution", value=[int(resolution[0]), int(resolution[1])], io_type=IOType.RESOLUTION),
                GenerationInputItem(name="cfg", value=float(self.config.get("cfg", 6.0)), io_type=IOType.CFG),
                GenerationInputItem(name="steps", value=self.config.get("steps", 25), io_type=IOType.STEP),
                GenerationInputItem(name="clip_skip", value=self.config.get("clip_skip", 2), io_type=IOType.CLIP_SKIP),
                GenerationInputItem(name="embedding", value=self.config.get("embeddings", {}), io_type=IOType.EMBEDDING),
                GenerationInputItem(name="conditioning", value=conditioning[idx] if idx < len(conditioning) else conditioning[0], io_type=IOType.CONDITIONING),
                # Guidance rescale
                GenerationInputItem(name="guidance_rescale", value=float(self.config.get("guidance_rescale", 0.0)), io_type=IOType.GUIDANCE_RESCALE),
            ]
        )

    def _build_generation_input_img2img(
            self,
            image,
            mask,
            width: int,
            height: int,
            seeds: list,
            conditioning: list
    ) -> GenerationInput:
        """Build GenerationInput for img2img mode"""
        return GenerationInput(
            input=[
                GenerationInputItem(name="sampler", value=self.config.get("sampler", "DPMPP_2M"), io_type=IOType.SAMPLER),
                GenerationInputItem(name="scheduler", value=self.config.get("scheduler", "karras"), io_type=IOType.SCHEDULER),
                GenerationInputItem(name="seed", value=plan_seeds(seeds, int(self.config.get("seed", -1)), 1)[0], io_type=IOType.SEED),
                GenerationInputItem(name="resolution", value=[width, height], io_type=IOType.RESOLUTION),
                GenerationInputItem(name="cfg", value=float(self.config.get("cfg", 6.0)), io_type=IOType.CFG),
                GenerationInputItem(name="steps", value=self.config.get("steps", 25), io_type=IOType.STEP),
                GenerationInputItem(name="clip_skip", value=self.config.get("clip_skip", 2), io_type=IOType.CLIP_SKIP),
                GenerationInputItem(name="image", value=image, io_type=IOType.IMAGE),
                GenerationInputItem(name="denoise", value=self.config.get("denoise", 0.8), io_type=IOType.DENOISE),
                GenerationInputItem(name="mask", value=mask, io_type=IOType.MASK),
                GenerationInputItem(name="embedding", value=self.config.get("embeddings", {}), io_type=IOType.EMBEDDING),
                GenerationInputItem(name="conditioning", value=conditioning[0], io_type=IOType.CONDITIONING),
                # Guidance rescale
                GenerationInputItem(name="guidance_rescale", value=float(self.config.get("guidance_rescale", 0.0)), io_type=IOType.GUIDANCE_RESCALE),
            ]
        )

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """Generator requires model, conditioning, and optionally seed/image/controlnet/mask"""
        return [
            PipeInputSpec("model", IOType.MODEL, True, "AI model for image generation", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds for generation", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, False, "Input image for img2img mode", is_array=True),
            PipeInputSpec("mask", IOType.IMAGE, False, "Mask image for inpainting mode", is_array=False),
            PipeInputSpec("controlnet", IOType.CONTROLNET, False, "ControlNet models for guided generation", is_array=True),
            PipeInputSpec("control_image", IOType.IMAGE, False, "Control images for ControlNet", is_array=True),
            PipeInputSpec("ASSETS", IOType.SERVICE, False, "Asset fetcher, to fetch the inpaint head before a masked generation", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """Generator produces generated images"""
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images", is_array=True),
        ]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Generator configuration parameters"""
        return [
            PipeConfigSpec(
                name="mode",
                param_type=str,
                default="txt2img",
                description="Generation mode",
                required=True,
                choices=["txt2img", "img2img"]
            ),
            PipeConfigSpec(
                name="model",
                param_type=str,
                default=None,
                description="Model identifier to use for generation",
                required=False
            ),
            PipeConfigSpec(
                name="vae",
                param_type=str,
                default=None,
                description="VAE model identifier",
                required=False
            ),
            PipeConfigSpec(
                name="steps",
                param_type=int,
                default=25,
                description="Number of denoising steps",
                required=False,
                min_value=1,
                max_value=150
            ),
            PipeConfigSpec(
                name="cfg",
                param_type=float,
                default=6.0,
                description="Classifier Free Guidance scale",
                required=False,
                min_value=0.0,
                max_value=30.0
            ),
            PipeConfigSpec(
                name="sampler",
                param_type=str,
                default="DPMPP_2M",
                description="Sampling algorithm to use",
                required=False,
                choices=["EULER", "EULER_A", "HEUN", "DPM2", "DPM2_A", "LMS",
                         "DPMPP_2S_A", "DPMPP_SDE", "DPMPP_2M", "DPMPP_2M_SDE", "DPMPP_3M_SDE", "LCM"]
            ),
            PipeConfigSpec(
                name="scheduler",
                param_type=str,
                default="karras",
                description="Noise schedule type to use",
                required=False,
                choices=["normal", "karras", "exponential", "sgm_uniform", "simple", "ddim_uniform"]
            ),
            PipeConfigSpec(
                name="seed",
                param_type=int,
                default=-1,
                description="Random seed (-1 for random)",
                required=False,
                min_value=-1
            ),
            PipeConfigSpec(
                name="resolution",
                param_type=str,
                default="1024x1024",
                description="Output resolution (WxH)",
                required=False
            ),
            PipeConfigSpec(
                name="quantity",
                param_type=int,
                default=1,
                description="Number of images to generate",
                required=False,
                min_value=1,
                max_value=10
            ),
            PipeConfigSpec(
                name="clip_skip",
                param_type=int,
                default=2,
                description="CLIP skip value",
                required=False,
                min_value=1,
                max_value=12
            ),
            PipeConfigSpec(
                name="denoise",
                param_type=float,
                default=0.8,
                description="Denoising strength for img2img",
                required=False,
                min_value=0.0,
                max_value=1.0
            ),
            PipeConfigSpec(
                name="embeddings",
                param_type=dict,
                default={},
                description="Embeddings configuration",
                required=False
            ),
            # Guidance rescale
            PipeConfigSpec(
                name="guidance_rescale",
                param_type=float,
                default=0.0,
                description="Guidance rescale factor (0.0 = disabled, 0.5-0.7 = good for realistic at high CFG)",
                required=False,
                min_value=0.0,
                max_value=1.0
            ),
            # Inpainting parameters
            PipeConfigSpec(
                name="inpaint_mode",
                param_type=bool,
                default=False,
                description="Enable inpainting mode (requires mask input)",
                required=False
            ),
            PipeConfigSpec(
                name="mask_blur",
                param_type=int,
                default=4,
                description="Blur radius for mask edges (smoother blending)",
                required=False,
                min_value=0,
                max_value=20
            ),
        ]
