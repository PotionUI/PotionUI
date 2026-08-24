import math
from pathlib import Path
from typing import Dict, Any, List
from collections import OrderedDict

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from src.pipelines.outputs import ProgressGenerationOutput, ImageGenerationOutput, CompareImagesGenerationOutput, \
    GalleryGenerationOutput
from src.platform.observability.logger import logger
from src.pipelines.contracts import BasePipe
from src.platform.runtime.device import clear_gpu_memory
from src.platform.runtime.tensors import torch_to_numpy, numpy_to_pil
from src.pipelines.contracts import (
    PipeInput,
    IOType,
    PipeOutput,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)


def get_tiled_scale_steps(width: int, height: int, tile_w: int, tile_h: int, overlap: int) -> int:
    """Number of tile steps for a progress estimate."""
    return math.ceil(width / (tile_w - overlap)) * math.ceil(height / (tile_h - overlap))


def tiled_scale(
        samples: torch.Tensor,
        model,
        user_scale: float,
        tile_w: int = 512,
        tile_h: int = 512,
        overlap: int = 32
) -> torch.Tensor:
    """
    Tile-based upscaling of a batch of images (BCHW) using `model`.
    - `user_scale`: the *final* upscaling factor desired by the user.
      If `model.scale` != `user_scale`, we'll resize from model.scale → user_scale.
    - `tile_w`, `tile_h`: tile size.
    - `overlap`: how many pixels overlap between tiles for feathered blending.
    """

    # The ESRGAN model typically has an internal scale factor (like 4× for RealESRGAN_x4)
    model_scale = getattr(model, "scale", 4)  # default to 4 if not set
    logger.debug(f"[UPSCALER] Model scale: {model_scale}x, user requested {user_scale}x")

    # We'll first upscale each tile by `model_scale`, then if needed, we downscale or upscale
    # to the user's desired factor.
    # Example: If the model is 4× but user wants 2×, we do 4×, then downscale to 2×.

    device = samples.device
    b, c, h, w = samples.shape

    # Final output dimensions
    final_out_h = round(h * user_scale)
    final_out_w = round(w * user_scale)

    # Intermediate tile will be upscaled by `model_scale`
    # so the mosaic is first assembled at model_scale,
    # then we unify the final mosaic to user_scale if needed.
    out_h_model_scale = round(h * model_scale)
    out_w_model_scale = round(w * model_scale)

    # Initialize output buffer (will be set after first tile to match model output channels)
    output_model_scale = None
    out_div = None

    # Force GPU memory cleanup before processing
    clear_gpu_memory()

    total_steps = get_tiled_scale_steps(w, h, tile_w, tile_h, overlap)
    current_step = 0

    for y in range(0, h, tile_h - overlap):
        for x in range(0, w, tile_w - overlap):
            # Current tile dimensions
            tile_h_current = min(tile_h, h - y)
            tile_w_current = min(tile_w, w - x)

            # Extract tile
            tile = samples[:, :, y : y+tile_h_current, x : x+tile_w_current]

            # Run model (upscales by model_scale)
            with torch.no_grad():
                upscaled_tile = model(tile).clone()   # Clone to ensure no memory sharing

            # Initialize buffers after first tile (to match model output channels)
            if output_model_scale is None:
                output_channels = upscaled_tile.shape[1]
                logger.debug(f"[UPSCALER] Model output channels: {output_channels}, input channels: {c}")
                output_model_scale = torch.zeros((b, output_channels, out_h_model_scale, out_w_model_scale), device=device, dtype=upscaled_tile.dtype)
                out_div = torch.zeros((b, output_channels, out_h_model_scale, out_w_model_scale), device=device, dtype=upscaled_tile.dtype)

            # Where in the model-scale mosaic it belongs
            out_y = round(y * model_scale)
            out_x = round(x * model_scale)
            out_tile_h = upscaled_tile.shape[2]
            out_tile_w = upscaled_tile.shape[3]

            # Feathering mask
            mask = torch.ones_like(upscaled_tile)
            feather_px = min(round(overlap * model_scale), out_tile_h, out_tile_w)

            if feather_px > 0:
                for t in range(feather_px):
                    factor = (t + 1) / feather_px
                    # top edge
                    if y > 0:
                        mask[:, :, t : t+1, :] *= factor
                    # bottom edge
                    if y + tile_h_current < h:
                        mask[:, :, -t-1 : -t if t > 0 else None, :] *= factor
                    # left edge
                    if x > 0:
                        mask[:, :, :, t : t+1] *= factor
                    # right edge
                    if x + tile_w_current < w:
                        mask[:, :, :, -t-1 : -t if t > 0 else None] *= factor

            # Add into the output buffer (clone to prevent any memory aliasing issues)
            weighted_tile = (upscaled_tile * mask).clone()
            output_model_scale[:, :, out_y : out_y+out_tile_h, out_x : out_x+out_tile_w] += weighted_tile
            out_div[:, :, out_y : out_y+out_tile_h, out_x : out_x+out_tile_w] += mask.clone()

            current_step += 1
            if current_step % 10 == 0:
                logger.debug(f"[Upscaler] Tiling progress: {current_step}/{total_steps}")

    # Merge tiles - create new tensor to avoid in-place corruption
    output_model_scale = torch.where(out_div > 0, output_model_scale / out_div, output_model_scale).clone()

    # Clean up division buffer immediately
    del out_div
    clear_gpu_memory()

    #
    # Now we have the entire image at the model's scale (e.g. 4×).
    # If user_scale != model_scale, we do a final resize.
    #
    if abs(user_scale - model_scale) > 1e-3:
        logger.debug(f"[Upscaler] Resizing from {model_scale}x -> {user_scale}x ...")
        output_user_scale = F.interpolate(
            output_model_scale,
            size=(final_out_h, final_out_w),
            mode="bicubic",
            align_corners=False
        )
        return output_user_scale
    else:
        # user_scale == model_scale
        return output_model_scale


class ImageUpscaler:
    """
    ESRGAN-based upscaler with tile support that can:
      - Use a model with internal scale=4
      - But optionally final scale=2 (downscale final).
    """
    def __init__(
            self,
            model_path: str,
            device: str = "cuda",
            user_scale: float = 2.0,
            tile_size: int = 512,
            tile_pad: int = 32
    ):
        self.device = device
        self.user_scale = user_scale
        self.tile_size = tile_size
        self.tile_pad = tile_pad

        # Load checkpoint
        state_dict = torch.load(model_path, map_location=device)
        if "params_ema" in state_dict:
            state_dict = state_dict["params_ema"]

        converted = OrderedDict()
        for k, v in state_dict.items():
            k2 = k[7:] if k.startswith("module.") else k
            converted[k2] = v

        # Build model (RealESRGAN / RRDB). RRDBNet infers its native upscale
        # factor from the checkpoint's state dict (number of upsample blocks,
        # see RRDBNet.get_scale()) and sets `self.scale` accordingly during
        # __init__ - do not override it here, or e.g. a real x2 ESRGAN model
        # gets mis-scaled as if it were x4.
        from vendor.chainner_pfn.RRDB import RRDBNet as ESRGAN
        self.model = ESRGAN(converted).to(device)
        self.model.eval()

    def upscale(self, img: Image.Image) -> Image.Image:
        """
        Perform tile-based upscaling.
        - Model might do 4× internally,
        - Then we do a final resize to user_scale if needed.
        """
        # Clean GPU cache before starting
        clear_gpu_memory()

        # Convert PIL image to RGB if needed (ESRGAN expects RGB)
        if isinstance(img, Image.Image):
            logger.debug(f"[UPSCALER] Input image mode: {img.mode}, size: {img.size}")
            # Convert to RGB if not already (handles RGBA, L, etc.)
            if img.mode != 'RGB':
                logger.debug(f"[UPSCALER] Converting from {img.mode} to RGB")
                img = img.convert('RGB')
            img = np.array(img)

        logger.debug(f"[UPSCALER] Input array shape: {img.shape}, dtype: {img.dtype}, range: [{img.min()}, {img.max()}]")
        img_tensor = torch.from_numpy(img).float() / 255.0

        # shape [H, W, C] -> [B, C, H, W]
        if img_tensor.ndim == 3:
            img_tensor = img_tensor.permute(2, 0, 1).unsqueeze(0)
        elif img_tensor.ndim == 2:
            # Grayscale image - add channel dimension and convert to RGB
            img_tensor = img_tensor.unsqueeze(0).unsqueeze(0).repeat(1, 3, 1, 1)

        logger.debug(f"[UPSCALER] Input tensor shape: {img_tensor.shape}, range: [{img_tensor.min():.4f}, {img_tensor.max():.4f}]")
        img_tensor = img_tensor.to(self.device)

        # Tiled scale
        with torch.no_grad():
            output_tensor = tiled_scale(
                img_tensor,
                self.model,
                user_scale=self.user_scale,
                tile_w=self.tile_size,
                tile_h=self.tile_size,
                overlap=self.tile_pad
            )

        logger.debug(f"[UPSCALER] Output tensor shape: {output_tensor.shape}, range: [{output_tensor.min():.4f}, {output_tensor.max():.4f}]")

        # Convert to uint8
        output_np = torch_to_numpy(output_tensor)

        logger.debug(f"[UPSCALER] Output array shape: {output_np.shape}, dtype: {output_np.dtype}, range: [{output_np.min()}, {output_np.max()}]")

        # Aggressive cleanup to prevent state pollution
        del img_tensor
        del output_tensor
        clear_gpu_memory()

        return numpy_to_pil(output_np)


class Upscaler(BasePipe):
    name = "upscaler"
    description = "Tile-based RealESRGAN upscaling with user-defined scale"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """Upscaler requires image input"""
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Input images to upscale", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """Upscaler produces upscaled images"""
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Upscaled images", is_array=True),
        ]

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "model": None,       # Path to .pth
            "scale": 2.0,       # user-requested final scale
            "tile_size": 512,
            "tile_padding": 32,
        }

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable
    ) -> PipeOutput:
        # 1) Grab config
        model_path = self.config.get("model")
        if not model_path:
            raise ValueError("No model path provided in config['model']")

        user_scale = float(self.config.get("scale", 2.0))
        tile_size = int(self.config.get("tile_size", 512))
        tile_pad = int(self.config.get("tile_padding", 32))

        # 2) Create upscaler
        upscaler = ImageUpscaler(
            model_path=Path(model_path),
            device=self.device.type,
            user_scale=user_scale,
            tile_size=tile_size,
            tile_pad=tile_pad
        )

        # 3) Input images
        input_images = pipe_input.input["image"]
        if not isinstance(input_images, list):
            input_images = [input_images]

        # Output status
        generation_outputs(
            ProgressGenerationOutput(
                state=f"Upscaling <<NUMBER:{len(input_images)}>> images with <<MODEL:{Path(model_path).name}>>"
            )
        )

        results = []
        for index, image in enumerate(input_images):
            # Show original
            generation_outputs(ImageGenerationOutput(image=image))

            # 4) Upscale
            upscaled_image = upscaler.upscale(image)

            # 5) Compare
            generation_outputs(
                CompareImagesGenerationOutput(
                    index=index,
                    compare=(None, image),
                    to=("Upscaled Image", upscaled_image),
                )
            )
            results.append(ImageGenerationOutput(image=upscaled_image))

        # Update gallery
        generation_outputs(GalleryGenerationOutput(images=results))

        return PipeOutput(output={"image": [img.image for img in results]})

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Upscaler configuration parameters"""
        return [
            PipeConfigSpec(
                name="model",
                param_type=str,
                default=None,
                description="Path to upscaler model (.pth file)",
                required=True
            ),
            PipeConfigSpec(
                name="scale",
                param_type=float,
                default=2.0,
                description="Final upscaling factor",
                required=False,
                min_value=0.1,
                max_value=8.0
            ),
            PipeConfigSpec(
                name="tile_size",
                param_type=int,
                default=512,
                description="Size of tiles for processing",
                required=False,
                min_value=128,
                max_value=2048
            ),
            PipeConfigSpec(
                name="tile_padding",
                param_type=int,
                default=32,
                description="Padding between tiles for smooth blending",
                required=False,
                min_value=0,
                max_value=128
            ),
        ]
