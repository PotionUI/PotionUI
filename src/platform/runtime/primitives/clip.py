from abc import ABC, abstractmethod
from typing import Any, Optional, Dict, List


class ConditioningModel:
    """Container for final prompt embeddings.

    ``embeds``/``n_embeds`` are role-keyed dicts of tensors, so any text
    encoder family can populate whatever roles it produces without the
    container knowing about them in advance. SDXL's dual encoder produces
    ``{"embeds": <sequence embeds>, "pooled": <pooled embeds>}``; a
    single-encoder model (e.g. Chroma's T5) only ever sets ``"embeds"``.
    """

    def __init__(
            self,
            p_prompt: str,
            n_prompt: str,
            embeds: Dict[str, Any],
            n_embeds: Dict[str, Any],
    ):
        self.p_prompt = p_prompt
        self.n_prompt = n_prompt
        self.embeds = embeds
        self.n_embeds = n_embeds


class ClipTextEncoder(ABC):
    """Abstract base class for CLIP text encoders"""

    # Whether this encoder wants the WHOLE per-generation image batch on
    # every request's ``images``, rather than one image selected per output
    # index. False (the default, inherited by every existing encoder) is
    # Qwen-Image-Edit/Krea-2's shape: N outputs, each edited from its OWN
    # source image, so `prompt_encoder` (`_encode_conditionings`) selects
    # `images[i]` (clamped to the last) per request. An encoder that instead
    # conditions on a FIXED set of images shared by every output of one
    # generation (e.g. MiniMax-H3's fl2va: first/last keyframes, identical
    # across every `quantity` variation) sets this True so `prompt_encoder`
    # forwards the full, unindexed list to every request instead. A plain
    # class attribute (checked via `getattr(clip, ..., False)`, matching this
    # file's existing `_model_fingerprint` duck-typing convention) rather
    # than a config flag: it is a structural property of what the loaded
    # encoder's own `images` means, not something a preset should have to
    # remember to set correctly per family.
    forwards_full_image_batch: bool = False

    @abstractmethod
    def encode_prompt(
            self,
            prompt: str,
            negative_prompt: str,
            num_images_per_prompt: int = 1,
            do_classifier_free_guidance: bool = True,
            embedding_files: Optional[Dict[str, str]] = None,
            images: Optional[List[Any]] = None,
    ) -> ConditioningModel:
        """
        Encode text prompts into embeddings.

        Args:
            prompt: The positive prompt text
            negative_prompt: The negative prompt text
            num_images_per_prompt: Number of images to generate per prompt
            do_classifier_free_guidance: Whether to use classifier-free guidance
            embedding_files: Optional dictionary of embedding token to file path mappings
            images: Optional source image(s) for a vision-conditioned encoder
                (Qwen-Image-Edit's Qwen2.5-VL text encoder). ``None``/absent for
                every text-only encoder -- ``encode_prompts``' default loop only
                forwards this kwarg when a request actually carries one, so an
                encoder that never declares it is never called with it.

        Returns:
            ConditioningModel containing the encoded embeddings
        """
        pass

    def encode_prompts(self, requests: List[Dict[str, Any]]) -> List["ConditioningModel"]:
        """
        Encode a BATCH of prompt/negative pairs, one request per output image.

        Each request is a dict with the same keys as :meth:`encode_prompt`'s
        arguments (``prompt``, ``negative_prompt``, and optionally
        ``num_images_per_prompt`` / ``do_classifier_free_guidance`` /
        ``embedding_files``). Returns one ``ConditioningModel`` per request, in
        the same order.

        Default implementation: one :meth:`encode_prompt` call per request —
        the historical behaviour, correct for every encoder whose backing
        model is cheap to move to the GPU per call. Override this for an
        encoder large enough that the per-call move dominates (LTX's ~20GB
        Gemma3-12B pays a full move-to-GPU-and-back round
        trip per call otherwise) to batch every request under ONE
        GPU-resident window instead of N.

        ``images`` is forwarded ONLY when a request actually carries a
        non-empty list — every encoder whose ``encode_prompt`` override never
        declared an ``images`` parameter (every family but Qwen-Image-Edit's)
        is therefore never called with that kwarg at all, not even as
        ``images=None``; this is what keeps this shared default byte-identical
        for every other encoder.
        """
        results = []
        for request in requests:
            kwargs: Dict[str, Any] = dict(
                prompt=request["prompt"],
                negative_prompt=request["negative_prompt"],
                num_images_per_prompt=request.get("num_images_per_prompt", 1),
                do_classifier_free_guidance=request.get("do_classifier_free_guidance", True),
                embedding_files=request.get("embedding_files"),
            )
            images = request.get("images")
            if images:
                kwargs["images"] = images
            results.append(self.encode_prompt(**kwargs))
        return results
