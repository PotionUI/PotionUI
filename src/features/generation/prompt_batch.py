import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.pipelines.outputs import (
    GalleryGenerationOutput,
    GenerationOutput,
    ImageGenerationOutput,
    VideoGenerationOutput,
)

MAX_BATCH_IMAGES = 20

PROMPT_BATCH_SPLIT_NOTICE = (
    "The images were generated, but they could not be separated into one history entry per prompt. "
    "They are all kept together in this entry."
)


class PromptBatchError(ValueError):
    pass


def is_prompt_batch(request) -> bool:
    prompts = getattr(request, 'prompts', None)
    return getattr(request, 'batch_prompts', False) is True and isinstance(prompts, list) and len(prompts) > 1


def per_prompt_quantity(form_data: Optional[Dict[str, Any]]) -> int:
    try:
        return max(1, int((form_data or {}).get('quantity', 1) or 1))
    except (TypeError, ValueError):
        return 1


def check_prompt_batch(request, backend) -> None:
    if not is_prompt_batch(request):
        return
    form_data = getattr(request, 'form_data', None) or {}
    authored_video = isinstance(form_data.get('video_director'), dict) and getattr(request, '_one_shot_director', False) is not True
    if authored_video or isinstance(form_data.get('music_director'), dict):
        raise PromptBatchError(
            "Batch prompts cannot be combined with the Video Director or Music Director, which already "
            "plan their own shots and sections. Send a single prompt instead."
        )
    if not getattr(backend, 'supports_prompt_batch', False):
        raise PromptBatchError(
            f"Backend '{backend.name}' (engine '{backend.engine}') renders one prompt per generation, "
            "so batch prompts are not available for this preset. Send a single prompt instead."
        )
    count = len(request.prompts)
    quantity = per_prompt_quantity(request.form_data)
    total = count * quantity
    if total > MAX_BATCH_IMAGES:
        raise PromptBatchError(
            f"A batch can produce at most {MAX_BATCH_IMAGES} images, but {count} prompts x {quantity} "
            f"per prompt is {total}. Send fewer prompts or lower the quantity."
        )


@dataclass
class PromptBatch:
    per_prompt: int
    base_seed: Optional[int] = None
    pairs: List[Dict[str, str]] = field(default_factory=list)
    cursor: int = 0
    files: Dict[str, int] = field(default_factory=dict)
    last: Optional[Tuple[int, str]] = None

    @property
    def count(self) -> int:
        return len(self.pairs) // self.per_prompt if self.per_prompt else 0

    def apply_to_pipes(self, pipes: List[Dict[str, Any]]) -> None:
        marker = {'per_prompt': self.per_prompt, 'count': self.count}
        for pipe in pipes:
            pipe.setdefault('config', {})['prompt_batch'] = dict(marker)

    def record_saved(self, output: GenerationOutput) -> None:
        if isinstance(output, GalleryGenerationOutput):
            items = list(output.images or []) + list(output.videos or [])
        else:
            items = [output]
        for item in items:
            index = getattr(item, 'prompt_index', None)
            path = getattr(item, '_saved_path', None)
            if isinstance(index, int) and isinstance(path, str) and path:
                self.files[path] = index

    def seed_for(self, index: int) -> Optional[int]:
        return None if self.base_seed is None else self.base_seed + index * self.per_prompt

    @classmethod
    def plan(cls, request) -> Optional['PromptBatch']:
        if not is_prompt_batch(request):
            return None
        return cls(per_prompt=per_prompt_quantity(request.form_data))

    def settle(self, request, pairs: Optional[List[Dict[str, str]]]) -> None:
        self.pairs = list(pairs or [])
        try:
            self.base_seed = int((request.form_data or {}).get('seed'))
        except (TypeError, ValueError):
            self.base_seed = None

    def locate(self, ordinal: int) -> Optional[Tuple[int, str]]:
        if ordinal < 0 or ordinal >= len(self.pairs):
            return None
        return ordinal // self.per_prompt, self.pairs[ordinal].get('positive', '')

    def stamp(self, output: GenerationOutput) -> None:
        if isinstance(output, GalleryGenerationOutput):
            for item in list(output.images or []) + list(output.videos or []):
                self._stamp_item(item)
        elif isinstance(output, (ImageGenerationOutput, VideoGenerationOutput)):
            self._stamp_item(output)

    def _stamp_item(self, item) -> None:
        if getattr(item, 'temporary', True) or getattr(item, 'isArtifact', False):
            return
        derived = getattr(item, 'derived', False)
        seed = getattr(item, 'seed', None)
        if self.base_seed is not None and isinstance(seed, int) and 0 <= seed - self.base_seed < len(self.pairs):
            located = self.locate(seed - self.base_seed)
        elif derived:
            located = self.last
        else:
            located = self.locate(self.cursor)
            self.cursor += 1
        if located is None:
            return
        item.prompt_index, item.prompt_text = located
        if not derived:
            self.last = located


def prompt_state_for(state: Optional[Dict[str, Any]], index: int, count: int) -> Optional[Dict[str, Any]]:
    if not isinstance(state, dict):
        return state
    entries = state.get('prompts')
    if not isinstance(entries, list) or len(entries) != count:
        return copy.deepcopy(state)
    rest = {key: copy.deepcopy(value) for key, value in state.items() if key != 'prompts'}
    entry = entries[index]
    if isinstance(entry, dict):
        rest.update(copy.deepcopy(entry))
    return rest
