from typing import Dict, Any, List
from PIL import Image, ImageFilter
import numpy as np

from src.pipelines.outputs import CompareImagesGenerationOutput, ProgressGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput, PipeOutput, IOType, PipeInputSpec, PipeOutputSpec, PipeConfigSpec,
)


class FilmGrain(BasePipe):
    name = "film_grain"
    description = "Adds realistic film grain effect to make images look more natural"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "intensity": 0.3,  # Controls the overall strength of the grain effect (0-1)
            "grain_size": 1.5,  # Controls the size of grain particles
            "shadows_bias": 0.6,  # More grain in darker areas (0-1)
            "highlights_protect": 0.2,  # Protect highlights from grain (0-1)
            "monochromatic": False,  # If True, applies same grain to all channels
            "blur_amount": 0.5,  # Slight blur to make grain look more natural
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("intensity", float, 0.3, "Controls the overall strength of the grain effect", required=False,
                          min_value=0.0, max_value=1.0),
            PipeConfigSpec("grain_size", float, 1.5, "Controls the size of grain particles", required=False,
                          min_value=0.5, max_value=3.0),
            PipeConfigSpec("shadows_bias", float, 0.6, "More grain in darker areas", required=False,
                          min_value=0.0, max_value=1.0),
            PipeConfigSpec("highlights_protect", float, 0.2, "Protect highlights from grain", required=False,
                          min_value=0.0, max_value=1.0),
            PipeConfigSpec("monochromatic", bool, False, "If True, applies same grain to all channels", required=False),
            PipeConfigSpec("blur_amount", float, 0.5, "Slight blur to make grain look more natural", required=False,
                          min_value=0.0, max_value=2.0),
            PipeConfigSpec("preset", str, None, "Preset film grain style", required=False,
                          choices=["classic_35mm", "cinematic_portra", "vintage_push", "modern_cine", "high_iso", "super8", "subtle_digital", "trix400"])
        ]

    @classmethod
    def preset_classic_35mm(cls) -> Dict[str, Any]:
        """
        Classic 35mm film look with moderate grain

        - Moderate grain with good shadow detail
        - Monochromatic grain for authenticity
        - Good for general use
        """

        return {
            "intensity": 0.25,
            "grain_size": 1.2,
            "shadows_bias": 0.7,
            "highlights_protect": 0.3,
            "monochromatic": True,
            "blur_amount": 0.3
        }

    @classmethod
    def preset_cinematic_portra(cls) -> Dict[str, Any]:
        """
        Inspired by Kodak Portra, subtle grain with protected highlights

        - Very subtle, color-sensitive grain
        - Protects highlights well
        - Great for portraits and soft images
        """
        return {
            "intensity": 0.18,
            "grain_size": 1.4,
            "shadows_bias": 0.5,
            "highlights_protect": 0.4,
            "monochromatic": False,
            "blur_amount": 0.4
        }

    @classmethod
    def preset_vintage_push(cls) -> Dict[str, Any]:
        """
        Pushed film look with pronounced grain in shadows

        - Strong grain, especially in shadows
        - Monochromatic for authenticity
        - Good for artistic/vintage looks
        """
        return {
            "intensity": 0.4,
            "grain_size": 1.0,
            "shadows_bias": 0.8,
            "highlights_protect": 0.2,
            "monochromatic": True,
            "blur_amount": 0.25
        }

    @classmethod
    def preset_modern_cine(cls) -> Dict[str, Any]:
        """
        Modern cinema look with fine, subtle grain

        - Very fine, subtle grain structure
        - Color-sensitive grain
        - Professional cinema feel
        """
        return {
            "intensity": 0.15,
            "grain_size": 1.6,
            "shadows_bias": 0.4,
            "highlights_protect": 0.5,
            "monochromatic": False,
            "blur_amount": 0.45
        }

    @classmethod
    def preset_high_iso(cls) -> Dict[str, Any]:
        """
        Simulates high ISO film with pronounced grain

        - More pronounced grain
        - Faster falloff in shadows
        - Good for low-light/moody images
        """
        return {
            "intensity": 0.5,
            "grain_size": 0.8,
            "shadows_bias": 0.6,
            "highlights_protect": 0.15,
            "monochromatic": True,
            "blur_amount": 0.2
        }

    @classmethod
    def preset_super8(cls) -> Dict[str, Any]:
        """
        Classic Super 8 film look with organic grain pattern

        - Organic, larger grain pattern
        - Color-sensitive
        - Vintage movie feel
        """
        return {
            "intensity": 0.35,
            "grain_size": 1.8,
            "shadows_bias": 0.65,
            "highlights_protect": 0.25,
            "monochromatic": False,
            "blur_amount": 0.5
        }

    @classmethod
    def preset_subtle_digital(cls) -> Dict[str, Any]:
        """
        Very subtle grain for digital images

        - Very subtle grain
        - Minimal shadow bias
        - Just enough to break up digital perfection
        """
        return {
            "intensity": 0.12,
            "grain_size": 1.3,
            "shadows_bias": 0.4,
            "highlights_protect": 0.6,
            "monochromatic": False,
            "blur_amount": 0.35
        }

    @classmethod
    def preset_trix400(cls) -> Dict[str, Any]:
        """
        Inspired by Tri-X 400 black and white film

        - Classic black and white film look
        - Strong shadow grain
        - Good contrast and grain structure
        """
        return {
            "intensity": 0.3,
            "grain_size": 1.1,
            "shadows_bias": 0.75,
            "highlights_protect": 0.2,
            "monochromatic": True,
            "blur_amount": 0.3
        }

    def _create_grain_layer(self, size: tuple, intensity: float, grain_size: float) -> np.ndarray:
        """Creates a grain noise layer with given parameters"""
        # Create base noise
        scale = 1 / grain_size
        # Use height, width order for numpy arrays
        scaled_size = (int(size[1] * scale), int(size[0] * scale))

        # Generate initial noise
        noise = np.random.normal(0, 1, scaled_size)

        # Resize to target size for larger grain
        if scale != 1:
            noise = Image.fromarray((noise * 255).astype(np.uint8))
            noise = noise.resize((size[0], size[1]), Image.BILINEAR)  # PIL uses (width, height)
            noise = np.array(noise).astype(float) / 255

        # Adjust contrast and intensity
        noise = (noise - noise.mean()) / noise.std()
        noise = noise * intensity

        return noise

    def _apply_grain(self, image: Image.Image) -> Image.Image:
        """Applies film grain effect to the image"""
        # Convert image to numpy array
        img_array = np.array(image).astype(float) / 255
        height, width = img_array.shape[:2]

        # Create grain layer matching the image dimensions
        grain = self._create_grain_layer(
            (width, height),  # PIL uses (width, height)
            self.config["intensity"],
            self.config["grain_size"]
        )

        # Create monochromatic or color grain
        if self.config["monochromatic"]:
            grain = np.expand_dims(grain, axis=-1)  # Add channel dimension
            grain = np.repeat(grain, 3, axis=-1)    # Repeat for RGB
        else:
            grain = np.stack([
                self._create_grain_layer((width, height), self.config["intensity"], self.config["grain_size"])
                for _ in range(3)
            ], axis=-1)

        # Calculate luminance for shadows/highlights bias
        luminance = np.mean(img_array, axis=2)

        # Create masks for shadows and highlights
        shadows_mask = 1 - (luminance ** (1 - self.config["shadows_bias"]))
        highlights_mask = 1 - (1 - luminance) ** (1 - self.config["highlights_protect"])

        # Combine masks and apply to grain
        final_mask = shadows_mask * highlights_mask
        final_mask = np.stack([final_mask] * 3, axis=-1)
        grain = grain * final_mask

        # Apply grain to image
        result = img_array + grain

        # Clip values to valid range
        result = np.clip(result, 0, 1)

        # Convert back to PIL Image
        result = (result * 255).astype(np.uint8)
        result_image = Image.fromarray(result)

        # Apply slight blur if configured
        if self.config["blur_amount"] > 0:
            result_image = result_image.filter(
                ImageFilter.GaussianBlur(radius=self.config["blur_amount"])
            )

        return result_image

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        if "image" not in pipe_input.input or not pipe_input.input["image"]:
            logger.error("[FILM_GRAIN] No input image provided")
            return PipeOutput(output={})

        if "preset" in self.config and self.config["preset"]:
            preset = self.config["preset"]

            logger.info(f"[FILM_GRAIN] Applying preset: {preset}")
            generation_outputs(ProgressGenerationOutput(state=f"Applying preset: <<EFFECT:{preset}>>"))

            if preset == "classic_35mm":
                self.config.update(self.preset_classic_35mm())
            elif preset == "cinematic_portra":
                self.config.update(self.preset_cinematic_portra())
            elif preset == "vintage_push":
                self.config.update(self.preset_vintage_push())
            elif preset == "modern_cine":
                self.config.update(self.preset_modern_cine())
            elif preset == "high_iso":
                self.config.update(self.preset_high_iso())
            elif preset == "super8":
                self.config.update(self.preset_super8())
            elif preset == "subtle_digital":
                self.config.update(self.preset_subtle_digital())
            elif preset == "trix400":
                self.config.update(self.preset_trix400())

        # Handle single image or list of images
        if isinstance(pipe_input.input["image"], list):
            images = pipe_input.input["image"]
        else:
            images = [pipe_input.input["image"]]

        processed_images = []
        for index, image in enumerate(images):
            logger.debug(f"[FILM_GRAIN] Processing image {index + 1}/{len(images)}")

            # Process image
            processed_image = self._apply_grain(image)
            processed_images.append(processed_image)

            # Generate output artifact for comparison
            generation_outputs(
                CompareImagesGenerationOutput(
                    index=index,
                    compare=(None, image),
                    to=("Grain Added", processed_image)
                )
            )

        return PipeOutput(output={"image": processed_images})

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """FilmGrain requires image input"""
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Input images to apply film grain effect", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """FilmGrain produces images with film grain effect"""
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Images with film grain effect applied", is_array=True),
        ]
