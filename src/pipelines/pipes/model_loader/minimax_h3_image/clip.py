from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import torch

from src.pipelines.pipes.generator.minimax_h3_image.references import (
    as_rgb,
    image_size,
    resize_to,
    vision_reference_size,
)
from src.pipelines.pipes.model_loader.minimax_h3.clip import MiniMaxH3ClipTextEncoder, _to_hwc_float01
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.memory.residency import TextEncodeWorkingMemoryError
from src.platform.runtime.native.text_encoders import image_content_fingerprint, prompt_embed_key

PICTURE_LABEL = "Picture {}: "
MAX_PROMPT_TOKENS = 4096
CACHE_TAG = "h3image"


class MiniMaxH3ImageClipTextEncoder(MiniMaxH3ClipTextEncoder):
    def __init__(self, te: Any = None, *, device: str = "cuda", model_fingerprint: Optional[str] = None,
                 te_loader: Optional[Callable[[], Any]] = None) -> None:
        super().__init__(te_loader if te_loader is not None else (lambda: te), device=device,
                         model_fingerprint=model_fingerprint)

    def _encode_fn_and_key(self, request: Dict[str, Any]) -> Tuple[Callable[[], Any], Optional[str]]:
        prompt = request["prompt"]
        pictures = [as_rgb(image) for image in request.get("images") or ()]
        pictures = [resize_to(picture, vision_reference_size(*picture.size)) for picture in pictures]
        image_tensors = [_to_hwc_float01(picture) for picture in pictures] or None

        def _encode() -> Dict[str, torch.Tensor]:
            if image_tensors and getattr(self.encoder.module, "visual", None) is None:
                raise NativeEngineUnsupportedError(
                    "MiniMax-H3-Image edits need the text encoder's vision tower, and this file has none"
                )
            return self.encoder.encode_request(
                prompt, images=image_tensors, picture_label=PICTURE_LABEL, max_prompt_tokens=MAX_PROMPT_TOKENS,
            )

        key_parts = [prompt, CACHE_TAG, *(image_content_fingerprint(image) for image in image_tensors or ())]
        return _encode, prompt_embed_key(self._model_fingerprint, self._encoder_role, *key_parts)

    def encode_prompts(self, requests: List[Dict[str, Any]]) -> List[Any]:
        try:
            return super().encode_prompts(requests)
        except TextEncodeWorkingMemoryError as exc:
            raise TextEncodeWorkingMemoryError(
                _pictures_too_costly(requests, exc.working_gb, exc.room_gb),
                working_gb=exc.working_gb, room_gb=exc.room_gb,
            ) from exc

    def _encode_working_gb_for(self, requests: List[Dict[str, Any]]) -> Callable[[Any, List[int]], float]:
        sizes = [
            [vision_reference_size(*image_size(image)) for image in request.get("images") or ()]
            for request in requests
        ]

        def working_gb(encoder: Any, misses: List[int]) -> float:
            needs = []
            for index in misses:
                if sizes[index] and getattr(encoder.module, "visual", None) is None:
                    continue
                tokens, vision_tokens = encoder.request_token_counts(
                    requests[index]["prompt"], sizes[index],
                    picture_label=PICTURE_LABEL, max_prompt_tokens=MAX_PROMPT_TOKENS,
                )
                needs.append(encoder.encode_working_gb(tokens, vision_tokens))
            return max(needs, default=0.0)

        return working_gb


def _aspect(width: int, height: int) -> str:
    ratio = max(width, height) / min(width, height)
    return f"1:{ratio:.0f}" if abs(ratio - round(ratio)) < 0.05 else f"1:{ratio:.1f}"


def _pictures_too_costly(requests: List[Dict[str, Any]], working_gb: float, room_gb: float) -> str:
    sizes = max(
        ([image_size(image) for image in request.get("images") or ()] for request in requests),
        key=lambda found: sum(width * height for width, height in (vision_reference_size(*size) for size in found)),
        default=[],
    )
    count = len(sizes)
    if not count:
        return (
            f"MiniMax-H3-Image cannot encode this prompt: its text encoder needs about {working_gb:.1f} GB of working "
            f"memory for it, more than this card holds even with every weight streamed, and with its weights more "
            f"than the {room_gb:.1f} GB of RAM left to encode on the CPU instead. Send a shorter prompt."
        )
    subject, pronoun = ("this picture", "it") if count == 1 else (f"these {count} pictures", "them")
    narrow = [(index, size) for index, size in enumerate(sizes, start=1) if max(size) >= 2 * min(size)]
    if len(narrow) > 3:
        widest = max((size for _index, size in narrow), key=lambda size: max(size) / min(size))
        shape = f" ({len(narrow)} of them are long and narrow, up to {_aspect(*widest)})"
    elif narrow:
        shape = f" ({', '.join(f'picture {index} is {_aspect(*size)}' for index, size in narrow)})"
    else:
        shape = ""
    return (
        f"MiniMax-H3-Image cannot read {subject}: its text encoder needs about {working_gb:.1f} GB of working memory "
        f"for {pronoun}, more than this card holds even with every weight streamed, and with its weights more than the "
        f"{room_gb:.1f} GB of RAM left to encode on the CPU instead. Each picture is read at a 1024-pixel short edge, so a long, narrow "
        f"picture costs as much as several square ones{shape}. Send fewer pictures, or crop the narrow ones "
        "closer to square."
    )
