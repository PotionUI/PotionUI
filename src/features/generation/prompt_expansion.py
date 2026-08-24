"""Per-image prompt expansion, resolved before the pipeline is built.

The authored positive/negative prompt is a *template*; this turns it into one
concrete realization per image and pins the seed so the expansion and the
latents share it. Kept out of the orchestrator so the seed/expansion contract
that every engine relies on lives in one small, testable place. See
docs/prompts.md.
"""

import logging
from typing import Dict, List, Optional

from src.platform.util.latents import generate_seed
from src.features.prompt.expander import expand_prompts

logger = logging.getLogger(__name__)


class PromptExpander:
    """Expands the authored prompt template into one pair per image."""

    def __init__(self, plugin_registry=None):
        """Initialize the expander.

        Args:
            plugin_registry: Optional plugin registry, forwarded to the
                `prompt.transform` hook inside `expand_prompts`.
        """
        self.plugin_registry = plugin_registry

    def expand_per_image(
        self,
        generation_id: str,
        request,  # GenerationRequest type
        prompts: Optional[List[Dict[str, str]]],
    ) -> Optional[List[Dict[str, str]]]:
        """
        Expand the authored prompt template into one realization per image.

        Engine-agnostic in placement, but only the `native` engine consumes the
        per-image `pairs`: a ComfyUI preset submits one workflow with
        `batch_size = quantity` and a single prompt text node, so it can only
        honor `pairs[0]`. See docs/prompts.md.

        The seed is resolved here rather than in `seed_generator` so that the
        prompt expansion and the latents share it: image `i` gets `base + i` in
        both places, and re-running the same seed reproduces the same batch.
        """
        if not prompts:
            return prompts

        form_data = request.form_data or {}

        # A Video Director document carries literal, per-segment prompts (no
        # `{a|b}` template grammar in v1) and its seed was already resolved by
        # normalize_video_director() in start_generation(); running the
        # dynamicprompts expander over `prompts[0]` here would be a no-op at
        # best and a stale re-roll of the seed at worst.
        if form_data.get('video_director'):
            return prompts

        try:
            quantity = max(1, int(form_data.get('quantity', 1) or 1))
        except (TypeError, ValueError):
            quantity = 1

        try:
            base_seed = int(form_data.get('seed', -1))
        except (TypeError, ValueError):
            base_seed = -1

        if base_seed == -1:
            # Pin the roll now and hand it to seed_generator, which would
            # otherwise draw an independent random seed per image at pipe time
            # and leave the expansion unreproducible.
            base_seed = generate_seed()
            form_data['seed'] = base_seed
            request.form_data = form_data
            logger.debug(f"Resolved seed -1 to {base_seed} for generation {generation_id}")

        # Only the first authored pair is a template. Multi-prompt tabs are a
        # separate concept; expanding each would multiply the image count.
        template = prompts[0]

        try:
            expanded = expand_prompts(
                template.get('positive', '') or '',
                template.get('negative', '') or '',
                count=quantity,
                base_seed=base_seed,
                variables=getattr(request, 'variables', None),
                plugin_registry=self.plugin_registry,
                generation_id=generation_id,
            )
        except Exception as e:
            # Never fail a generation because expansion misbehaved.
            logger.error(f"Prompt expansion failed for {generation_id}, using template: {e}", exc_info=True)
            return prompts

        return [{'positive': e.positive, 'negative': e.negative} for e in expanded]
