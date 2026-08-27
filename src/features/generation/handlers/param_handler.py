"""
Parameter generation output handler for saving generation parameters to database.

This module provides a handler for ParamGenerationOutput, which saves generation
parameters to the database. It includes special handling for the "model" parameter,
which also creates associations in the generation_models table by looking up model
IDs from file paths.
"""

import logging
from typing import Dict, Any, Optional

from src.pipelines.outputs import GenerationOutput, ParamGenerationOutput
from src.features.generation.handlers.base_handler import BaseGenerationOutputHandler
from src.features.generation.output_types import OutputTypeSpec, output_type_registry
from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)


def _looks_unrendered(value: Any) -> bool:
    """Whether `value` still carries a raw `{{ }}`/`{% %}` marker.

    Defense in depth against a preset templating bug slipping an unrendered
    expression past the pipeline (e.g. a literal `@loop` `items:` list whose
    own elements were never template-processed - see
    `src/features/presets/processor.py`'s `_resolve_loop_items`). Values
    should already be fully rendered by the time they reach a pipe output.
    """
    return isinstance(value, str) and (
        ("{{" in value and "}}" in value) or ("{%" in value and "%}" in value)
    )


class ParamGenerationOutputHandler(BaseGenerationOutputHandler):
    """Handler for ParamGenerationOutput - saves generation parameters to database."""

    def can_handle(self, output: GenerationOutput) -> bool:
        """Check if this handler can process ParamGenerationOutput."""
        return isinstance(output, ParamGenerationOutput)

    def handle(self, output: ParamGenerationOutput) -> Dict[str, Any]:
        """
        Process ParamGenerationOutput - save parameters to database.
        Special handling for "model" parameter - also saves to generation_models table.

        Args:
            output: ParamGenerationOutput to process

        Returns:
            Dictionary with processing metadata
        """
        metadata = {
            'handler': 'ParamGenerationOutputHandler',
            'processed': True,
            'parameter_name': output.name,
            'value_count': len(output.values) if output.values else 0
        }

        try:
            # Import here to avoid circular dependency
            from src.features.generation.parameter_repository import generation_parameter_repo
            from src.features.generation.display_parameters import is_display_parameter

            logger.debug(f"Processing ParamGenerationOutput: name={output.name}, values={output.values}, generation_id={self.generation_id}")

            metadata['saved_count'] = 0
            metadata['parameter_ids'] = []

            if not is_display_parameter(output.name):
                logger.debug(f"[PARAM HANDLER] '{output.name}' is not a display parameter, not recording")
            elif any(_looks_unrendered(v) for v in output.values):
                # All-or-nothing per parameter: dropping only the offending
                # index would shift `parameter_index` for the rest and break
                # the per-image lookup (get_by_generation_and_index).
                logger.warning(
                    f"[PARAM HANDLER] '{output.name}' carries an unrendered template value, "
                    f"not recording: {output.values!r}"
                )
            else:
                # Save parameters directly to generation with index
                saved_params = generation_parameter_repo.create_batch(
                    self.generation_id,
                    output.name,
                    output.values
                )

                metadata['saved_count'] = len(saved_params)
                metadata['parameter_ids'] = [p.id for p in saved_params]

                logger.debug(f"Saved {len(saved_params)} parameters for {output.name} in generation {self.generation_id}")

            # Special handling for "model" parameter - save to generation_models table
            if output.name == "model":
                model_metadata = self._handle_model_parameter(output.values)
                metadata['models'] = model_metadata

            return metadata

        except Exception as e:
            logger.error(f"Error handling ParamGenerationOutput: {str(e)}")
            metadata['error'] = str(e)
            metadata['processed'] = False
            return metadata

    @staticmethod
    def _resolve_model(model_repo, value: str):
        """Resolve a form value to a model row.

        An exact `file_path` match only works when the value is a path on this host.
        ComfyUI presets hand their engine a bare name (`detail.safetensors`) or a name
        with a subdirectory (`style/detail.safetensors`), and those never matched a
        `file_path` - so those generations recorded no models at all.

        Falling back to the identity `(model_type, filename)` is not a guess: it is the
        same rule the model index uses to merge a model across backends. The basename is
        taken from the ref because a ref's directory belongs to whichever engine produced
        it. See docs/models.md.
        """
        from pathlib import Path

        model = model_repo.get_by_file_path(value, include_providers=False)
        if model:
            return model

        filename = Path(value).name
        if not filename:
            return None

        # model_type is not carried on the emitted parameter, so match on filename alone.
        # UNIQUE(model_type, filename) bounds this to one row per type; more than one
        # means the same filename exists as two different kinds of model.
        matches = model_repo.get_by_filename(filename)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.warning(
                f"[PARAM HANDLER] '{filename}' matches {len(matches)} models across "
                f"different types; refusing to guess which one was used"
            )
        return None

    def _handle_model_parameter(self, model_paths: list) -> Dict[str, Any]:
        """
        Handle model parameter - lookup model IDs and save to generation_models table.

        Args:
            model_paths: List of model file paths or engine-native refs

        Returns:
            Metadata about model handling
        """
        from src.features.models.repository import model_repo
        from src.features.generation.model_repository import generation_model_repo

        metadata = {
            'total_paths': len(model_paths),
            'models_found': 0,
            'models_not_found': 0,
            'saved_associations': 0
        }

        model_ids = []

        for model_path in model_paths:
            try:
                model = self._resolve_model(model_repo, model_path)

                if model:
                    model_ids.append(model.id)
                    metadata['models_found'] += 1
                    logger.debug(f"[PARAM HANDLER] Found model for path '{model_path}': {model.id}")
                else:
                    metadata['models_not_found'] += 1
                    logger.warning(f"[PARAM HANDLER] Model not found for path: {model_path}")

            except Exception as e:
                metadata['models_not_found'] += 1
                logger.error(f"[PARAM HANDLER] Error looking up model for path '{model_path}': {str(e)}")

        # Save model associations to generation_models table
        if model_ids:
            try:
                saved_models = generation_model_repo.create_batch(self.generation_id, model_ids)
                metadata['saved_associations'] = len(saved_models)
                logger.debug(f"[PARAM HANDLER] Saved {len(saved_models)} model associations for generation {self.generation_id}")
            except Exception as e:
                logger.error(f"[PARAM HANDLER] Error saving model associations: {str(e)}")
                metadata['save_error'] = str(e)

        return metadata


output_type_registry.register(OutputTypeSpec(
    output_cls=ParamGenerationOutput,
    key='param',
    message_type='generation_update',
    # ParamGenerationOutput carries no display payload today; the base_message
    # (type/generation_id/pipe_id/pipe_name) is emitted as-is, matching the
    # previous serializer's behavior of falling through with no extra fields.
    serializer=None,
    handler_cls=ParamGenerationOutputHandler,
))
