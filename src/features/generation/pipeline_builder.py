"""
Pipeline Builder

Transforms form data and preset configurations into executable pipeline
configurations. This is THE canonical build path: it is used both to start
real generations and to render the pipeline graph preview, so that preview
and execution can never diverge.

This component bridges the gap between the API layer (HTTP requests, form
data) and the core layer (pipeline execution). It handles:
- Loading preset templates (when given a preset_id)
- Constructing the generation data structure
- Applying Jinja2 template rendering via PresetProcessor
- Building the final pipeline configuration

Example:
    builder = PipelineBuilder(preset_template_loader, preset_processor)
    built = builder.build_pipeline(
        preset_id='01K0W24A3RADXXABH16YQ7KE90',
        form_data={'prompt': 'beautiful landscape', 'steps': 20},
        mode='txt2img'
    )
    payload = built.to_backend_payload()
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Union

from src.features.presets import PresetTemplateLoader, PresetProcessor
from src.features.presets.templates import PresetTemplate
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

_warned_stale_cache_key: Set[str] = set()


@dataclass
class BuiltPipeline:
    """Result of building a pipeline: the canonical processed-pipe list plus
    everything needed to either execute it on a backend or project it into a
    graph preview."""

    generation_id: str
    preset_id: str
    preset_template: PresetTemplate
    pipes: List[Dict[str, Any]]

    def to_backend_payload(self) -> Dict[str, Any]:
        """Return the exact dict shape backends expect to receive.

        Data only: the payload is the description of a run, so everything in it
        has to be something that could equally be handed to a worker in another
        process. `preset_template` is kept on this object for callers that
        legitimately hold the whole build (the orchestrator reads its version),
        never sent to a backend.
        """
        return {
            'generation_id': self.generation_id,
            'preset_id': self.preset_id,
            'pipes': self.pipes,
        }


class PipelineBuilder:
    """
    Builds pipeline configurations from form data and presets.

    This class is responsible for the form -> pipeline translation. It
    orchestrates PresetTemplateLoader and PresetProcessor to build complete
    pipeline configurations from user input.

    This is the ONE build path in the codebase: both generation execution
    (GenerationOrchestrator) and the pipeline graph preview
    (operations.get_pipeline -> build_graph) call through here.
    """

    def __init__(
        self,
        preset_template_loader: PresetTemplateLoader,
        preset_processor: PresetProcessor
    ):
        """
        Initialize the pipeline builder.

        Args:
            preset_template_loader: Loader for preset templates
            preset_processor: Processor for preset configurations and Jinja2 rendering
        """
        self.preset_template_loader = preset_template_loader
        self.preset_processor = preset_processor
        logger.debug("PipelineBuilder initialized")

    def build_pipeline(
        self,
        preset_id: Union[str, PresetTemplate],
        form_data: Dict[str, Any],
        mode: str = 'txt2img',
        generation_id: Optional[str] = None,
        prompts: Optional[List[Dict[str, str]]] = None,
        prompt: str = '',
        negative_prompt: str = '',
        form_name: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> BuiltPipeline:
        """
        Build a pipeline configuration from form data and preset.

        This method orchestrates the entire pipeline building process:
        1. Resolves the preset template (loads it if given an id)
        2. Constructs the generation data structure
        3. Processes the preset using PresetProcessor (applies Jinja2 templates)
        4. Returns a complete BuiltPipeline ready for execution or graph preview

        Args:
            preset_id: The preset identifier (ULID), or an already-loaded
                PresetTemplate (e.g. when the caller already resolved it,
                such as the preset graph preview).
            form_data: User-submitted form data with field values
            mode: Generation mode (txt2img, img2img, etc.). Default: 'txt2img'
            generation_id: Optional generation ID. If not provided, a new ULID will be generated
            prompts: Array of prompt pairs [{positive: str, negative: str}, ...]. Default: None
            prompt: Legacy positive prompt (deprecated, use prompts). Default: ''
            negative_prompt: Legacy negative prompt (deprecated, use prompts). Default: ''
            form_name: Which form "variant" within the mode this submission came
                from (see docs/presets.md "Variants"). If not given,
                PresetProcessor resolves the mode's default variant - the same
                rule operations.get_form_schema uses.
            user_id: The authenticated user, if any. Resolves user-scoped
                `runtime.settings` entries in the pipeline template context
                (e.g. `nsfw`). `None` for unauthenticated/system callers
                (preview, test suite).

        Returns:
            BuiltPipeline containing generation_id, preset_id, preset_template
            and the canonical processed pipe list.

        Raises:
            ValueError: If preset not found or invalid configuration
        """
        logger.info(f"Building pipeline for preset_id={preset_id}, mode={mode}")

        # Generate ULID if not provided
        if not generation_id:
            generation_id = generate_ulid()
            logger.debug(f"Generated new generation_id: {generation_id}")

        # Resolve preset template - accept either a preset_id string or an
        # already-loaded PresetTemplate (callers that already have it, e.g.
        # the graph preview, should not pay for a second lookup).
        if isinstance(preset_id, PresetTemplate):
            preset_template = preset_id
            resolved_preset_id = preset_template.id
        else:
            logger.debug(f"Loading preset template: {preset_id}")
            preset_template = self.preset_template_loader.load_preset_by_id(preset_id)
            resolved_preset_id = preset_id

        if not preset_template:
            error_msg = f"Preset '{preset_id}' not found"
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.debug(f"Preset template loaded: {preset_template.name} v{preset_template.version}")

        # Build generation data structure
        # Normalize form_data to empty dict if None
        form_data = form_data or {}

        # Normalize prompts - support both legacy and new format
        if prompts is None:
            prompts = [{'positive': prompt, 'negative': negative_prompt}]

        generation_data = {
            'prompts': prompts,  # Array of prompt pairs
            'prompt': prompts[0]['positive'] if prompts else '',  # Legacy compat
            'negative_prompt': prompts[0]['negative'] if prompts else '',  # Legacy compat
            'mode': mode,
            'form_data': form_data,
            'form_name': form_name,
        }

        logger.debug(f"Generation data prepared with {len(form_data)} form fields")

        # Process preset to get processed pipes with Jinja2 rendering
        logger.debug("Processing preset through PresetProcessor")
        try:
            processed_pipes = self.preset_processor.process(preset_template, generation_data, user_id=user_id)
            self._drop_stale_cache_keys(processed_pipes, resolved_preset_id)
            logger.info(f"Pipeline built successfully with {len(processed_pipes)} pipes")
        except Exception as e:
            # TemplateEvaluationError already carries preset_id/source_file/mode/
            # form_name/pipe_id/config_path by the time it gets here
            # (PresetProcessor.process fills in whatever the evaluator itself
            # couldn't know) - str(e) renders all of it, so wrapping in
            # ValueError here still fails the build loudly with full location,
            # not a bare "None" swallowed downstream.
            error_msg = f"Failed to process preset: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from e

        return BuiltPipeline(
            generation_id=generation_id,
            preset_id=resolved_preset_id,
            preset_template=preset_template,
            pipes=processed_pipes
        )

    @staticmethod
    def _drop_stale_cache_keys(pipes: List[Dict[str, Any]], preset_id: str) -> None:
        """
        Pipe-level `cache:` keys are inert: caching goes entirely through
        ModelLifecycleManager.acquire() (key/fingerprint/loader, with
        eviction), not through per-pipe config. Custom presets that still
        declare `cache:` in their pipeline.yml must keep working rather than
        crash or silently misbehave, so this drops the key here with a
        one-line warning pointing at the preset that needs updating.
        """
        for pipe_config in pipes:
            cache_keys = pipe_config.get('cache')
            if cache_keys:
                warn_key = f"{preset_id}:{pipe_config.get('name')}"
                if warn_key not in _warned_stale_cache_key:
                    logger.warning(
                        f"Preset '{preset_id}' pipe '{pipe_config.get('name')}' declares stale "
                        f"'cache: {cache_keys}' — the cache: mechanism was removed in favor of "
                        f"ModelLifecycleManager; this key is now ignored. Remove it from the preset's "
                        f"pipeline.yml."
                    )
                    _warned_stale_cache_key.add(warn_key)
            pipe_config['cache'] = []
