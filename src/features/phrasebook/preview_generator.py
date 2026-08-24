"""
Phrasebook Preview Generator

Generates preview images for phrasebook values using << value >> templates.
Leverages the existing generation infrastructure to create images.
"""
import asyncio
import os
import re
import logging
import random
from typing import Dict, Any, Optional, List

from src.features.phrasebook.dto import PhrasebookStateFilter
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.features.sessions.repository import session_repo
from src.platform.filesystem.storage_driver import FileStorageDriver
from src.platform.settings.settings import SettingsManager

logger = logging.getLogger(__name__)


class PhrasebookPreviewGenerator:
    """
    Generates preview images for phrasebook values.

    Uses a plain << value >> token substitution and leverages the generation
    orchestrator to create images.
    """

    def __init__(
        self,
        category_repository: PhrasebookCategoryRepository,
        value_repository: PhrasebookValueRepository,
        settings_manager: SettingsManager,
        storage_driver: Optional[FileStorageDriver] = None,
    ):
        self.categories = category_repository
        self.values = value_repository
        self.settings_manager = settings_manager
        # `None` unless the container injects its shared driver - resolved
        # lazily on first use, matching `BaseGenerationOutputHandler`.
        self.storage_driver = storage_driver

    def _resolve_storage_driver(self) -> FileStorageDriver:
        if self.storage_driver is not None:
            return self.storage_driver

        from src.platform.filesystem.storage_driver import LocalFileStorageDriver

        base_storage_dir = self.settings_manager.get_setting("file_storage_directory", "storage")
        self.storage_driver = LocalFileStorageDriver(base_storage_dir)
        return self.storage_driver

    def validate_prompt_template(self, template: str) -> bool:
        """
        Validate that a prompt template contains the << value >> placeholder.

        Args:
            template: The prompt template string

        Returns:
            True if valid, raises ValueError if invalid

        Raises:
            ValueError: If template doesn't contain << value >>
        """
        if not re.search(r'<<\s*value\s*>>', template):
            raise ValueError(
                "Prompt template must contain << value >> placeholder. "
                "Example: 'A photo of << value >>'"
            )

        return True

    def render_prompt(self, template: str, value: str) -> str:
        """
        Render a prompt template with a specific value.

        Args:
            template: Template string containing << value >>
            value: The value to substitute

        Returns:
            Rendered prompt string
        """
        return re.sub(r'<<\s*value\s*>>', lambda m: value, template)

    def get_preview_storage_path(self, category_id: str) -> str:
        """
        Get the storage path for preview images.

        Args:
            category_id: The category ID

        Returns:
            Absolute path to the preview storage directory
        """
        base_path = self.settings_manager.get_setting("file_storage_directory", "storage")
        return os.path.join(base_path, "phrasebook", category_id)

    def build_generation_request(
        self,
        session_data: Dict[str, Any],
        preset_id: str,
        rendered_prompt: str,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Build a generation request from session data.

        Args:
            session_data: The session's form data
            preset_id: The preset ID to use
            rendered_prompt: The rendered prompt with value substituted
            negative_prompt: Optional override for negative prompt
            seed: Optional seed (None = random)

        Returns:
            Dictionary suitable for GenerationRequest
        """
        # Start with session form data
        form_data = dict(session_data.get('form_data', session_data))

        # Determine final negative prompt
        final_negative = negative_prompt
        if final_negative is None:
            # Try to get from session data
            final_negative = (
                session_data.get('negative_prompt') or
                form_data.get('negative_prompt', '')
            )

        # Determine seed
        final_seed = seed if seed is not None else random.randint(0, 2147483647)

        return {
            'preset_id': preset_id,
            'prompts': [{
                'positive': rendered_prompt,
                'negative': final_negative
            }],
            'mode': session_data.get('mode', 'txt2img'),
            'form_data': form_data,
            'seed': final_seed
        }

    async def generate_previews(
        self,
        category_id: str,
        session_id: str,
        prompt_template: str,
        mode: str,
        user_id: str,
        generation_orchestrator,  # GenerationOrchestrator (passed to avoid circular import)
        value_ids: Optional[List[str]] = None,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate preview images for phrasebook values.

        This method:
        1. Loads the session for form configuration
        2. Gets values to generate (all active if value_ids is None)
        3. Validates the template
        4. For each value:
           a. Renders the prompt (substitutes << value >>)
           b. Creates a generation request
           c. Starts the generation
        5. Returns a summary of the generation results

        Args:
            category_id: The category ID to generate previews for
            session_id: Session ID containing form configuration
            prompt_template: Template string containing << value >>
            mode: The session mode to use (e.g., 'txt2img', 'img2img')
            user_id: The user ID
            generation_orchestrator: The generation orchestrator instance
            value_ids: Specific value IDs (None = all active values)
            negative_prompt: Override session's negative prompt
            seed: Fixed seed (None = random per generation)

        Returns:
            Dictionary with:
            - total: Total values to process
            - started: Number of generations started
            - failed: Number of generations that failed to start
            - generations: List of {value_id, generation_id} for started generations

        Raises:
            ValueError: If category/session not found or template invalid
        """
        # Validate template
        self.validate_prompt_template(prompt_template)

        # Load category
        category = self.categories.get_by_id(category_id, user_id)
        if not category:
            raise ValueError(f"Category not found: {category_id}")

        # Load session
        session = session_repo.get_by_id(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        # Validate mode exists in session data
        # Session data structure: session.data[mode] = { formData: {...}, negativePrompt: '...', ... }
        mode_data = session.data.get(mode, {})
        if not mode_data:
            raise ValueError(f"Mode '{mode}' not found in session data. Available modes: {list(session.data.keys())}")

        # Get values to generate
        if value_ids:
            # Get specific values
            all_values = self.values.get_by_category(
                category_id, user_id, PhrasebookStateFilter.ALL
            )
            values_to_generate = [v for v in all_values if v.id in value_ids]
        else:
            # Get all active values
            values_to_generate = self.values.get_by_category(
                category_id, user_id, PhrasebookStateFilter.ACTIVE
            )

        if not values_to_generate:
            return {
                'total': 0,
                'started': 0,
                'completed': 0,
                'failed': 0,
                'generations': []
            }

        # Build generation request class from dto
        from src.features.generation.dto import GenerationRequest, PromptPair

        results = {
            'total': len(values_to_generate),
            'started': 0,
            'completed': 0,
            'failed': 0,
            'generations': []
        }

        # Extract formData from mode data (frontend uses camelCase)
        # mode_data was already validated above
        session_form_data = mode_data.get('formData', {})

        # Process generations SEQUENTIALLY to avoid overwhelming ComfyUI
        # ComfyUI can only handle one generation at a time properly
        for i, value in enumerate(values_to_generate):
            logger.debug(
                f"Processing preview {i + 1}/{len(values_to_generate)}: '{value.label}'"
            )

            try:
                # Render prompt
                rendered_prompt = self.render_prompt(prompt_template, value.value)

                # Determine seed for this generation
                generation_seed = seed if seed is not None else random.randint(0, 2147483647)

                # Determine negative prompt
                final_negative = negative_prompt
                if final_negative is None:
                    # Get from mode data (frontend uses camelCase)
                    final_negative = mode_data.get('negativePrompt', '')

                # Build form_data from session's formData
                # Copy to avoid mutating the session
                form_data = dict(session_form_data)

                # Override seed
                form_data['seed'] = generation_seed

                # Build request - this matches how the generation page sends data
                request = GenerationRequest(
                    preset_id=session.preset_id,
                    prompts=[PromptPair(positive=rendered_prompt, negative=final_negative)],
                    mode=mode,
                    form_data=form_data
                )

                # Create completion event to wait for this generation
                completion_event = asyncio.Event()
                generation_error = None

                # Create output callback that will save the preview image AND signal completion
                def create_preview_callback(value_id: str, category_id: str, event: asyncio.Event):
                    async def callback(generation_id: str, output):
                        nonlocal generation_error
                        try:
                            if output is None:
                                # Generation complete - signal the event
                                logger.debug(f"Generation {generation_id} completed, signaling event")
                                event.set()
                            else:
                                # Process the output (save preview image)
                                await self._handle_preview_output(
                                    generation_id, output, value_id, category_id, user_id
                                )
                        except Exception as e:
                            logger.error(f"Error in preview callback: {e}")
                            generation_error = e
                            event.set()  # Signal completion even on error
                    return callback

                preview_callback = create_preview_callback(value.id, category_id, completion_event)

                # Start generation
                result = await generation_orchestrator.start_generation(
                    request,
                    user_id,
                    output_callback=preview_callback
                )

                generation_id = result['generation_id']

                results['started'] += 1
                results['generations'].append({
                    'value_id': value.id,
                    'value_label': value.label,
                    'generation_id': generation_id,
                    'rendered_prompt': rendered_prompt
                })

                logger.debug(
                    f"Started preview generation for value '{value.label}' "
                    f"(id={value.id}): generation_id={generation_id}"
                )

                # WAIT for this generation to complete before starting the next
                # This prevents overwhelming ComfyUI with concurrent requests
                try:
                    # Wait up to 10 minutes for each generation
                    await asyncio.wait_for(completion_event.wait(), timeout=600.0)

                    if generation_error:
                        logger.error(f"Generation {generation_id} had an error: {generation_error}")
                    else:
                        results['completed'] += 1
                        logger.debug(
                            f"Completed preview {i + 1}/{len(values_to_generate)}: '{value.label}'"
                        )

                except asyncio.TimeoutError:
                    logger.error(
                        f"Timeout waiting for generation {generation_id} to complete "
                        f"(value: {value.label})"
                    )
                    results['failed'] += 1

            except Exception as e:
                results['failed'] += 1
                logger.error(
                    f"Failed to start preview generation for value '{value.label}' "
                    f"(id={value.id}): {e}"
                )

        logger.info(
            f"Preview generation batch complete: "
            f"{results['completed']}/{results['total']} completed, "
            f"{results['failed']} failed"
        )

        return results

    async def _handle_preview_output(
        self,
        generation_id: str,
        output,
        value_id: str,
        category_id: str,
        user_id: str
    ):
        """
        Handle generation output for preview image.

        When a final image is generated, save it using FileStore and
        update the value's preview_file_id.

        Args:
            generation_id: The generation ID
            output: The generation output
            value_id: The value ID
            category_id: The category ID
            user_id: The user ID
        """
        from src.pipelines.outputs import ImageGenerationOutput, GalleryGenerationOutput
        from src.features.generation.file_repository import file_repo

        logger.debug(
            f"Preview callback received for value {value_id}, generation {generation_id}: "
            f"output_type={type(output).__name__}"
        )

        # Only process image outputs that are not temporary
        if output is None:
            logger.debug(f"Preview callback: completion signal (None) for value {value_id}")
            return

        # Handle GalleryGenerationOutput - extract first non-temporary image
        image_output = None
        if isinstance(output, GalleryGenerationOutput):
            logger.debug(f"Preview callback: processing GalleryGenerationOutput for value {value_id}")
            # Get the first non-temporary image from the gallery
            for img in output.images:
                if not getattr(img, 'temporary', True):
                    image_output = img
                    break
            if not image_output:
                logger.debug(f"Preview callback: no non-temporary images in gallery for value {value_id}")
                return
        elif isinstance(output, ImageGenerationOutput):
            if getattr(output, 'temporary', False):
                logger.debug(f"Preview callback: skipping temporary image for value {value_id}")
                return
            image_output = output
        else:
            logger.debug(
                f"Preview callback: skipping non-image output {type(output).__name__} for value {value_id}"
            )
            return

        # Check if output has an image
        if not image_output.image:
            logger.warning(f"Preview callback: no image on output for value {value_id}")
            return

        logger.debug(f"Preview callback: PROCESSING image for value {value_id}")

        try:
            # The image handler already saved the file and created a file record.
            # We just need to find that file record and link it to the phrasebook value.

            # Get files associated with this generation
            generation_files = file_repo.get_generation_files(generation_id)
            if not generation_files:
                logger.warning(
                    f"Preview callback: no files found for generation {generation_id} "
                    f"(value {value_id})"
                )
                return

            # Use the first file (there should typically be one final image per generation)
            file_record = generation_files[0]
            file_id = file_record.id

            logger.debug(f"Preview callback: found existing file record with ID: {file_id}")

            # Update value with preview file ID
            update_result = self.values.update_preview_file(value_id, user_id, file_id, generation_id)

            logger.debug(
                f"Linked preview for value {value_id}: file_id={file_id} "
                f"(db_updated={update_result})"
            )

        except Exception as e:
            logger.error(
                f"Failed to link preview image for value {value_id}: {e}",
                exc_info=True
            )

    def delete_preview_image(self, value_id: str, category_id: str, user_id: str) -> bool:
        """
        Delete a preview image for a value.

        Args:
            value_id: The value ID
            category_id: The category ID (not used, kept for API compatibility)
            user_id: The user ID

        Returns:
            True if deleted, False otherwise
        """
        from src.features.generation.file_repository import file_repo

        value = self.values.get_by_id(value_id, user_id)
        if not value or not value.preview_file_id:
            return False

        try:
            # Get the file record
            file_record = file_repo.get_by_id(value.preview_file_id)
            if file_record:
                # Delete the actual file
                self._resolve_storage_driver().delete(file_record.file_path)

                # Delete the file record
                file_repo.delete(value.preview_file_id)

            # Clear the preview file ID in database
            self.values.update_preview_file(value_id, user_id, None, None)

            logger.info(f"Deleted preview image for value {value_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete preview image for value {value_id}: {e}")
            return False
