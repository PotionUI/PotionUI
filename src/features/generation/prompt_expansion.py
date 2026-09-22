"""Per-image prompt expansion, resolved before the pipeline is built.

The authored positive/negative prompt is a *template*; this turns it into one
concrete realization per image and pins the seed so the expansion and the
latents share it. Kept out of the orchestrator so the seed/expansion contract
that every engine relies on lives in one small, testable place. See
docs/prompts.md.
"""

import logging
from typing import Any, Dict, List, Optional

from src.platform.util.latents import generate_seed
from src.features.prompt.expander import expand_prompts
from src.features.music_director import compile_sections_to_lyrics

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
        form_data = request.form_data or {}

        video_director = form_data.get('video_director')
        if isinstance(video_director, dict):
            self._expand_director_segments(generation_id, request, video_director)
            first = (video_director.get('segments') or [None])[0]
            if prompts and isinstance(first, dict):
                prompts[0] = {'positive': first.get('prompt', ''), 'negative': first.get('negative_prompt', '')}
            return prompts

        music_director = form_data.get('music_director')
        if isinstance(music_director, dict):
            self._expand_music_document(generation_id, request, music_director)
            return prompts

        if not prompts:
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

    def _expand_director_segments(
        self,
        generation_id: str,
        request,  # GenerationRequest type
        document: Dict[str, Any],
    ) -> None:
        segments = document.get('segments')
        if not segments:
            return

        base_seed = _document_seed(document)
        variables = getattr(request, 'variables', None)

        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            segment_seed = segment.get('seed')
            seed = segment_seed if isinstance(segment_seed, int) else base_seed + index
            expanded = self._expand_pair(
                generation_id, variables, seed, f"segment {index}",
                segment.get('prompt', '') or '', segment.get('negative_prompt', '') or '',
            )
            if expanded is None:
                continue
            segment['prompt'], segment['negative_prompt'] = expanded

    def _expand_music_document(
        self,
        generation_id: str,
        request,  # GenerationRequest type
        document: Dict[str, Any],
    ) -> None:
        base_seed = _document_seed(document)
        variables = getattr(request, 'variables', None)

        expanded = self._expand_pair(
            generation_id, variables, base_seed, "description", document.get('description') or '', '',
        )
        if expanded is not None:
            document['description'] = expanded[0]

        sections = document.get('sections') or []
        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue
            seed = base_seed + index + 1
            for key in ('lyrics', 'style_hint'):
                text = section.get(key)
                if not isinstance(text, str) or not text:
                    continue
                expanded = self._expand_pair(generation_id, variables, seed, f"section {index} {key}", text, '')
                if expanded is not None:
                    section[key] = expanded[0]

        if 'compiled_lyrics' in document:
            document['compiled_lyrics'] = compile_sections_to_lyrics(sections)

    def _expand_pair(
        self,
        generation_id: str,
        variables: Optional[Dict[str, str]],
        seed: int,
        label: str,
        positive: str,
        negative: str,
    ) -> Optional[tuple]:
        try:
            expanded = expand_prompts(
                positive,
                negative,
                count=1,
                base_seed=seed,
                variables=variables,
                plugin_registry=self.plugin_registry,
                generation_id=generation_id,
            )[0]
        except Exception as e:
            logger.error(
                f"Director prompt expansion failed for {generation_id} {label}, using template: {e}",
                exc_info=True,
            )
            return None
        return expanded.positive, expanded.negative


def _document_seed(document: Dict[str, Any]) -> int:
    try:
        return int((document.get('settings') or {}).get('seed'))
    except (TypeError, ValueError):
        return 0
