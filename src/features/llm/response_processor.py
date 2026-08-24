"""LLM response processing utilities.

This module handles:
- Thinking tag removal from LLM responses
- Image loading and preparation for vision models
"""

import re
import base64
import logging
from pathlib import Path
from io import BytesIO
from typing import Optional

from PIL import Image

from src.features.llm.exceptions import ImageLoadFailedException

logger = logging.getLogger(__name__)


class LLMResponseProcessor:
    """Processes LLM responses and prepares inputs."""

    def remove_thinking_tags(self, content: str) -> str:
        """Remove thinking tags and their content from LLM responses.

        Handles various formats:
        - <think>...</think>
        - <thinking>...</thinking>
        - <thought>...</thought>
        - Case insensitive matching

        Args:
            content: Raw LLM response content

        Returns:
            Cleaned content with thinking tags removed
        """
        # Pattern to match various thinking tags and their content
        # Using DOTALL flag to match across newlines
        patterns = [
            r'<think[^>]*>.*?</think>',
            r'<thinking[^>]*>.*?</thinking>',
            r'<thought[^>]*>.*?</thought>'
        ]

        cleaned_content = content
        for pattern in patterns:
            cleaned_content = re.sub(
                pattern, '', cleaned_content,
                flags=re.DOTALL | re.IGNORECASE
            )

        # Clean up any extra whitespace that may be left
        cleaned_content = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned_content)
        cleaned_content = cleaned_content.strip()

        return cleaned_content

    async def load_and_prepare_image(
        self,
        image_data: str,
        storage_directory: str,
        max_size_mb: int = 5
    ) -> str:
        """Load image from path and prepare for LLM vision API.

        - Converts relative/absolute paths to base64
        - Resizes images larger than max_size_mb
        - Maintains aspect ratio

        Args:
            image_data: File path (relative/absolute) or base64 string
            storage_directory: Base storage directory for relative paths
            max_size_mb: Maximum size in MB (default: 5MB)

        Returns:
            Base64 encoded image string (without data URI prefix)

        Raises:
            ImageLoadFailedException: If image loading/processing fails
        """
        try:
            # Check if already base64
            if image_data.startswith('data:') or (len(image_data) > 100 and '/' not in image_data):
                if image_data.startswith('data:'):
                    return image_data.split(',', 1)[1]
                return image_data

            # Load image from path
            file_path = Path(image_data)

            if not file_path.is_absolute():
                # Relative path - load from storage directory
                storage_dir = Path(storage_directory)
                file_path = storage_dir / file_path

            if not file_path.exists():
                raise ImageLoadFailedException(f"Image file not found: {file_path}")

            # Load image with PIL
            with Image.open(file_path) as img:
                # Convert RGBA to RGB if necessary (for JPEG compatibility)
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(
                        img,
                        mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None
                    )
                    img = background

                # Initial conversion to bytes to check size
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=95)
                size_mb = buffer.tell() / (1024 * 1024)

                # If image is too large, progressively resize
                if size_mb > max_size_mb:
                    # Calculate resize factor
                    # Target 80% of max size to have some buffer
                    target_size_mb = max_size_mb * 0.8
                    scale_factor = (target_size_mb / size_mb) ** 0.5

                    new_width = int(img.width * scale_factor)
                    new_height = int(img.height * scale_factor)

                    # Resize with high-quality resampling
                    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                    # Re-save with adjusted quality if still needed
                    buffer = BytesIO()
                    quality = 85
                    img.save(buffer, format='JPEG', quality=quality)

                    # If still too large, reduce quality further
                    while buffer.tell() / (1024 * 1024) > max_size_mb and quality > 50:
                        buffer = BytesIO()
                        quality -= 10
                        img.save(buffer, format='JPEG', quality=quality)

                    final_size_mb = buffer.tell() / (1024 * 1024)
                    logger.debug(
                        f"Image resized from {size_mb:.2f}MB to {final_size_mb:.2f}MB "
                        f"({img.width}x{img.height}, quality={quality})"
                    )

                # Convert to base64
                buffer.seek(0)
                base64_string = base64.b64encode(buffer.read()).decode('utf-8')
                return base64_string

        except ImageLoadFailedException:
            raise
        except Exception as e:
            logger.error(f"Failed to load/process image: {e}")
            raise ImageLoadFailedException(f"Failed to load/process image: {str(e)}")
