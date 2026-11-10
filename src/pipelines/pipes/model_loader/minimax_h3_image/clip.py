from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

import torch

from src.pipelines.pipes.generator.minimax_h3_image.references import as_rgb, resize_to, vision_reference_size
from src.pipelines.pipes.model_loader.minimax_h3.clip import MiniMaxH3ClipTextEncoder, _to_hwc_float01
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
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
