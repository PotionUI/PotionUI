from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import hashlib
import re

import torch

from src.pipelines.outputs import ProgressGenerationOutput, DiffTextGenerationOutput, ParamGenerationOutput
from src.platform.runtime.primitives.clip import ClipTextEncoder, ConditioningModel
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.pipes._shared.generation.prompt_diff import word_diff
from src.pipelines.pipes._shared.generation.reference_order import pack_references
from src.platform.observability.profiling import get_profiler


def _image_fingerprint(image: Any) -> str:
    """Deterministic content hash of one source image, for the acquire
    fingerprint (any of PIL/tensor/ndarray -- ``media_loader``'s output type,
    kept generic here rather than importing a family-specific tensor helper
    into this shared pipe)."""
    if isinstance(image, torch.Tensor):
        image = image.detach().cpu().numpy()
    if hasattr(image, "tobytes"):
        return hashlib.sha256(image.tobytes()).hexdigest()
    if isinstance(image, (bytes, bytearray)):
        return hashlib.sha256(image).hexdigest()
    return repr(image)  # fallback for anything else content-hashable only via repr


def _media_fingerprint(media: Any) -> str:
    """Deterministic hash of one reference's media, whatever `media_loader`
    handed over for its kind.

    An image arrives decoded (PIL/tensor/ndarray) and hashes its pixels
    through :func:`_image_fingerprint`. A video or an audio reference arrives
    as a PATH -- `media_loader` deliberately does not decode either -- so the
    identity available here is the path plus its size and mtime. That is
    weaker than a content hash (an in-place rewrite keeping both would alias),
    and stronger than the path alone, which would reuse a stale conditioning
    for a re-uploaded file at the same name.
    """
    if isinstance(media, (str, Path)):
        try:
            stat = Path(media).stat()
            return f"{media}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            return str(media)
    return _image_fingerprint(media)


class PromptEncoderPipe(BasePipe):
    name = "prompt_encoder"
    description = "Encodes prompts using CLIP text encoders for SDXL"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Return default configuration for the pipe."""
        return {
            "p_prompt": "",
            "n_prompt": "",
            "quantity": 1,
            "guidance_scale": 7.5,
            "nag_scale": 1.0,
            "clip_skip": None,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("p_prompt", dict, {}, "Positive prompt configuration", required=False),
            PipeConfigSpec("n_prompt", dict, {}, "Negative prompt configuration", required=False),
            PipeConfigSpec("quantity", int, 1, "Number of images to generate", required=False,
                          min_value=1, max_value=20),
            PipeConfigSpec("guidance_scale", float, 7.5, "Guidance scale for CFG", required=False,
                          min_value=1.0, max_value=30.0),
            PipeConfigSpec("nag_scale", float, 1.0, "Normalized Attention Guidance scale, mirrored from the "
                          "generator's own nag_scale config. NAG needs the negative prompt encoded even at "
                          "guidance_scale=1.0 (true CFG off), so >1.0 here forces the negative pass on regardless "
                          "of guidance_scale", required=False, min_value=1.0, max_value=20.0),
            PipeConfigSpec("clip_skip", int, None, "Number of CLIP layers to skip", required=False,
                          min_value=1, max_value=12),
            PipeConfigSpec("pairs", list, [], "Per-image expanded prompt pairs (from the prompt expander)",
                          required=False),
            PipeConfigSpec("quantity", int, 1, "Number of conditioning tensors to generate", required=False,
                          min_value=1, max_value=20),
            PipeConfigSpec("positive_embeddings", list, [], "Positive text embeddings configuration", required=False),
            PipeConfigSpec("negative_embeddings", list, [], "Negative text embeddings configuration", required=False),
            PipeConfigSpec("prompt_helpers", dict, {}, "Prompt helper data", required=False),
            # Vision-grounded encode knobs (Qwen-Image-Edit stage 2 / Krea-2 edit
            # mode) — inert unless the pipe also received an `image`
            # input AND the encoder is vision-capable; ignored otherwise.
            PipeConfigSpec("grounding_px", int, 768, "Longest-side cap (px) for the image sent to a "
                          "vision-grounded text encoder; 0 uses native resolution", required=False,
                          min_value=0, max_value=4096),
            PipeConfigSpec("system_prompt", str, None, "Override the vision-grounded encoder's default "
                          "system prompt", required=False),
            PipeConfigSpec("image_pixel_budget", float, 0.0, "Cap the pixel AREA of each image handed to a "
                          "vision-grounded text encoder, as a multiple of the output canvas area "
                          "(`output_resolution`); 0 leaves the encoder's own bound in place", required=False,
                          min_value=0.0, max_value=16.0),
            PipeConfigSpec("output_resolution", str, None, "Output canvas as WxH — the area "
                          "`image_pixel_budget` multiplies. Inert without it", required=False),
            PipeConfigSpec("reference_video_frames", int, 0, "Frame count of the clip being generated; a "
                          "reference video is truncated to it before being presented, so that the "
                          "encoder and the generator's own condition-encode cut it at the same frame. "
                          "0 (default) leaves a reference video untruncated. Inert without a "
                          "`reference_video` input", required=False, min_value=0),
            PipeConfigSpec("reference_selections", list, [], "Per-request reference SUBSET, index-aligned "
                          "with 'pairs' -- entry i is a list of indices into the packed "
                          "'reference_image'/'reference_video'/'reference_audio' order for output i. An "
                          "absent or empty entry presents every packed reference (the default, and the "
                          "only shape a non-Director ref2va request uses). The H3 adapter numbers "
                          "'<Picture i>'/'<Video k>'/'<Audio j>' against whichever list it is actually "
                          "handed, so a selection RE-LABELS that output's subset from 1 rather than "
                          "keeping the packed set's own numbering. Inert without 'references'",
                          required=False),
        ]

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        """Process the input prompts and return the encoded embeddings."""
        # Check if prompts are provided from previous pipe (e.g., prompt_expander)
        # If so, use those instead of configuration
        p_prompt_from_input = pipe_input.input.get("p_prompt")
        n_prompt_from_input = pipe_input.input.get("n_prompt")

        # Per-image expanded prompts, produced by src/features/prompt/expander.py and
        # handed in by the preset. An upstream prompt_expander owns the text when
        # it supplies one, so its output wins over the per-image pairs.
        pairs = self.config.get("pairs") or []
        if p_prompt_from_input is not None or n_prompt_from_input is not None:
            pairs = []

        if p_prompt_from_input is not None:
            logger.debug("[PROMPT_ENCODER] Using p_prompt from pipe input (expanded)")
            p_prompt_input = p_prompt_from_input
            p_prompt_output = p_prompt_from_input
        else:
            p_prompt_input = self.config.get("p_prompt", {}).get("input", "")
            p_prompt_output = self.config.get("p_prompt", {}).get("output", "")

        if n_prompt_from_input is not None:
            logger.debug("[PROMPT_ENCODER] Using n_prompt from pipe input (expanded)")
            n_prompt_input = n_prompt_from_input
            n_prompt_output = n_prompt_from_input
        else:
            n_prompt_input = self.config.get("n_prompt", {}).get("input", "")
            n_prompt_output = self.config.get("n_prompt", {}).get("output", "")

        quantity = int(self.config.get("quantity", 1))

        # do_cfg is a property of the resolved config alone (binding
        # the true guidance/nag before this pipe runs), so record it once per
        # generation regardless of whether the conditioning cache is hit below:
        # when False the negative prompt is authored but never encoded, and the
        # generation record must say so rather than imply it applied.
        do_cfg = self._do_cfg()
        generation_outputs(ParamGenerationOutput(
            name="negative_applied",
            values=[do_cfg] * quantity,
        ))

        generation_outputs(ProgressGenerationOutput(state="Encoding prompts"))

        clip: ClipTextEncoder = pipe_input.input["text_encoder"]
        models = pipe_input.input.get("MODELS", None)
        # Optional source image(s) (Qwen-Image-Edit's vision-conditioned text
        # encoder, or an H3 fl2va keyframe pair) — absent for every other
        # family/mode, since only an `edit`/`fl2va`-mode pipeline wires an
        # `image` input into this pipe.
        images = pipe_input.input.get("image") or []
        # H3 ref2va references — SEPARATE inputs from `image` (never both on
        # the same request: fl2va's keyframes and ref2va's references
        # condition through different presentation labels and, downstream in
        # the generator pipe, different packed-sequence layouts). The three
        # modality inputs are collapsed into ONE packed order here, by the
        # same `pack_references` the generator pipe derives its reference
        # blocks from — see that module for why both sides must share it.
        references = pack_references(
            pipe_input.input.get("reference_image"),
            pipe_input.input.get("reference_video"),
            pipe_input.input.get("reference_audio"),
        )
        if images and references:
            raise ValueError(
                "prompt_encoder: 'reference_image'/'reference_video'/'reference_audio' (ref2va) are "
                "mutually exclusive with 'image' (fl2va keyframes) — a request is one or the other, "
                "never both"
            )

        def _encode() -> List[ConditioningModel]:
            return self._encode_conditionings(
                clip, p_prompt_input, p_prompt_output, n_prompt_input, n_prompt_output,
                quantity, pairs, generation_outputs, images, do_cfg, references,
            )

        if models is not None:
            # Conditioning depends on both the prompt text and the model
            # identity that encoded it (LoRA/clip_skip changes alter the
            # embeddings even for the same prompt text), so the model's own
            # fingerprint (set by checkpoint_loader) is part of the key.
            #
            # `pairs` must be in the key: with per-image expansion the two
            # scalar prompts no longer identify the batch, and a stale memo
            # would silently reuse one image's conditioning for all of them.
            model_fingerprint = getattr(clip, "_model_fingerprint", None) or repr(id(clip))
            # Two different source images with the same prompt text must not
            # alias to the same cached conditioning batch — same hazard
            # `image_content_fingerprint` (embed_cache.py) documents for the
            # per-encoder-call cache; this is the ANALOGOUS fix for this
            # pipe's own (coarser, whole-batch) MODELS-service cache.
            image_fp = "+".join(_image_fingerprint(img) for img in images) if images else ""
            # ref2va references alias with neither an fl2va keyframe set nor
            # each other — same hazard image_fp guards against, kept as its
            # own key part since 'image' and the reference inputs are mutually
            # exclusive but a stale cache entry from one mode must not be
            # read back for the other.
            #
            # Every modality keys, and so does each reference's KIND and
            # PACKED POSITION: the presentation numbers its labels per
            # modality in this order, so the same media in a different packed
            # position is a different request. A pixel-only key would collide
            # across two requests sharing their images but differing in a
            # video or an audio reference — wrong conditioning, silently.
            reference_fp = "+".join(
                f"{index}:{kind}:{_media_fingerprint(media)}"
                for index, (kind, media) in enumerate(references)
            )
            # A different grounding_px cap changes the vision tower's output for
            # the SAME image; system_prompt changes the encoded template text;
            # a different pixel budget resizes the image before the vision tower
            # sees it. All inert (never part of the key) when there's no image.
            grounding_fp = (
                f"|{self.config.get('grounding_px', 768)}|{self.config.get('system_prompt')}"
                f"|{self._image_max_pixels()}|{self._reference_video_frames()}"
                if (images or references) else ""
            )
            # The fingerprint must key on the FINAL text actually sent to the
            # encoder, not the pre-substitution `p_prompt_input`/`n_prompt_input`:
            # a preset can change what `p_prompt_output` renders to (a toggled
            # quality-prompt suffix, an @helper resolving differently) without
            # touching `p_prompt_input`, which would otherwise reuse stale
            # conditioning for a different final prompt.
            final_prompts = tuple(
                self._final_prompt_pair(
                    p_prompt_output, p_prompt_input, n_prompt_output, n_prompt_input, pairs, i
                )
                for i in range(quantity)
            )
            fingerprint = (
                f"{model_fingerprint}|{final_prompts}|{quantity}|"
                f"{pairs}|"
                f"{self.config.get('guidance_scale', 7.5)}|{self.config.get('nag_scale', 1.0)}|"
                f"{self.config.get('clip_skip')}|"
                f"{self.config.get('positive_embeddings', [])}|"
                f"{self.config.get('negative_embeddings', [])}|"
                f"{image_fp}|{reference_fp}{grounding_fp}|{self.config.get('reference_selections', [])}"
            )
            get_profiler().mark("prompt_encoder.acquire.start", quantity=quantity)
            conditionings = models.acquire(
                key="prompt_encoder.conditioning",
                fingerprint=fingerprint,
                loader=_encode,
            )
            get_profiler().mark("prompt_encoder.acquire.end", quantity=quantity)
        else:
            conditionings = _encode()

        return PipeOutput(
            output={
                "conditioning": conditionings,
            }
        )

    @staticmethod
    def _substitute_pair(template: str, body: str, replacement: str) -> str:
        """
        Swap this image's expanded prompt into the preset's wrapper template.

        `template` is the rendered `p_prompt.output` — the authored prompt (`body`)
        plus whatever the preset appends, e.g. embedding tokens. Splitting on `body`
        preserves that suffix for every image. For image 0, `body == replacement`,
        so this reconstructs `template` byte-for-byte.
        """
        if not body:
            # Nothing authored: the whole template is suffix (embedding tokens).
            return f"{replacement}{template}"
        if body not in template:
            logger.warning(
                "[PROMPT_ENCODER] Could not locate the authored prompt inside the "
                "output template; per-image expansion falls back to the template."
            )
            return template
        head, _, tail = template.partition(body)
        return f"{head}{replacement}{tail}"

    @staticmethod
    def _normalize_prompt_whitespace(text: str) -> str:
        """
        Collapse a prompt template into the flat, single-line form sent to the
        encoder. Newlines become a single space (never dropped outright --
        `"red\\ncar"` must stay two words, not fuse into `"redcar"`), then any
        whitespace/comma runs left over from that substitution (or from a
        dropped @helper/pair value) are collapsed to one.
        """
        text = text.replace("\n", " ")
        text = re.sub(r",\s*,+", ",", text)
        text = re.sub(r" {2,}", " ", text)
        return text.strip()

    def _final_prompt_pair(
        self, p_prompt_output, p_prompt_input, n_prompt_output, n_prompt_input, pairs, index,
    ) -> Tuple[str, str]:
        """
        The exact (positive, negative) prompt text this pipe hands to
        `clip.encode_prompts` for image `index`: pair substitution, then
        normalize -> @helper-expand -> normalize, in that order. Shared by the
        actual encode loop and the cache fingerprint, which must key on this
        final text rather than the pre-substitution `p_prompt_output` template
        -- a preset can change what gets appended to the template (e.g. LTX
        Upscale's `apply_quality_prompt`) without changing `p_prompt_input`.
        """
        p_template, n_template = p_prompt_output, n_prompt_output

        if index < len(pairs):
            pair = pairs[index] or {}
            p_template = self._substitute_pair(
                p_prompt_output, p_prompt_input, pair.get("positive", "") or ""
            )
            n_template = self._substitute_pair(
                n_prompt_output, n_prompt_input, pair.get("negative", "") or ""
            )

        p_prompt = self._normalize_prompt_whitespace(p_template)
        n_prompt = self._normalize_prompt_whitespace(n_template)

        p_prompt = self._process_prompt_helpers(p_prompt)
        n_prompt = self._process_prompt_helpers(n_prompt)

        p_prompt = self._normalize_prompt_whitespace(p_prompt)
        n_prompt = self._normalize_prompt_whitespace(n_prompt)

        return p_prompt, n_prompt

    def _image_max_pixels(self) -> Optional[int]:
        """The pixel-area cap for one image sent to a vision-grounded encoder,
        derived from `image_pixel_budget` x the `output_resolution` canvas area.

        `None` (the default) means "send nothing" -- the encoder keeps its own
        checkpoint-declared bound, which for a vision tower fed a reference
        image can be an order of magnitude above the canvas being generated,
        with the vision forward priced by area. Expressed relative to the
        output rather than as an absolute count so the knob keeps its meaning
        when the resolution changes.

        The encoder's own MINIMUM bound is not applied here: it is a property
        of the loaded checkpoint, so the family adapter that knows it clamps.
        """
        try:
            budget = float(self.config.get("image_pixel_budget") or 0)
        except (TypeError, ValueError):
            return None
        if budget <= 0:
            return None

        parts = str(self.config.get("output_resolution") or "").lower().split("x")
        if len(parts) != 2:
            return None
        try:
            width, height = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        if width <= 0 or height <= 0:
            return None

        return int(budget * width * height)

    def _reference_video_frames(self) -> Optional[int]:
        """The generated clip's frame count, which a reference VIDEO is
        truncated to before it is presented.

        A reference longer than the clip being generated contributes only its
        head, and the generator's own condition-encode truncates it the same
        way. Both cuts have to land on the same frame: if the encoder labels
        `<t seconds>` blocks the conditioning rows never cover (or the other
        way round), the presentation and the packed sequence describe two
        different videos. Nothing here can derive it -- the frame count is the
        generator's request, so the preset states it on both nodes.

        `None` (the default, and every non-video-reference request) means the
        family adapter is told nothing and no truncation applies.
        """
        try:
            frames = int(self.config.get("reference_video_frames") or 0)
        except (TypeError, ValueError):
            return None
        return frames if frames > 0 else None

    def _do_cfg(self) -> bool:
        """Whether the negative prompt is actually encoded and sent to the model.

        NAG (Normalized Attention Guidance) is the generator's mechanism for
        enforcing the negative prompt at guidance_scale=1.0 (true CFG off); it
        needs the negative pass encoded even though true CFG doesn't, so
        nag_scale > 1.0 forces it on -- otherwise n_embeds comes back {} and
        NAG's own `uncond is None` no-op guard silently defeats it.
        """
        return (
            self.config.get("guidance_scale", 7.5) > 1.0
            or self.config.get("nag_scale", 1.0) > 1.0
        )

    def _encode_conditionings(
        self, clip, p_prompt_input, p_prompt_output, n_prompt_input, n_prompt_output,
        quantity, pairs, generation_outputs, images=None, do_cfg=True, references=None,
    ) -> List[ConditioningModel]:
        # Build every image's request FIRST, then encode the whole batch in ONE
        # clip.encode_prompts() call: a per-image encode loop turns an N-image
        # batch into N full GPU move round trips for an encoder large enough that
        # the move dominates (LTX's ~20GB Gemma3-12B). encode_prompts() defaults
        # to the same N-call loop for encoders that don't override it.
        requests = []
        image_max_pixels = self._image_max_pixels()
        for _ in range(quantity):
            p_prompt, n_prompt = self._final_prompt_pair(
                p_prompt_output, p_prompt_input, n_prompt_output, n_prompt_input, pairs, _
            )

            (diff_p_prompt, diff_n_prompt) = diff_prompts(p_prompt_input, p_prompt,
                                                          n_prompt_input, n_prompt)

            logger.debug(f"[PROMPT_ENCODER] p_prompt: {p_prompt}")
            logger.debug(f"[PROMPT_ENCODER] n_prompt: {n_prompt}")

            generation_outputs(DiffTextGenerationOutput(index=_, name="Positive Prompt", diff=diff_p_prompt))
            generation_outputs(DiffTextGenerationOutput(
                index=_, name="Negative Prompt", diff=diff_n_prompt, negative_applied=do_cfg
            ))

            embeddings = {}

            logger.debug(f"[PROMPT_ENCODER] Processing embeddings for iteration {_}")

            # Process positive embeddings
            positive_embeddings_config = self.config.get("positive_embeddings", [])
            embeddings.update(self._extract_embedding_tokens(positive_embeddings_config, "positive"))

            # Process negative embeddings
            negative_embeddings_config = self.config.get("negative_embeddings", [])
            embeddings.update(self._extract_embedding_tokens(negative_embeddings_config, "negative"))

            # Log summary of embeddings to be used
            if embeddings:
                logger.debug(f"[PROMPT_ENCODER] Total embeddings to load: {len(embeddings)}")
                for token, path in embeddings.items():
                    logger.debug(f"[PROMPT_ENCODER]   - Token '{token}' -> {path}")
            else:
                logger.debug("[PROMPT_ENCODER] No embeddings to load")

            request = {
                "prompt": p_prompt,
                "negative_prompt": n_prompt,
                "embedding_files": embeddings,
                "do_classifier_free_guidance": do_cfg,
            }
            if images:
                if getattr(clip, "forwards_full_image_batch", False):
                    # The loaded encoder conditions on a FIXED set of images
                    # shared by every output of this generation (e.g. H3
                    # fl2va's first/last keyframes) -- forward the whole,
                    # unindexed list to every request rather than selecting
                    # one per index (see ClipTextEncoder.forwards_full_image_
                    # batch's docstring).
                    request["images"] = list(images)
                else:
                    # One source image per output (edit mode's single `image`
                    # input); clamp to the last when a batch somehow outruns
                    # the image list, matching img2img's own `_select_source`
                    # convention.
                    request["images"] = [images[_] if _ < len(images) else images[-1]]
                request["grounding_px"] = int(self.config.get("grounding_px", 768))
                request["system_prompt"] = self.config.get("system_prompt")
                if image_max_pixels is not None:
                    request["image_max_pixels"] = image_max_pixels
            elif references:
                # ref2va references, like fl2va's keyframes, are shared
                # across every output of this generation by default --
                # forwarded whole rather than selected per index, same as the
                # `images` branch above. A Director request can instead give
                # output `_` its OWN subset via `reference_selections[_]`
                # (index-aligned with `pairs`); selecting a subset presents
                # ONLY that subset's own list, in the order given, so the H3
                # adapter's per-modality numbering re-labels it from 1 rather
                # than preserving the packed set's global positions -- the
                # only labeling scheme the port's `encode_reference_request`
                # supports, since it numbers whatever list it is handed and
                # has no "skip index k" concept (see
                # generator/video_minimax_h3/windows.py's module docstring,
                # "Per-segment reference selection", for the generator side
                # of this same contract).
                #
                # `references`' PRESENCE is what tells the H3 adapter
                # (`model_loader/minimax_h3/clip.py`) to route to
                # `encode_reference_request` instead of `encode_request`, and
                # its ORDER is the packed order. This pipe never invents label
                # text: `"<Picture i>: "`/`"<Video k>: "`/`"<Audio j>: "` are
                # numbered per modality by the text encoder itself, which is
                # the only place that can do it (a video's labels interleave
                # with the frame-group timestamps only the encoder computes).
                selections = self.config.get("reference_selections") or []
                selection = selections[_] if _ < len(selections) else None
                if selection:
                    out_of_range = [i for i in selection if not 0 <= i < len(references)]
                    if out_of_range:
                        raise ValueError(
                            f"prompt_encoder: 'reference_selections'[{_}] indexes {out_of_range} but "
                            f"only {len(references)} reference(s) were packed"
                        )
                    selected_references = [references[i] for i in selection]
                else:
                    selected_references = references
                request["references"] = [{"kind": kind, "media": media} for kind, media in selected_references]
                request["grounding_px"] = int(self.config.get("grounding_px", 768))
                request["system_prompt"] = self.config.get("system_prompt")
                if image_max_pixels is not None:
                    request["image_max_pixels"] = image_max_pixels
                reference_video_frames = self._reference_video_frames()
                if reference_video_frames:
                    request["reference_video_frames"] = reference_video_frames
            requests.append(request)

        return clip.encode_prompts(requests)

    @staticmethod
    def _extract_embedding_tokens(embeddings_config: list, label: str) -> Dict[str, str]:
        """
        Build a {token: file_path} map from a list-style embeddings config
        (used by the positive/negative/legacy-list embedding blocks).

        Skips entries with no `file_path` or `enabled` set to anything other
        than "true"; derives the token from the filename (without extension)
        when no explicit `token` is given.
        """
        import os

        embeddings: Dict[str, str] = {}
        if not isinstance(embeddings_config, list):
            return embeddings

        for embedding in embeddings_config:
            if not embedding.get("file_path") or embedding.get("file_path") == "":
                continue

            # Check if embedding is enabled (default to true if not specified)
            if embedding.get("enabled", "true") != "true":
                continue

            # Use token as key, or generate one from file path
            token = embedding.get("token")
            if not token:
                # Generate token from filename (without extension)
                token = os.path.splitext(os.path.basename(embedding["file_path"]))[0]

            embeddings[token] = embedding["file_path"]
            logger.debug(f"[PROMPT_ENCODER] {label} embedding: {token}")

        return embeddings

    def _process_prompt_helpers(self, prompt: str) -> str:
        """
        Process @ prefix prompt helpers in the prompt string.
        Replaces @category."item" with the actual value from the helper data.

        Args:
            prompt: The prompt string to process

        Returns:
            The processed prompt string with @ prefixes replaced
        """
        # Pattern to match @category."item" or @category.item
        pattern = r'@([a-zA-Z_][a-zA-Z0-9_]*)\."([^"]+)"'

        def replace_helper(match):
            category = match.group(1)
            item = match.group(2)

            # Get the helper data from configuration
            helper_data = self.config.get("prompt_helpers", {}).get(category, {})

            if not helper_data:
                logger.warning(f"[PROMPT_ENCODER] Prompt helper category '{category}' not found")
                return match.group(0)  # Return original if not found

            # Find the item in the helper data
            value = self._find_helper_value(helper_data, item)

            if value is None:
                logger.warning(f"[PROMPT_ENCODER] Prompt helper item '{item}' not found in category '{category}'")
                return match.group(0)  # Return original if not found

            logger.debug(f"[PROMPT_ENCODER] Replaced @{category}.\"{item}\" with '{value}'")
            return value

        # Replace all occurrences
        processed_prompt = re.sub(pattern, replace_helper, prompt)

        return processed_prompt

    def _find_helper_value(self, helper_data: Dict[str, Any], item_label: str) -> str:
        """
        Find the value for a given item label in helper data.

        Args:
            helper_data: The helper data dictionary
            item_label: The label to search for

        Returns:
            The value associated with the label, or None if not found
        """
        options = helper_data.get("options", [])

        if not options:
            return None

        # Search through options
        for option in options:
            if isinstance(option, dict):
                # Check if this is the item we're looking for
                if option.get("label") == item_label:
                    return option.get("value", item_label)

        return None

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """PromptEncoder requires a text encoder, optionally accepts expanded prompts"""
        return [
            PipeInputSpec("text_encoder", IOType.TEXT_ENCODER, True, "Text encoder for prompt processing", is_array=False),
            PipeInputSpec("p_prompt", IOType.TEXT, False, "Positive prompt (optional, from prompt_expander)", is_array=False),
            PipeInputSpec("n_prompt", IOType.TEXT, False, "Negative prompt (optional, from prompt_expander)", is_array=False),
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service for cross-generation reuse", is_array=False),
            # Optional source image(s) for a vision-conditioned encoder (Qwen-
            # Image-Edit, or an H3 fl2va keyframe pair). Absent for every
            # family/mode that doesn't wire an `image` input into this pipe —
            # encode_prompts() only forwards `images` to encode_prompt() when
            # a request actually carries one.
            PipeInputSpec("image", IOType.IMAGE, False, "Source image(s) for a vision-conditioned encoder", is_array=True),
            # H3 ref2va references, one input per modality — mutually
            # exclusive with `image` (see `process()`'s guard); wired only by
            # a ref2va preset. The packed order across the three is fixed by
            # `pack_references` (images, then videos, then audio), not by the
            # order the edges happen to be declared in.
            PipeInputSpec("reference_image", IOType.IMAGE, False,
                          "ref2va reference image(s), in packed order", is_array=True),
            PipeInputSpec("reference_video", IOType.VIDEO, False,
                          "ref2va reference video(s), packed after every image reference", is_array=True),
            PipeInputSpec("reference_audio", IOType.AUDIO, False,
                          "ref2va reference audio track(s), packed after every video reference",
                          is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """PromptEncoder produces conditioning tensors"""
        return [
            PipeOutputSpec("conditioning", IOType.CONDITIONING, "Encoded prompt conditioning tensors", is_array=True),
        ]


def diff_prompts(o_p_prompt: str, p_prompt: str, o_n_prompt: str, n_prompt: str) -> Tuple[Any, Any]:
    """Generate diffs for both positive and negative prompts, preserving spaces."""
    p_diff = word_diff(o_p_prompt, p_prompt)
    n_diff = word_diff(o_n_prompt, n_prompt)

    return p_diff, n_diff
