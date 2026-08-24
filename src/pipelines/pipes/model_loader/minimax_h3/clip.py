"""ClipTextEncoder adapter for the native MiniMax-H3 Qwen3-VL-32B conditioning.

Thin adapter over ``MiniMaxH3TextEncoder.encode_request``/``.
encode_reference_request`` (``text_encoders/qwen3.py``) -- the canonical H3
t2va/fl2va and ref2va presentation builders respectively (labels TEXT-tagged
and vision spans VIDEO-tagged, ``grounding_px=0``, matching diffusers'
``MiniMaxH3FL2VATextEncoderStep``/``MiniMaxH3Ref2VATextEncoderStep``,
Apache-2.0). `fl2va` labels every keyframe ``"<Picture i>: "``; `ref2va`
numbers ``"<Picture i>: "``/``"<Video k>: "``/``"<Audio j>: "`` per MODALITY,
which the TE derives from the reference list itself -- this module supplies
the list, never the label text. Presentation building used to live here too;
it was consolidated into the TE's own entry points (mirroring the Krea-2
precedent of image-grounded encode living in the TE, and the diffusers
reference building its presentation inside the text-encoder step) to remove a
duplicate implementation that would otherwise drift out of sync.

This module's own remaining responsibilities: (1) PIL/array -> ``[H, W, 3]``
float ``[0, 1]`` tensor conversion (``encode_request``'s own contract for
``images`` -- already-preprocessed HWC float tensors, the SAME convention
Krea-2's ``Qwen3VLTextEncoder.encode(images=...)`` uses), and the video
counterpart of it: a reference VIDEO arrives from ``media_loader`` as a PATH,
so this module decodes it and puts it on H3's own frame rate and canvas
(``conditioning.normalize_reference_video``, the SAME normalize the generator
pipe's condition-encode runs, truncated to the SAME frame count) before
handing the encoder ``[F, H, W, 3]`` float pixels; (2) mapping
``ConditioningModel``'s ABC shape onto ``encode_request``'s plain
``{"context", "token_tags"}`` dict, with ``n_embeds`` always empty (H3 is
guidance-distilled -- dossier "Guidance -- none"), (3) routing a request that
carries ``references`` to ``encode_reference_request`` instead of
``encode_request``, building the ``MiniMaxH3Reference`` list it takes --
``references``' PRESENCE is what distinguishes a `ref2va` request from
`fl2va` at this adapter's boundary, since the underlying presentation math is
otherwise identical for an image-only reference list, and its ORDER is the
packed order (`prompt_encoder` fixes it with ``pack_references``; nothing
here may re-sort it) -- and (4) GPU placement.

An AUDIO reference carries no media through here at all: a waveform never
reaches the conditioner, which sees only an ``"<Audio j>: "`` label, so the
adapter forwards the kind and nothing else. The sound itself conditions
through the audio VAE's rows in the generator's packed sequence.

**Placement.** Subclasses :class:`SequentialWindowClipTextEncoder`
(``_shared/generation/clip_batch.py``) -- the SAME shared base Krea-2/Qwen/
Flux/Wan/Anima/Z-Image use for a `NativeTextEncoder` with a plain
`.to(device)`/`.encode(...)` contract (unlike LTX's Gemma3, which needs its
own two-stage raw-encode+projection placement). Without this, the 32B TE
never moves off the CPU it was loaded on at all (`NativeEngineLoader.
_load_te` always loads to `device="cpu"`) -- a real-GPU run measured ~190s
and +11.4GB host RSS at ZERO VRAM for one prompt, a full-precision 32B
transformer forward on the CPU. `run_text_encode_batch` (via the base
class's `encode_prompts`) moves the TE to `self.device` ONCE for the whole
per-generation batch (co-resides beside the DiT when it fits, evicts only
under pressure), runs every request's closure, then offloads back to CPU in
its own `finally` -- exactly the "move once, offload in finally" placement
every other native family's text encoder already gets.

**Lazy TE acquisition.** ``__init__`` takes a zero-arg ``te_factory``
callable (``model_loader/minimax_h3/main.py``'s own ``MODELS.acquire(...)``
closure), NOT an already-resolved encoder -- resolved only on first actual
read of ``self.encoder`` (the ``encoder`` property below), i.e. the moment a
request genuinely needs encoding. `prompt_encoder`'s own conditioning cache
(`MODELS.acquire(key="prompt_encoder.conditioning", ...)`) wraps the ENTIRE
encode call: on a hit (same final prompt text as a prior generation), its
`_encode()` closure -- and therefore `clip.encode_prompts`, and therefore
this property -- is never invoked at all. Root cause this fixes: the loader
pipe used to acquire the TE UNCONDITIONALLY at load time, every generation,
regardless of whether `prompt_encoder` would end up needing it -- a real
warm-run trace showed the ~21GB TE reloaded from disk (~21s) even when the
SAME prompt hit `prompt_encoder`'s cache and never touched the TE at all.
`model_fingerprint` (used for `prompt_encoder`'s OWN cache key) is set at
construction and needs no resolved encoder, so the cache lookup itself never
forces the load either -- the whole point.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import torch

from src.pipelines.pipes._shared.generation.clip_batch import SequentialWindowClipTextEncoder
from src.pipelines.pipes._shared.generation.weak_model_ref import WeakModelRef
from src.pipelines.pipes._shared.media.video_read import read_video_frames
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import normalize_reference_video
from src.pipelines.pipes.generator.video_minimax_h3.geometry import align_num_frames
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from src.platform.runtime.native.text_encoders import image_content_fingerprint, prompt_embed_key
from src.platform.runtime.native.text_encoders.qwen3 import MiniMaxH3Reference
from src.platform.runtime.native.text_encoders.qwen3_vl_vision import H3_VISION_MIN_PIXELS
from src.platform.runtime.primitives.clip import ConditioningModel

Tensor = torch.Tensor


def _to_hwc_float01(image: Any) -> Tensor:
    """PIL image (or an already-tensor/array) -> `(H, W, 3)` float32 tensor
    in `[0, 1]` -- `MiniMaxH3TextEncoder.encode_request`'s own contract for
    `images` (a pipe-side concern: the TE never does PIL/CHW conversion or
    resizing beyond the vision tower's own smart-resize)."""
    if hasattr(image, "convert"):
        return torch.from_numpy(np.array(image.convert("RGB"))).float() / 255.0
    tensor = torch.as_tensor(image, dtype=torch.float32)
    return tensor / 255.0 if tensor.max() > 1.5 else tensor


def _to_fhwc_float01(video_path: Any, num_frames: Optional[int]) -> Tensor:
    """A reference video's file path -> `(F, H, W, 3)` float32 tensor in
    `[0, 1]` on MiniMax-H3's own 24 fps and canvas.

    Decodes every frame, then runs the SAME `normalize_reference_video` the
    generator pipe's condition-encode runs, truncated to the SAME
    `num_frames`. The conditioner reads the result far more coarsely (every
    twelfth frame) than the video VAE does, but it has to read the same
    frames: the `<t seconds>` timestamps it labels its vision blocks with are
    derived from position in this sequence.
    """
    if num_frames is None:
        raise ValueError(
            "minimax_h3 text encoder: a ref2va request carries a reference video but no "
            "'reference_video_frames' -- the encoder and the generator's condition-encode would "
            "then truncate it at different frames. Set prompt_encoder's 'reference_video_frames' "
            "to the generated clip's frame count"
        )
    frames, fps = read_video_frames(video_path)
    normalized = normalize_reference_video(frames, fps=fps, num_frames=align_num_frames(int(num_frames)))
    return torch.from_numpy(normalized.copy()).float() / 255.0


def _build_references(request: Dict[str, Any]) -> List[MiniMaxH3Reference]:
    """The request's `ref2va` references, in packed order, as the TE's own
    :class:`MiniMaxH3Reference` records.

    The list order IS the packed order (`prompt_encoder`'s `pack_references`)
    -- the TE numbers its per-modality labels by walking it, and the
    generator's layout walks the same sequence, so nothing here may re-sort
    or filter it. A `"video"` entry is decoded and normalized here; an
    `"audio"` entry carries no media at all and only earns its label.

    A reference video's own soundtrack is NOT read: sound reaches this family
    only through an explicit audio reference, so `has_audio` is exactly
    "this is an audio reference".
    """
    num_frames = request.get("reference_video_frames")
    references: List[MiniMaxH3Reference] = []
    for index, entry in enumerate(request.get("references") or []):
        kind = entry.get("kind")
        media = entry.get("media")
        if kind == "image":
            references.append(MiniMaxH3Reference(kind="image", media=_to_hwc_float01(media)))
        elif kind == "video":
            references.append(MiniMaxH3Reference(kind="video", media=_to_fhwc_float01(media, num_frames)))
        elif kind == "audio":
            references.append(MiniMaxH3Reference(kind="audio", media=None, has_audio=True))
        else:
            raise ValueError(
                f"minimax_h3 text encoder: references[{index}] must be 'image', 'video' or 'audio', "
                f"got {kind!r}"
            )
    return references


def _reference_fingerprint(reference: MiniMaxH3Reference) -> str:
    """Content hash of one reference, for the conditioning cache key.

    An AUDIO reference hashes to a CONSTANT, and that is not a collision: a
    waveform never reaches the conditioner, which sees only an `"<Audio j>: "`
    label, so two requests differing solely in audio CONTENT genuinely produce
    identical conditioning. What must still reach the key is that the
    reference exists and where it sits -- the caller adds the kind and index,
    because both move every later label's number and every later block's place
    on the packed rotary clock.

    Image and video references hash their pixels through the shared
    `image_content_fingerprint`, whose header carries the tensor SHAPE, so an
    `[H, W, 3]` image cannot collide with an `[F, H, W, 3]` video.
    """
    if reference.media is None:
        return "no-pixels"
    return image_content_fingerprint(reference.media)


class MiniMaxH3ClipTextEncoder(SequentialWindowClipTextEncoder):
    """Adapt `MiniMaxH3TextEncoder.encode_request` to the `ClipTextEncoder` ABC.

    H3 is guidance-distilled (dossier "Guidance -- none"): no negative
    prompt, no unconditional branch, anywhere -- `_pack` always returns an
    EMPTY `n_embeds`; any pipe reading this adapter's output must not attempt
    CFG.
    """

    # fl2va's first/last keyframes are ONE FIXED pair shared by every output
    # of a generation (every `quantity` variation denoises the same two
    # keyframes with a different seed) -- not one distinct source image per
    # output the way Qwen-Image-Edit/Krea-2 edit mode works. `prompt_encoder`
    # forwards its whole `image` input list to every request instead of
    # selecting one per index (see ClipTextEncoder.forwards_full_image_batch).
    forwards_full_image_batch = True

    # Storage for the RESOLVED encoder, once `te_factory` has actually run
    # (see the module docstring's "Lazy TE acquisition"). Weakly referenced
    # for the same "conditioning zombie" reason LTX's/Krea-2's adapters
    # document: this pipe's own `clip` output is kept alive by `Generation.
    # generate()`'s `pipe_outputs` list for the entire remaining generation,
    # long after `prompt_encoder` (its only consumer) is done with it. Named
    # `_resolved_encoder` rather than `encoder` directly -- `encoder` is the
    # PROPERTY below that triggers the lazy resolution on first read;
    # `SequentialWindowClipTextEncoder`'s own placement machinery reads
    # `self.encoder`/`self.device` by name and is unaware either way.
    _resolved_encoder = WeakModelRef()

    def __init__(self, te_factory: Callable[[], Any], *, device: str = "cuda",
                 model_fingerprint: Optional[str] = None) -> None:
        self._te_factory = te_factory
        self.device = device
        self._model_fingerprint = model_fingerprint

    @property
    def encoder(self) -> Any:
        resolved = self._resolved_encoder
        if resolved is None:
            resolved = self._te_factory()
            self._resolved_encoder = resolved
        return resolved

    def _encode_fn_and_key(self, request: Dict[str, Any]) -> Tuple[Callable[[], Any], Optional[str]]:
        prompt = request["prompt"]
        images = request.get("images")
        references = _build_references(request) if request.get("references") is not None else None
        image_tensors = [_to_hwc_float01(img) for img in images] if images else None
        needs_vision = bool(image_tensors) or any(
            reference.kind in ("image", "video") for reference in (references or ())
        )
        if needs_vision and getattr(self.encoder.module, "visual", None) is None:
            # A pipe-side guard ahead of `encode_request`'s/`encode_reference_
            # request`'s own identical check -- gives a clear failure at this
            # adapter's boundary rather than several calls deep into the TE.
            # Not a "presentation builder" duplicate (nothing here could drift
            # out of sync with the TE's own token/tag construction): both
            # raise for the exact same condition, "images without a vision
            # tower", nothing more.
            raise NativeEngineUnsupportedError(
                "minimax_h3 text encoder: fl2va keyframes or ref2va references were supplied but this "
                "text encoder has no vision tower loaded -- request the vision-enabled variant at load "
                "time (model_loader/minimax_h3 always requests vision=True; this means the loaded "
                "checkpoint itself carries no vision tower)"
            )
        # `prompt_encoder`'s pixel budget, expressed relative to the output
        # canvas there and resolved to an absolute area here. Clamped UP to
        # H3's own minimum: `preprocess_qwen3_vl_image` applies its max/min
        # bounds as an if/elif single pass, so a max below the min silently
        # wins and hands the vision tower a below-spec grid. Absent (the
        # default) means the request never mentions bounds at all, so
        # `encode_request`'s H3_VISION_MAX_PIXELS default stands.
        requested_max_pixels = request.get("image_max_pixels")
        max_pixels = max(int(requested_max_pixels), H3_VISION_MIN_PIXELS) if requested_max_pixels else None

        def _encode() -> Dict[str, Tensor]:
            bounds = {"max_pixels": max_pixels} if max_pixels else {}
            if references is not None:
                return self.encoder.encode_reference_request(prompt, references, **bounds)
            return self.encoder.encode_request(prompt, images=image_tensors, **bounds)

        # Two different keyframe/reference sets with the same prompt text
        # must not alias to the same cached conditioning (same hazard
        # image_content_fingerprint's own docstring documents for Krea-2).
        key_parts: list = [prompt]
        if references is not None:
            # A ref2va key is built from the REFERENCE LIST, not from
            # `image_tensors`: once references can be video or audio, an
            # images-only key collides across requests that share a prompt and
            # its images but differ in a video or audio reference -- wrong
            # conditioning, silently, with no error. `_reference_fingerprint`
            # covers every modality, and the kind and position of each entry
            # go into the key too, so `[image A, video B]` cannot key the same
            # as `[video B, image A]` (the packed sequence is order-sensitive,
            # so those are two different requests). This also keeps ref2va
            # distinct from an fl2va request with the same prompt and images.
            key_parts.append("ref2va")
            if any(reference.kind == "video" for reference in references):
                # The truncation frame count is part of the presentation: the
                # same reference video cut to two lengths is two different
                # `<t seconds>` block sequences.
                key_parts.append(f"vframes={request.get('reference_video_frames')}")
            key_parts.extend(
                f"{index}:{reference.kind}:{'a' if reference.has_audio else '-'}:"
                f"{_reference_fingerprint(reference)}"
                for index, reference in enumerate(references)
            )
        elif image_tensors:
            key_parts.extend(image_content_fingerprint(img) for img in image_tensors)
        if max_pixels:
            # The SAME image at two budgets is two different vision grids.
            key_parts.append(f"maxpx={max_pixels}")
        cache_key = prompt_embed_key(self._model_fingerprint, getattr(self.encoder, "role", None), *key_parts)
        return _encode, cache_key

    def _pack(self, request: Dict[str, Any], result: Dict[str, Tensor]) -> ConditioningModel:
        return ConditioningModel(
            p_prompt=request["prompt"], n_prompt=request.get("negative_prompt", ""),
            embeds={"context": result["context"], "token_tags": result["token_tags"]},
            n_embeds={},
        )
