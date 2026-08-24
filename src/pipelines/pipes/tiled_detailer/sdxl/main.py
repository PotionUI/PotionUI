import logging

from typing import Dict, Any, List, Tuple
from PIL import Image
import numpy as np

from src.pipelines.outputs import CompareImagesGenerationOutput, ProgressGenerationOutput, ImageGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.platform.util.dimensions import round_to_multiple
from src.pipelines.contracts import (
    PipeInput, PipeOutput, IOType, PipeInputSpec, PipeOutputSpec, PipeConfigSpec,
)
from src.pipelines.contracts import GenerationInput, GenerationInputItem
from src.platform.util.latents import generate_seed


class TiledDetailerSDXL(BasePipe):
    name = "tiled_detailer"
    description = "SDXL-optimized tiled detailer for high-resolution image enhancement"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "tile_size": 1536,  # SDXL optimized - larger tiles for better quality
            "overlap": 128,     # Increased overlap for SDXL
            "denoise": 0.25,    # Lower denoise for SDXL quality preservation
            "steps": 20,        # Fewer steps needed for SDXL
            "cfg": 5.5,         # SDXL optimized CFG
            "sampler": "DPMPP_2M",  # SDXL optimized sampler algorithm
            "scheduler": "karras",  # SDXL optimized noise schedule
            "clip_skip": 2,
            "seed": -1,
            "feather_amount": 96,  # Reduced feathering for SDXL sharpness
            "detail_boost": 1.1,   # Conservative boost for SDXL
            "adaptive_tiles": True,
            "enable_detail_conditioning": True,
            "content_aware_denoise": True,
            "min_denoise": 0.1,    # Lower minimums for SDXL
            "max_denoise": 0.45,   # Lower maximums for SDXL
            "vram_limit_gb": 8,  # Updated default for high VRAM cards
            "min_tile_size": 896,  # Based on 896x1152 generation capability
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("tile_size", int, 1536, "Base tile size for processing", required=False,
                          min_value=256, max_value=4096),
            PipeConfigSpec("overlap", int, 128, "Overlap between tiles in pixels", required=False,
                          min_value=16, max_value=512),
            PipeConfigSpec("denoise", float, 0.25, "Denoising strength", required=False,
                          min_value=0.0, max_value=1.0),
            PipeConfigSpec("steps", int, 20, "Number of inference steps", required=False,
                          min_value=1, max_value=150),
            PipeConfigSpec("cfg", float, 5.5, "CFG scale", required=False,
                          min_value=1.0, max_value=30.0),
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
            PipeConfigSpec("clip_skip", int, 2, "Number of CLIP layers to skip", required=False,
                          min_value=0, max_value=12),
            PipeConfigSpec("seed", int, -1, "Random seed for generation (-1 for random)", required=False),
            PipeConfigSpec("feather_amount", int, 96, "Feathering amount for tile blending", required=False,
                          min_value=16, max_value=512),
            PipeConfigSpec("detail_boost", float, 1.1, "Detail enhancement multiplier", required=False,
                          min_value=0.5, max_value=2.0),
            PipeConfigSpec("adaptive_tiles", bool, True, "Enable adaptive tile sizing", required=False),
            PipeConfigSpec("enable_detail_conditioning", bool, True, "Enable detail-focused conditioning", required=False),
            PipeConfigSpec("content_aware_denoise", bool, True, "Adjust denoise based on content complexity", required=False),
            PipeConfigSpec("min_denoise", float, 0.1, "Minimum denoise for uniform areas", required=False,
                          min_value=0.0, max_value=1.0),
            PipeConfigSpec("max_denoise", float, 0.35, "Maximum denoise for detailed areas", required=False,
                          min_value=0.0, max_value=1.0),
            PipeConfigSpec("vram_limit_gb", int, 32, "VRAM limit in GB for tile sizing", required=False,
                          min_value=4, max_value=80),
            PipeConfigSpec("min_tile_size", int, 896, "Minimum tile size to avoid tiny tiles", required=False,
                          min_value=256, max_value=2048)
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """TiledDetailer requires model, conditioning, seeds, images, and memory management services"""
        return [
            PipeInputSpec("model", IOType.MODEL, True, "AI model for image generation", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds for generation", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, True, "Input images to detail", is_array=True),
            PipeInputSpec("GPU", IOType.SERVICE, False, "GPU manager service for VRAM monitoring", is_array=False),
            PipeInputSpec("MEMORY", IOType.SERVICE, False, "Memory manager service for intelligent tiling", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """TiledDetailer produces detailed images"""
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Enhanced detailed images", is_array=True),
        ]

    def _split_image_into_tiles(
            self,
            image: Image.Image,
            tiles_x: int,
            tiles_y: int,
            tile_width: int,
            tile_height: int,
            overlap: int
    ) -> List[Tuple[Image.Image, Tuple[int, int], Dict[str, bool]]]:
        """Split image into evenly distributed tiles with VRAM-aware sizing.

        Args:
            image: Input image to split
            tiles_x: Number of tiles horizontally
            tiles_y: Number of tiles vertically
            tile_width: Target width of each tile
            tile_height: Target height of each tile
            overlap: Overlap amount between tiles

        Returns:
            List of tuples containing (tile_image, (left, top), edge_info)
        """
        width, height = image.size
        tiles = []

        # Calculate effective spacing between tiles
        if tiles_x == 1:
            x_step = width
        else:
            x_step = (width - tile_width) // (tiles_x - 1)

        if tiles_y == 1:
            y_step = height
        else:
            y_step = (height - tile_height) // (tiles_y - 1)

        for row in range(tiles_y):
            for col in range(tiles_x):
                # Calculate tile position
                if col == 0:
                    left = 0
                elif col == tiles_x - 1:
                    left = width - tile_width
                else:
                    left = col * x_step

                if row == 0:
                    top = 0
                elif row == tiles_y - 1:
                    top = height - tile_height
                else:
                    top = row * y_step

                # Ensure bounds
                left = max(0, min(left, width - tile_width))
                top = max(0, min(top, height - tile_height))
                right = min(left + tile_width, width)
                bottom = min(top + tile_height, height)

                # Determine edge information
                edge_info = {
                    "is_left_edge": left == 0,
                    "is_right_edge": right == width,
                    "is_top_edge": top == 0,
                    "is_bottom_edge": bottom == height
                }

                # Extract tile
                tile = image.crop((left, top, right, bottom))
                tiles.append((tile, (left, top), edge_info))

        return tiles

    def _create_feathered_mask(self, size: Tuple[int, int], feather: int, edge_info: Dict[str, bool]) -> Image.Image:
        """Create a feathered mask for smooth tile blending, respecting image edges."""
        mask = Image.new('L', size, 255)

        # Use smoother gradient
        feather_arr = np.linspace(0, 255, feather, dtype=np.uint8)

        # Create smooth transitions using sine curve
        x = np.linspace(0, np.pi/2, feather)
        feather_arr = (np.sin(x) * 255).astype(np.uint8)

        # Handle horizontal edges
        if not edge_info["is_left_edge"]:
            gradient = feather_arr.reshape(1, -1)
            mask.paste(Image.fromarray(gradient).resize((feather, size[1]), Image.LANCZOS), (0, 0))

        if not edge_info["is_right_edge"]:
            gradient = feather_arr[::-1].reshape(1, -1)
            mask.paste(Image.fromarray(gradient).resize((feather, size[1]), Image.LANCZOS),
                       (size[0] - feather, 0))

        # Handle vertical edges
        if not edge_info["is_top_edge"]:
            gradient = feather_arr.reshape(-1, 1)
            mask.paste(Image.fromarray(gradient).resize((size[0], feather), Image.LANCZOS), (0, 0))

        if not edge_info["is_bottom_edge"]:
            gradient = feather_arr[::-1].reshape(-1, 1)
            mask.paste(Image.fromarray(gradient).resize((size[0], feather), Image.LANCZOS),
                       (0, size[1] - feather))

        # Create and apply corner gradients only where needed
        if not (edge_info["is_left_edge"] or edge_info["is_top_edge"]):
            corner = np.outer(feather_arr, feather_arr) / 255
            corner_img = Image.fromarray((corner).astype(np.uint8))
            mask.paste(corner_img.resize((feather, feather), Image.LANCZOS), (0, 0))

        if not (edge_info["is_right_edge"] or edge_info["is_top_edge"]):
            corner = np.outer(feather_arr, feather_arr[::-1]) / 255
            corner_img = Image.fromarray((corner).astype(np.uint8))
            mask.paste(corner_img.resize((feather, feather), Image.LANCZOS),
                       (size[0] - feather, 0))

        if not (edge_info["is_left_edge"] or edge_info["is_bottom_edge"]):
            corner = np.outer(feather_arr[::-1], feather_arr) / 255
            corner_img = Image.fromarray((corner).astype(np.uint8))
            mask.paste(corner_img.resize((feather, feather), Image.LANCZOS),
                       (0, size[1] - feather))

        if not (edge_info["is_right_edge"] or edge_info["is_bottom_edge"]):
            corner = np.outer(feather_arr[::-1], feather_arr[::-1]) / 255
            corner_img = Image.fromarray((corner).astype(np.uint8))
            mask.paste(corner_img.resize((feather, feather), Image.LANCZOS),
                       (size[0] - feather, size[1] - feather))

        return mask

    def hijack_generation_output(self, generation_outputs: callable, generation_output: Any, wh: tuple[int, int], indexes: tuple[int, int], current_image: Image.Image, tile: Image.Image, position: Tuple[int, int], mask: Image.Image):
        if isinstance(generation_output, ProgressGenerationOutput):
            generation_output.state = f"SDXL tile processing: <<PROGRESS:{indexes[0]}/{indexes[1]}>> at <<RESOLUTION:{wh[0]}x{wh[1]}>>"
            generation_outputs(generation_output)
        elif isinstance(generation_output, ImageGenerationOutput):
            # Create a temporary image to show the current state
            temp_image = current_image.copy()

            # Ensure the generated image matches the tile size before pasting
            gen_img = generation_output.image

            # Debug logging
            logger.debug(f"[TILED_DETAILER SDXL] Hijack received image: size={gen_img.size}, mode={gen_img.mode}")

            # Safety check: Ensure gen_img is not a mask
            if gen_img.mode == 'L':
                logger.error(f"[TILED_DETAILER SDXL] ERROR: Generated image is grayscale in hijack! Converting to RGB")
                gen_img = gen_img.convert('RGB')

            if gen_img.size != tile.size:
                # Resize to match tile dimensions
                gen_img = gen_img.resize(tile.size, Image.LANCZOS)

            # Also ensure mask matches tile size
            if mask.size != tile.size:
                mask = mask.resize(tile.size, Image.LANCZOS)

            # Ensure mask is grayscale
            if mask.mode != 'L':
                logger.warning(f"[TILED_DETAILER SDXL] Mask is not grayscale in hijack, converting")
                mask = mask.convert('L')

            temp_image.paste(gen_img, position, mask=mask)
            generation_outputs(ImageGenerationOutput(image=temp_image, temporary=True))
        else:
            generation_outputs(generation_output)

    def _estimate_vram_usage(self, width: int, height: int) -> float:
        """Estimate VRAM usage for SDXL at given resolution (rough approximation)."""
        # Base VRAM usage for SDXL model loading (~4GB for SDXL base model)
        base_vram = 4.0

        # Memory usage scales with resolution
        # More accurate estimation based on actual SDXL usage patterns:
        # - 1024x1024 uses ~6-7GB total
        # - 2048x2048 uses ~10-12GB total
        # - 3072x3072 uses ~18-20GB total
        pixels = width * height
        megapixels = pixels / (1024 * 1024)

        # Non-linear scaling - larger resolutions are more efficient per pixel
        # Using a logarithmic scaling factor for more accurate estimation
        if megapixels <= 1:  # Up to 1024x1024
            additional_vram = megapixels * 3.0
        elif megapixels <= 4:  # Up to 2048x2048
            additional_vram = 3.0 + (megapixels - 1) * 2.0
        elif megapixels <= 9:  # Up to 3072x3072
            additional_vram = 9.0 + (megapixels - 4) * 1.8
        else:  # Beyond 3072x3072
            additional_vram = 18.0 + (megapixels - 9) * 1.5

        total_vram = base_vram + additional_vram

        # Add 10% overhead for safety
        return total_vram * 1.1

    def _get_optimal_tile_grid(self, image_size: Tuple[int, int], gpu_manager=None, memory_manager=None) -> Tuple[int, int, int, int]:
        """
        Calculate optimal tile grid that fits VRAM constraints and avoids tiny tiles.

        Args:
            image_size: (width, height) tuple
            gpu_manager: Optional GPU manager service for dynamic VRAM budget
            memory_manager: Optional Memory manager service for intelligent tiling

        Returns:
            Tuple[int, int, int, int]: (tiles_x, tiles_y, tile_width, tile_height)
        """
        width, height = image_size
        overlap = int(self.config["overlap"])
        min_tile_size = int(self.config.get("min_tile_size", 896))

        # Determine VRAM budget
        vram_limit = self.config.get("vram_limit_gb", None)
        if gpu_manager and vram_limit is None:
            # No cap configured on the backend - bound only by available hardware
            vram_limit = gpu_manager.get_vram_budget()
            logger.debug(f"[TILED_DETAILER SDXL] Using dynamic VRAM budget: {vram_limit:.2f}GB")
        elif vram_limit is not None:
            logger.debug(f"[TILED_DETAILER SDXL] Using configured VRAM limit: {vram_limit}GB")
        else:
            # No service and no config - use conservative default
            vram_limit = 12.0
            logger.debug(f"[TILED_DETAILER SDXL] No VRAM limit specified, using default: {vram_limit}GB")

        # Use memory manager for intelligent tiling if available
        if memory_manager:
            logger.debug(f"[TILED_DETAILER SDXL] Using MEMORY service for tile calculation")
            tiles_x, tiles_y, tile_width, tile_height = memory_manager.calculate_optimal_tile_count(
                image_size=image_size,
                vram_budget=vram_limit,
                model_type="sdxl",
                overlap=overlap,
                min_tile_size=min_tile_size
            )
            return tiles_x, tiles_y, tile_width, tile_height

        # Fall back to internal implementation if no memory manager
        logger.debug(f"[TILED_DETAILER SDXL] Using internal tile calculation (no MEMORY service)")

        # First, check if the entire image fits in VRAM
        full_image_vram = self._estimate_vram_usage(width, height)
        if full_image_vram <= vram_limit * 0.85:  # 85% safety margin for full image
            logger.debug(f"[TILED_DETAILER SDXL] Full image fits in {vram_limit}GB VRAM (estimated {full_image_vram:.1f}GB), processing as single tile")
            return 1, 1, width, height

        # Extended tile sizes for high VRAM cards
        # For 32GB cards, we can handle much larger tiles
        if vram_limit >= 24:  # 24GB+ cards (3090, 4090, A5000, etc.)
            test_sizes = [3072, 2816, 2560, 2304, 2048, 1920, 1792, 1664, 1536, 1344, 1216, 1152, 1024, 896]
        elif vram_limit >= 16:  # 16GB+ cards
            test_sizes = [2304, 2048, 1920, 1792, 1664, 1536, 1344, 1216, 1152, 1024, 896]
        elif vram_limit >= 12:  # 12GB+ cards
            test_sizes = [1792, 1664, 1536, 1344, 1216, 1152, 1024, 896]
        else:  # 8GB and below
            test_sizes = [1344, 1216, 1152, 1024, 896, 768]

        # Find the largest tile size that fits in VRAM
        max_tile_size = min_tile_size  # Default to minimum
        for test_size in test_sizes:
            estimated_vram = self._estimate_vram_usage(test_size, test_size)
            if estimated_vram <= vram_limit * 0.85:  # 85% safety margin
                max_tile_size = test_size
                logger.debug(f"[TILED_DETAILER SDXL] Selected tile size {test_size}x{test_size} (estimated {estimated_vram:.1f}GB VRAM)")
                break

        # Calculate optimal grid dimensions
        # We want to minimize the number of tiles while ensuring even distribution
        def calculate_tiles_needed(dimension, tile_size):
            if dimension <= tile_size:
                return 1, round_to_multiple(dimension)

            # Calculate how many tiles we need
            effective_tile_size = tile_size - overlap
            tiles_needed = max(1, int(np.ceil((dimension - overlap) / effective_tile_size)))

            # Calculate actual tile size for even distribution
            if tiles_needed == 1:
                actual_tile_size = dimension
            else:
                actual_tile_size = (dimension + overlap * (tiles_needed - 1)) // tiles_needed

            # Ensure minimum tile size and round to multiple of 8
            actual_tile_size = max(min_tile_size, actual_tile_size)
            actual_tile_size = round_to_multiple(actual_tile_size)

            return tiles_needed, actual_tile_size

        tiles_x, tile_width = calculate_tiles_needed(width, max_tile_size)
        tiles_y, tile_height = calculate_tiles_needed(height, max_tile_size)

        logger.debug(f"[TILED_DETAILER SDXL] VRAM-aware grid: {tiles_x}x{tiles_y} tiles, size: {tile_width}x{tile_height}, VRAM estimate: {self._estimate_vram_usage(tile_width, tile_height):.1f}GB")

        return tiles_x, tiles_y, tile_width, tile_height

    def _analyze_tile_complexity(self, tile: Image.Image) -> float:
        """Analyze the complexity/texture density of a tile."""
        import cv2

        # Convert to grayscale numpy array
        gray = np.array(tile.convert('L'))

        # Calculate edge density using Sobel operator
        sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        edge_magnitude = np.sqrt(sobel_x**2 + sobel_y**2)

        # Calculate texture complexity metrics
        edge_density = np.mean(edge_magnitude) / 255.0
        variance = np.var(gray) / (255.0 ** 2)

        # Combine metrics (normalized to 0-1 range)
        complexity = min(1.0, (edge_density * 0.7) + (variance * 0.3))

        return complexity

    def _get_detail_strength(self, image_size: Tuple[int, int], tile: Image.Image = None) -> float:
        """Calculate optimal denoise strength based on image size, detail boost, and content complexity."""
        base_denoise = float(self.config.get("denoise", 0.25))  # Lower for SDXL
        detail_boost = float(self.config.get("detail_boost", 1.1))  # Conservative for SDXL

        # Start with base calculation - respect configured denoise strength
        width, height = image_size
        # Apply detail boost but don't cap too aggressively - let user configuration take precedence
        strength = base_denoise * detail_boost

        # Only apply reasonable maximum cap to prevent extreme values
        strength = min(1.0, strength)  # Cap at 100% denoise maximum

        # Apply content-aware adjustment if enabled and tile is provided
        if self.config.get("content_aware_denoise", True) and tile is not None:
            complexity = self._analyze_tile_complexity(tile)

            # Use more reasonable min/max values that respect user configuration
            min_denoise = max(0.1, strength * 0.7)  # At least 70% of configured strength
            max_denoise = min(1.0, strength * 1.2)  # Up to 120% of configured strength

            # Interpolate between min and max based on complexity
            # Low complexity (uniform areas) → lower denoise to prevent hallucinations
            # High complexity (detailed areas) → higher denoise for enhancement
            adjusted_strength = min_denoise + (complexity * (max_denoise - min_denoise))

            # Use the content-aware value instead of being overly conservative
            strength = adjusted_strength

            logger.debug(f"[TILED_DETAILER SDXL] Tile complexity: {complexity:.3f}, denoise: {strength:.3f}")

        return strength

    def _enhance_conditioning_for_details(self, conditioning, tile_index: int, total_tiles: int):
        """Enhance conditioning to focus on detail generation."""
        if not self.config.get("enable_detail_conditioning", True):
            return conditioning

        # For now, return the original conditioning
        # This can be extended to add detail-specific prompt enhancements
        return conditioning

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        # Get memory management services if available
        gpu_manager = pipe_input.input.get("GPU", None)
        memory_manager = pipe_input.input.get("MEMORY", None)

        # Get configuration values
        overlap = int(self.config["overlap"])
        conditioning = pipe_input.input.get("conditioning", [])
        model = pipe_input.input["model"]
        seeds = pipe_input.input.get("seed", self.config.get("seed", []))
        images = pipe_input.input.get("image", [])

        if "image" not in pipe_input.input or not pipe_input.input["image"]:
            logger.error("[TILED_DETAILER SDXL] No input image provided")
            return PipeOutput(output={})

        if not isinstance(images, list):
            images = [images]
        if not isinstance(conditioning, list):
            conditioning = [conditioning]

        processed_images = []
        for index, image in enumerate(images):
            current_image = image.copy()  # Keep a copy for blending

            # Calculate optimal VRAM-aware tile grid with services
            tiles_x, tiles_y, tile_width, tile_height = self._get_optimal_tile_grid(
                image.size,
                gpu_manager=gpu_manager,
                memory_manager=memory_manager
            )

            tiles = self._split_image_into_tiles(image, tiles_x, tiles_y, tile_width, tile_height, overlap)
            processed_tiles = []

            logger.info(f"[TILED_DETAILER SDXL] Processing {len(tiles)} tiles, for image with resolution {image.size[0]}x{image.size[1]}")

            generation_outputs(
                ProgressGenerationOutput(
                    state=f"SDXL tiled processing: <<PROGRESS:{index + 1}/{len(images)}>>. Tiles: <<{len(tiles)}>>"
                )
            )

            for idx, (tile, position, edge_info) in enumerate(tiles):
                width, height = tile.size
                logger.debug(f"[TILED_DETAILER SDXL] Processing tile {idx + 1}/{len(tiles)}")

                # Calculate content-aware denoise strength for this specific tile
                tile_denoise_strength = self._get_detail_strength(image.size, tile)

                # Enhance conditioning for better detail generation
                enhanced_conditioning = self._enhance_conditioning_for_details(
                    conditioning[index] if index < len(conditioning) else conditioning[0],
                    idx,
                    len(tiles)
                )

                g_input = GenerationInput(
                    input=[
                        GenerationInputItem(name="sampler", value=self.config["sampler"], io_type=IOType.SAMPLER),
                        GenerationInputItem(name="scheduler", value=self.config["scheduler"], io_type=IOType.SCHEDULER),
                        GenerationInputItem(name="seed", value=seeds[index] if index < len(seeds) else generate_seed(), io_type=IOType.SEED),
                        GenerationInputItem(name="resolution", value=[width, height], io_type=IOType.RESOLUTION),
                        GenerationInputItem(name="cfg", value=float(self.config.get("cfg", 5.5)), io_type=IOType.CFG),
                        GenerationInputItem(name="steps", value=int(self.config.get("steps", 20)), io_type=IOType.STEP),
                        GenerationInputItem(name="clip_skip", value=self.config["clip_skip"], io_type=IOType.CLIP_SKIP),
                        GenerationInputItem(name="image", value=tile, io_type=IOType.IMAGE),
                        GenerationInputItem(name="denoise", value=tile_denoise_strength, io_type=IOType.DENOISE),  # Use per-tile content-aware strength
                        GenerationInputItem(name="conditioning", value=enhanced_conditioning, io_type=IOType.CONDITIONING),
                    ]
                )

                # Pre-create mask for this tile
                mask = self._create_feathered_mask(tile.size, int(self.config["feather_amount"]), edge_info)

                output = model.img2img(g_input, generation_outputs=lambda _output: self.hijack_generation_output(generation_outputs, _output, (width, height), (idx + 1, len(tiles)), current_image, tile, position, mask))
                processed_tile = output.image

                # Debug: Log what we received
                logger.debug(f"[TILED_DETAILER SDXL] Received processed tile: size={processed_tile.size}, mode={processed_tile.mode}")
                logger.debug(f"[TILED_DETAILER SDXL] Original tile: size={tile.size}, mode={tile.mode}")
                logger.debug(f"[TILED_DETAILER SDXL] Mask: size={mask.size}, mode={mask.mode}")

                # Ensure processed tile matches expected tile size before pasting
                if processed_tile.size != tile.size:
                    logger.warning(f"[TILED_DETAILER SDXL] Processed tile size {processed_tile.size} doesn't match expected {tile.size}, resizing")
                    processed_tile = processed_tile.resize(tile.size, Image.LANCZOS)

                # Ensure mask matches tile size
                if mask.size != tile.size:
                    logger.warning(f"[TILED_DETAILER SDXL] Mask size {mask.size} doesn't match tile size {tile.size}, resizing")
                    mask = mask.resize(tile.size, Image.LANCZOS)

                # Debug: Verify we're not pasting the mask.
                # getextrema() scans the full image, so only compute it at DEBUG level.
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"[TILED_DETAILER SDXL] About to paste tile at position {position}")
                    logger.debug(f"[TILED_DETAILER SDXL] Processed tile: size={processed_tile.size}, mode={processed_tile.mode}, extrema={processed_tile.getextrema()}")
                    logger.debug(f"[TILED_DETAILER SDXL] Mask: size={mask.size}, mode={mask.mode}, extrema={mask.getextrema()}")

                # Safety check: Ensure processed_tile is not a mask (should be RGB/RGBA, not L)
                if processed_tile.mode == 'L':
                    logger.error(f"[TILED_DETAILER SDXL] ERROR: Processed tile is grayscale (mode='L'), expected RGB/RGBA!")
                    logger.error(f"[TILED_DETAILER SDXL] This suggests the model returned a mask instead of an image!")
                    # Convert to RGB to at least prevent showing a black/white image
                    processed_tile = processed_tile.convert('RGB')

                # Ensure mask is grayscale
                if mask.mode != 'L':
                    logger.warning(f"[TILED_DETAILER SDXL] Mask is not grayscale (mode={mask.mode}), converting to 'L'")
                    mask = mask.convert('L')

                # Update current image with processed tile
                current_image.paste(processed_tile, position, mask=mask)

                processed_tiles.append((processed_tile, position, edge_info))

            processed_images.append(current_image)

            generation_outputs(
                CompareImagesGenerationOutput(
                    index=index,
                    compare=(None, image),
                    to=("SDXL Enhanced Image", current_image)
                )
            )

        # One aggressive cleanup per pipe run; per-tile img2img cleanups are
        # light (no sync/multi-GC) by design.
        from src.platform.runtime.model_lifecycle.manager import get_model_lifecycle_manager
        models = get_model_lifecycle_manager()
        if models is not None:
            models.cleanup(aggressive=True)
        elif hasattr(model, "clear_cuda_cache"):
            model.clear_cuda_cache(aggressive=True)

        # Use the incrementally updated image as final result
        return PipeOutput(output={"image": processed_images})
