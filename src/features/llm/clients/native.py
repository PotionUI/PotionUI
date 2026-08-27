"""Native LLM provider — chat/enhance/tagging running in-process via
``transformers``, attached to the app's ``ModelLifecycle``.

Unlike ``OllamaClient``/``OpenAIClient`` (stateless HTTP transports), this
client holds a live, memory-heavy ``torch.nn.Module`` — so every call goes
through the SAME cache/eviction/lease machinery the native diffusion engine
uses for its own models (``src.platform.runtime.model_lifecycle``), under the
cache key ``native/llm/{checkpoint_path}``:

  * ``acquire()`` reuses a warm (CPU-resident) checkpoint across turns instead
    of re-reading it from disk every message.
  * a generation-style lease (``begin_lease``/``end_lease``, the same context
    manager pipes use for a diffusion generation) protects the checkpoint from
    RAM-pressure eviction for the duration of ONE turn's ``generate()`` call —
    without it, a concurrent diffusion generation's admission control could
    evict the weights out from under an in-flight forward pass.
  * between turns the checkpoint sits unleased and evictable, exactly like
    every other cache entry, so "generations win VRAM; chat model offloads
    and returns" falls out of the existing policy for free.

Explicit ``model.to("cuda")``/``model.to("cpu")`` calls move the checkpoint
across the lease boundary. This is deliberately simpler than the native
engine's ``GpuResidencyRegistry`` (layer-level streaming, quantization-aware
placement) — that system is built for the custom native DiT/TE module
classes, not a generic ``transformers.PreTrainedModel``; teaching it this
shape is out of scope for this card.

Family support is an allowlist gated on ``config.model_type`` (read via
``AutoConfig`` before any weights load): text-only dense models load through
``AutoModelForCausalLM``/``AutoTokenizer``, vision-language models through
``AutoModelForImageTextToText``/``AutoProcessor``. Everything else is a clean
``ValueError`` naming what IS supported, never a crash mid-generation.

Tool calling is always prompt-injected (``<tool_call>{...}</tool_call>`` XML
that ``src.features.llm.tools.executor``'s ``_parse_xml_tool_calls`` already
parses as a fallback for any client) — there is no attempt at per-architecture
structured/native tool-call decoding via ``transformers``, which would be a
separate, model-specific undertaking. Admin configs for this provider should
set ``provider_options.force_prompt_tools = true`` so the tool executor uses
its buffered legacy path, the best-tested path for XML-embedded tool calls.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.features.llm.clients.base import LLMResponse
from src.features.llm.native_library import (
    NATIVE_LLM_QUANT_MODES,
    NATIVE_LLM_QUANT_SIZE_FACTORS,
    SUPPORTED_MODEL_TYPES,
    TEXT_MODEL_TYPES,
    VISION_MODEL_TYPES,
    checkpoint_size_gb,
    resolve_native_checkpoint_path,
)
from src.features.llm.native_te_adoption import (
    build_adopted_te,
    is_adopted_te_reference,
    resolve_adopted_te_path,
)
from src.features.llm.repository import LLMConfig
from src.platform.runtime.model_lifecycle.lifecycle import (
    ModelLifecycle,
    file_size_gb,
    get_model_lifecycle,
)

logger = logging.getLogger(__name__)

_LIFECYCLE_KEY_PREFIX = "native/llm/"
# Distinct cache kind so an adopted text encoder loaded FOR CHAT (a transformers
# PreTrainedModel) never collides with the native TE stack's own cache entry for
# the same file (a different consumer, a different in-memory form).
_LIFECYCLE_TE_KEY_PREFIX = "native/llm-te/"
_SENTINEL = object()


def _is_oom(error: BaseException) -> bool:
    return isinstance(error, RuntimeError) and "out of memory" in str(error).lower()


@dataclass
class _LoadedCheckpoint:
    model: Any                  # transformers.PreTrainedModel
    tokenizer: Any               # tokenizer OR processor — both expose apply_chat_template/__call__/decode
    vision: bool
    model_type: str
    # A bnb-quantized checkpoint is placed on the GPU at load time (device_map)
    # and CANNOT round-trip through .to("cpu") — the lease keeps it GPU-resident
    # and evict-only instead of moving it across the lease boundary.
    quantized: bool = False
    # The GpuResidencyRegistry handle registered while this checkpoint
    # is CUDA-resident (see NativeLLMClient._note_resident/_note_offloaded) —
    # None whenever it isn't currently registered (CPU-resident, quantized, or
    # CUDA unavailable). Kept ON the checkpoint (not in a side dict) so the
    # handle's lifetime matches the cache entry's exactly with no separate
    # bookkeeping to leak.
    residency_handle: Any = None


class _NativeLLMResidencyHandle:
    """Evictable handle registering a GPU-resident native LLM checkpoint with
    the native engine's ``GpuResidencyRegistry``.

    Only registered for an unquantized checkpoint actually moved onto CUDA for
    a turn (see ``NativeLLMClient._note_resident`` / ``manage_device`` in
    ``_leased``) — a quantized (bnb) checkpoint is permanently GPU-resident
    and evict-only; moving it with ``.to("cpu")`` would corrupt it, so it is
    never registered here.

    ``offload()`` is what ``GpuResidencyRegistry`` calls when a DiT load needs
    the room — it moves the checkpoint back to CPU and de-registers. Re-use
    across turns: the SAME handle instance is stored on the checkpoint
    (``_LoadedCheckpoint.residency_handle``) and re-registered on every
    placement, so a residency-driven offload between turns is transparent to
    the next ``_leased()`` call (it simply re-places the checkpoint on CUDA
    and re-registers).
    """

    def __init__(self, checkpoint: "_LoadedCheckpoint", key: str, models: "ModelLifecycle") -> None:
        self.checkpoint = checkpoint
        self.key = key
        self.models = models
        self.offloaded = False

    def offload(self) -> None:
        try:
            self.checkpoint.model.to("cpu")
        except Exception:
            logger.warning(
                "[NativeLLM] residency-driven offload to CPU failed for key='%s'; evicting instead",
                self.key, exc_info=True,
            )
            self.models.invalidate(self.key)
        self.offloaded = True
        try:
            from src.platform.runtime.native.memory.residency import get_residency_registry
            get_residency_registry().note_offloaded(self)
        except Exception:
            logger.debug("[NativeLLM] note_offloaded failed for key='%s'", self.key, exc_info=True)


class NativeLLMClient:
    """LLMClient implementation backed by an in-process transformers model."""

    def __init__(self, model_lifecycle: Optional[ModelLifecycle] = None):
        self._model_lifecycle = model_lifecycle

    # -- ModelLifecycle plumbing --------------------------------

    def _models(self) -> ModelLifecycle:
        lifecycle = self._model_lifecycle or get_model_lifecycle()
        if lifecycle is None:
            raise ValueError(
                "Native LLM provider: no ModelLifecycle available yet "
                "(the app container hasn't finished composing)"
            )
        return lifecycle

    @staticmethod
    def _family(model_type: Optional[str]) -> tuple[bool, str]:
        """Returns (is_vision, model_type), or raises a clean ValueError
        naming the supported allowlist."""
        if model_type in TEXT_MODEL_TYPES:
            return False, model_type
        if model_type in VISION_MODEL_TYPES:
            return True, model_type
        raise ValueError(
            f"Native LLM provider: unsupported model family {model_type!r}. "
            f"Supported families: {', '.join(sorted(SUPPORTED_MODEL_TYPES))}"
        )

    @staticmethod
    def _quant_mode(config: LLMConfig) -> str:
        """The requested quantization mode from provider_options, validated
        against the accepted set. ``"none"`` = bf16/auto (default)."""
        mode = (config.provider_options or {}).get("quantization", "none")
        if mode not in NATIVE_LLM_QUANT_MODES:
            raise ValueError(
                f"Native LLM provider: unknown quantization mode {mode!r}. "
                f"Supported: {', '.join(NATIVE_LLM_QUANT_MODES)}"
            )
        return mode

    @staticmethod
    def _load_kwargs(config: LLMConfig) -> Dict[str, Any]:
        """``from_pretrained`` kwargs derived from provider_options - also
        folded into the cache fingerprint (see ``_fingerprint``) so editing
        either busts the cached checkpoint instead of silently reusing one
        loaded under different settings. ``quantization`` (when not "none") is
        carried as a marker here and translated to a ``BitsAndBytesConfig`` in
        ``_build``; it is NOT a real ``from_pretrained`` kwarg."""
        provider_opts = config.provider_options or {}
        kwargs: Dict[str, Any] = {"dtype": provider_opts.get("dtype", "auto")}
        revision = provider_opts.get("revision")
        if revision:
            kwargs["revision"] = revision
        quant_mode = NativeLLMClient._quant_mode(config)
        if quant_mode != "none":
            kwargs["quantization"] = quant_mode
        return kwargs

    @staticmethod
    def _fingerprint(path: str, load_kwargs: Dict[str, Any]) -> str:
        return (
            f"{path}|dtype={load_kwargs.get('dtype')}"
            f"|revision={load_kwargs.get('revision', '')}"
            f"|quant={load_kwargs.get('quantization', 'none')}"
        )

    @staticmethod
    def _bnb_config(quant_mode: str):
        """Build the ``BitsAndBytesConfig`` for a quantized load, raising a
        clear, user-actionable ``ValueError`` when the load can't run here
        (bitsandbytes needs a CUDA GPU and is an optional dependency)."""
        import torch

        if not torch.cuda.is_available():
            raise ValueError(
                f"Native LLM provider: quantized loading ({quant_mode}) requires a CUDA "
                f"GPU, which isn't available on this host — use quantization 'none' instead"
            )
        try:
            import bitsandbytes  # noqa: F401
        except ImportError as e:
            raise ValueError(
                f"Native LLM provider: quantized loading ({quant_mode}) needs the optional "
                f"'bitsandbytes' package, which isn't installed (pip install bitsandbytes)"
            ) from e
        from transformers import BitsAndBytesConfig

        if quant_mode == "int8":
            return BitsAndBytesConfig(load_in_8bit=True)
        return BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

    @staticmethod
    def _quantize_adopted_module(model: Any, quant_mode: str) -> Any:
        """Quantize an adopted single-file checkpoint's Linear layers in place.
        An adopted checkpoint has no HF-layout directory, so it can't
        go through ``from_pretrained(..., quantization_config=...)`` (the path
        ``_build`` uses for an HF-directory checkpoint) — there is no
        ``from_pretrained`` call here to hand a ``quantization_config`` to.

        Reuses the SAME module-replacement transformers' own HF-directory bnb
        loading uses — ``transformers.integrations.bitsandbytes.replace_with_bnb_linear``,
        which decides Linear8bitLt vs. Linear4bit (and their constructor kwargs)
        from the ``BitsAndBytesConfig`` exactly like ``from_pretrained`` does —
        rather than re-deriving that mapping here. That helper alone only
        allocates meta-device (empty) quantized modules, since it's normally
        driven by ``from_pretrained``'s own meta-model state-dict-loading
        pipeline (accelerate's ``set_module_tensor_to_device``); an adopted
        checkpoint has already loaded real weights directly into a live module
        (``build_adopted_te``), so this fills each replaced module's weight
        from the corresponding original tensor via bitsandbytes' own public
        ``Int8Params``/``Params4bit`` classes and moves the model onto the GPU,
        which is what actually triggers quantization for both classes.

        ``lm_head`` is excluded (kept at full precision) — the highest
        precision-sensitive layer, and for a tied checkpoint it shares storage
        with the embedding table rather than being an independent weight.
        """
        import torch.nn as nn

        # _bnb_config raises its own actionable "requires a CUDA GPU" /
        # "needs bitsandbytes" ValueError — must run BEFORE importing
        # bitsandbytes directly below, or a host with neither would instead
        # surface a bare ModuleNotFoundError here.
        quant_config = NativeLLMClient._bnb_config(quant_mode)
        import bitsandbytes as bnb
        from transformers.integrations.bitsandbytes import replace_with_bnb_linear

        originals = {
            name: module for name, module in model.named_modules()
            if isinstance(module, nn.Linear) and name != "lm_head"
        }
        replace_with_bnb_linear(model, modules_to_not_convert=["lm_head"], quantization_config=quant_config)
        for name, original in originals.items():
            new_module = model.get_submodule(name)
            if quant_mode == "int8":
                new_module.weight = bnb.nn.Int8Params(
                    original.weight.data.clone(), requires_grad=False,
                    has_fp16_weights=quant_config.llm_int8_has_fp16_weight,
                )
            else:
                new_module.weight = bnb.nn.Params4bit(
                    original.weight.data.clone(), requires_grad=False,
                    quant_type=quant_config.bnb_4bit_quant_type,
                    compress_statistics=quant_config.bnb_4bit_use_double_quant,
                )
            if original.bias is not None:
                new_module.bias = nn.Parameter(original.bias.data.clone(), requires_grad=False)
        # Params4bit/Int8Params quantize their data on this device move — the
        # same trigger from_pretrained relies on for the HF-directory path.
        model.to("cuda")
        return model

    def _build(self, path: str, load_kwargs: Dict[str, Any]) -> _LoadedCheckpoint:
        """The ModelLifecycle ``loader`` — runs at most once per
        (path, load_kwargs) fingerprint until evicted. An unquantized checkpoint
        builds on CPU and the caller places it on the compute device for the
        duration of one leased turn; a quantized one is placed on the GPU here
        (``device_map``) and stays there (see ``_LoadedCheckpoint.quantized``)."""
        from transformers import (
            AutoConfig,
            AutoModelForCausalLM,
            AutoModelForImageTextToText,
            AutoProcessor,
            AutoTokenizer,
        )

        load_kwargs = dict(load_kwargs)
        quant_mode = load_kwargs.pop("quantization", "none")
        quantized = quant_mode != "none"

        try:
            config = AutoConfig.from_pretrained(path)
        except Exception as e:
            raise ValueError(f"Native LLM provider: could not read config at '{path}': {e}") from e

        vision, model_type = self._family(getattr(config, "model_type", None))
        if quantized:
            load_kwargs["quantization_config"] = self._bnb_config(quant_mode)
            load_kwargs["device_map"] = {"": 0}
        try:
            if vision:
                model = AutoModelForImageTextToText.from_pretrained(path, **load_kwargs)
                tokenizer = AutoProcessor.from_pretrained(path)
            else:
                model = AutoModelForCausalLM.from_pretrained(path, **load_kwargs)
                tokenizer = AutoTokenizer.from_pretrained(path)
        except ImportError as e:
            raise ValueError(
                f"Native LLM provider: missing an optional dependency needed to load "
                f"'{path}' ({model_type}): {e}"
            ) from e
        model.eval()
        return _LoadedCheckpoint(
            model=model, tokenizer=tokenizer, vision=vision, model_type=model_type, quantized=quantized
        )

    @staticmethod
    def _estimated_size_gb(path: str, quant_mode: str) -> Optional[float]:
        """Resident-footprint estimate fed to ModelLifecycle admission
        control: the bf16 shard sum scaled by the quant mode's packing factor
        (this size drives host-RAM eviction, never a VRAM placement decision —
        see the manager's ``acquire`` docstring)."""
        size = checkpoint_size_gb(path)
        if size is not None and quant_mode != "none":
            size *= NATIVE_LLM_QUANT_SIZE_FACTORS.get(quant_mode, 1.0)
        return size

    @staticmethod
    def _resolve_model(name: str) -> tuple[str, bool]:
        """Resolve ``config.model`` to ``(absolute_path, is_adopted_te)``. An
        adopted single-file text encoder is referenced as ``text_encoders/<file>``; every
        other value is an HF-layout directory under ``models/llm/``."""
        if is_adopted_te_reference(name):
            return resolve_adopted_te_path(name), True
        return resolve_native_checkpoint_path(name), False

    def _build_adopted(self, path: str, quant_mode: str = "none") -> _LoadedCheckpoint:
        """Loader for an adopted single-file text encoder: builds a
        transformers chat model + chat tokenizer from the bare safetensors
        (bf16, or fp8-scaled dequantized to bf16), then quantizes it
        in place when the config requests int8/nf4 — see
        ``_quantize_adopted_module``."""
        model, tokenizer, model_type = build_adopted_te(path)
        quantized = quant_mode != "none"
        if quantized:
            model = self._quantize_adopted_module(model, quant_mode)
        return _LoadedCheckpoint(
            model=model, tokenizer=tokenizer, vision=False, model_type=model_type, quantized=quantized
        )

    @staticmethod
    def _cache_key(path: str, is_te: bool) -> str:
        prefix = _LIFECYCLE_TE_KEY_PREFIX if is_te else _LIFECYCLE_KEY_PREFIX
        return f"{prefix}{path}"

    def _acquire(self, path: str, config: LLMConfig, is_te: bool = False) -> _LoadedCheckpoint:
        if is_te:
            quant_mode = self._quant_mode(config)
            size = file_size_gb(path)
            if size is not None and quant_mode != "none":
                size *= NATIVE_LLM_QUANT_SIZE_FACTORS.get(quant_mode, 1.0)
            return self._models().acquire(
                key=self._cache_key(path, True),
                fingerprint=f"te|{path}|bf16|quant={quant_mode}",
                loader=lambda: self._build_adopted(path, quant_mode),
                estimated_vram_gb=size,
            )
        load_kwargs = self._load_kwargs(config)
        quant_mode = load_kwargs.get("quantization", "none")
        return self._models().acquire(
            key=self._cache_key(path, False),
            fingerprint=self._fingerprint(path, load_kwargs),
            loader=lambda: self._build(path, load_kwargs),
            estimated_vram_gb=self._estimated_size_gb(path, quant_mode),
        )

    # -- GpuResidencyRegistry registration for CUDA-resident chat
    # models — belt-and-suspenders so a DiT load's own VRAM admission
    # (src.pipelines.pipes._shared.generation.dit_placement's ensure_free/
    # offload_all) can reclaim a straggling chat checkpoint even if this
    # client's own per-turn restore is ever bypassed. Mirrors
    # checkpoint_loader/sdxl/main.py's _SdxlResidencyHandle — same pattern,
    # a different owning subsystem. ------------------------------------

    def _note_resident(self, checkpoint: _LoadedCheckpoint, key: str, device: str) -> None:
        try:
            from src.platform.runtime.native.memory.residency import get_residency_registry
        except Exception:
            return
        size_gb = self._models().entry_size_gb(key) or 0.0
        handle = checkpoint.residency_handle
        if handle is None:
            handle = _NativeLLMResidencyHandle(checkpoint, key, self._models())
            checkpoint.residency_handle = handle
        handle.offloaded = False
        try:
            get_residency_registry().note_resident(handle, device, size_gb)
        except Exception:
            logger.debug("[NativeLLM] note_resident failed for key='%s'", key, exc_info=True)

    @staticmethod
    def _note_offloaded(checkpoint: _LoadedCheckpoint) -> None:
        handle = checkpoint.residency_handle
        if handle is None:
            return
        try:
            from src.platform.runtime.native.memory.residency import get_residency_registry
            get_residency_registry().note_offloaded(handle)
        except Exception:
            logger.debug("[NativeLLM] note_offloaded failed", exc_info=True)

    @asynccontextmanager
    async def _leased(self, path: str, config: LLMConfig, is_te: bool = False):
        """Acquire the checkpoint (a REAL ModelLifecycle entry - same
        cache/eviction/admission-control path every other model goes
        through, not a private object held outside it), lease it (unevictable)
        for one turn, place it on the compute device, and guarantee it's
        moved back to CPU and released (evictable again) on the way out —
        success or failure.

        A chat turn's `end_lease()` must never run the same-owner sweep
        (`_sweep_unused_owned` only fires when `owner is not None`), and a
        generation's own preset-sweep must never touch this entry either
        (it only matches entries whose owner equals the finishing
        generation's owner exactly, never None). Both directions require
        this turn's `_cache_owner` to actually BE None while it runs -
        `begin_generation(None)` is the public, documented way to force that
        (mirrors how a comfyui/non-native generation clears the tag). This is
        NOT redundant with "nothing else sets it": `_cache_owner` is a
        `contextvars.ContextVar`, ambient in whatever context this coroutine
        runs in - a caller that happens to run this on the same
        thread/context as a just-finished `begin_generation("presets/A")`
        (no thread/task boundary in between to give it a fresh Context)
        would otherwise inherit that stale tag and get owner-swept for real.
        Caught by test_native_client_lifecycle_interactions.py before this
        explicit clear was added; keep it.
        """
        import torch

        models = self._models()
        models.begin_generation(None)
        lease_id = f"native-llm-{uuid.uuid4().hex}"
        models.begin_lease(lease_id)
        key = self._cache_key(path, is_te)
        checkpoint: Optional[_LoadedCheckpoint] = None
        device = "cpu"
        manage_device = False
        try:
            checkpoint = await asyncio.to_thread(self._acquire, path, config, is_te)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            # A quantized checkpoint is already GPU-resident (device_map) and
            # must never be moved across the lease boundary — bnb modules don't
            # round-trip to CPU. It stays put; eviction (not offload) reclaims it.
            manage_device = device != "cpu" and not checkpoint.quantized
            if manage_device:
                try:
                    await asyncio.to_thread(checkpoint.model.to, device)
                except RuntimeError as e:
                    if not _is_oom(e):
                        raise
                    torch.cuda.empty_cache()
                    logger.warning(
                        "[NativeLLM] native LLM fell back to CPU for this turn: CUDA OOM "
                        "during placement — free VRAM or run the Clear VRAM action"
                    )
                    device = "cpu"
                    manage_device = False
                else:
                    # Registered ONLY once actually CUDA-resident, so a
                    # DiT load's own VRAM admission can reclaim this checkpoint
                    # even if the restore below is ever bypassed.
                    self._note_resident(checkpoint, key, device)
            yield checkpoint, device
        finally:
            if checkpoint is not None and manage_device:
                try:
                    await asyncio.to_thread(checkpoint.model.to, "cpu")
                    torch.cuda.empty_cache()
                    self._note_offloaded(checkpoint)
                except Exception:
                    # A checkpoint that CANNOT be verified off the GPU
                    # must never be left as a zombie CUDA-resident cache entry
                    # with no further recovery path (the bug this guards:
                    # "cache_entries=1 ... pinned_cum_gb=0.000" while VRAM
                    # stayed ~full) — evict it outright rather than only warn.
                    # The residency handle (if registered) still lets a DiT
                    # load's admission control reclaim it via note_resident's
                    # last-registered state, but invalidate() is the
                    # deterministic guarantee.
                    logger.warning(
                        "[NativeLLM] failed to move checkpoint back to CPU after lease; "
                        "evicting the cache entry key='%s' so it can't be left GPU-resident",
                        key, exc_info=True,
                    )
                    models.invalidate(key)
            models.end_lease(lease_id)

    # -- prompt / chat-template assembly --------------------------------

    @staticmethod
    def _decode_image(image_data: str):
        from PIL import Image

        try:
            raw = base64.b64decode(image_data)
            return Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as e:
            raise ValueError(f"Native LLM provider: could not decode the attached image: {e}") from e

    def _build_chat(
        self,
        messages: List[Dict[str, str]],
        system_message: Optional[str],
        image_data: Optional[str],
    ) -> tuple[list, Any]:
        chat: list = []
        if system_message:
            chat.append({"role": "system", "content": system_message})
        for m in messages:
            chat.append({"role": m["role"], "content": m["content"]})

        image = None
        if image_data:
            image = self._decode_image(image_data)
            for i in range(len(chat) - 1, -1, -1):
                if chat[i]["role"] == "user":
                    text = chat[i]["content"]
                    chat[i] = {"role": "user", "content": [{"type": "image"}, {"type": "text", "text": text}]}
                    break
        return chat, image

    @staticmethod
    def _apply_template(checkpoint: _LoadedCheckpoint, chat: list, image: Any) -> Dict[str, Any]:
        prompt_text = checkpoint.tokenizer.apply_chat_template(chat, add_generation_prompt=True, tokenize=False)
        if checkpoint.vision:
            return dict(checkpoint.tokenizer(text=[prompt_text], images=[image] if image is not None else None, return_tensors="pt"))
        return dict(checkpoint.tokenizer(prompt_text, return_tensors="pt"))

    @staticmethod
    def _generation_kwargs(config: LLMConfig, options_override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        overrides = options_override or {}
        provider_opts = config.provider_options or {}
        temperature = overrides.get("temperature", config.temperature)
        max_new_tokens = overrides.get("max_tokens", config.max_tokens)
        top_p = overrides.get("top_p", provider_opts.get("top_p"))
        top_k = overrides.get("top_k", provider_opts.get("top_k"))

        kwargs: Dict[str, Any] = {"max_new_tokens": int(max_new_tokens)}
        do_sample = temperature is not None and float(temperature) > 0.0
        kwargs["do_sample"] = do_sample
        if do_sample:
            kwargs["temperature"] = float(temperature)
            if top_p is not None:
                kwargs["top_p"] = float(top_p)
            if top_k is not None:
                kwargs["top_k"] = int(top_k)
        return kwargs

    @staticmethod
    def _inject_tools_into_system_message(system_message: Optional[str], tools: Optional[List[Dict]]) -> str:
        if not tools:
            return system_message or ""
        lines = [
            "\n\nYou have access to the following tools. To call one, respond with "
            "EXACTLY one <tool_call>{\"name\": \"...\", \"arguments\": {...}}</tool_call> "
            "block and nothing else — for example "
            "<tool_call>{\"name\": \"get_form_state\", \"arguments\": {}}</tool_call>. "
            "Do not wrap a call in a <tool_action> tag or a code fence; only a "
            "<tool_call> block runs.",
        ]
        for t in tools:
            fn = t.get("function", t)
            lines.append(
                f"- {fn.get('name')}: {fn.get('description', '')} "
                f"(arguments schema: {json.dumps(fn.get('parameters', {}))})"
            )
        return (system_message or "") + "\n".join(lines)

    # -- LLMClient protocol ----------------------------------------------

    async def generate(
        self,
        prompt: str,
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
    ) -> LLMResponse:
        return await self.generate_with_history([{"role": "user", "content": prompt}], config, system_message, image_data)

    async def generate_with_history(
        self,
        messages: list[Dict[str, str]],
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        import torch

        path, is_te = self._resolve_model(config.model)
        async with self._leased(path, config, is_te) as (checkpoint, device):
            if image_data and not checkpoint.vision:
                raise ValueError(
                    f"Native LLM provider: '{config.model}' ({checkpoint.model_type}) has no "
                    f"vision support, but an image was attached to this turn"
                )
            chat, image = self._build_chat(messages, system_message, image_data)
            gen_kwargs = self._generation_kwargs(config, options_override)

            def _run():
                inputs = self._apply_template(checkpoint, chat, image)
                inputs = {k: v.to(device) for k, v in inputs.items() if hasattr(v, "to")}
                with torch.no_grad():
                    output_ids = checkpoint.model.generate(**inputs, **gen_kwargs)
                prompt_len = inputs["input_ids"].shape[-1]
                completion_ids = output_ids[:, prompt_len:]
                text = checkpoint.tokenizer.decode(completion_ids[0], skip_special_tokens=True)
                return text, prompt_len, int(completion_ids.shape[-1])

            try:
                content, prompt_tokens, completion_tokens = await asyncio.to_thread(_run)
            except RuntimeError as e:
                if _is_oom(e):
                    torch.cuda.empty_cache()
                    raise ValueError(
                        f"Native LLM provider: ran out of GPU memory generating with '{config.model}'"
                    ) from e
                raise

        return LLMResponse(
            content=content,
            model=config.model,
            provider_id="native",
            tokens_used=prompt_tokens + completion_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason="stop",
        )

    async def stream_with_history(
        self,
        messages: list[Dict[str, str]],
        config: LLMConfig,
        system_message: str,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[dict, None]:
        import torch
        from transformers import TextIteratorStreamer

        path, is_te = self._resolve_model(config.model)
        async with self._leased(path, config, is_te) as (checkpoint, device):
            if image_data and not checkpoint.vision:
                raise ValueError(
                    f"Native LLM provider: '{config.model}' ({checkpoint.model_type}) has no "
                    f"vision support, but an image was attached to this turn"
                )
            chat, image = self._build_chat(messages, system_message, image_data)
            gen_kwargs = self._generation_kwargs(config, options_override)

            inputs = await asyncio.to_thread(self._apply_template, checkpoint, chat, image)
            inputs = {k: v.to(device) for k, v in inputs.items() if hasattr(v, "to")}
            prompt_tokens = int(inputs["input_ids"].shape[-1])

            streamer = TextIteratorStreamer(checkpoint.tokenizer, skip_prompt=True, skip_special_tokens=True)
            errors: List[BaseException] = []
            completion_tokens_box = [0]

            def _run():
                try:
                    with torch.no_grad():
                        output_ids = checkpoint.model.generate(**inputs, streamer=streamer, **gen_kwargs)
                    completion_tokens_box[0] = int(output_ids.shape[-1]) - prompt_tokens
                except BaseException as e:  # noqa: BLE001 - relayed to the caller below
                    errors.append(e)

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()

            stream_iter = iter(streamer)
            while True:
                text = await asyncio.to_thread(next, stream_iter, _SENTINEL)
                if text is _SENTINEL:
                    break
                if text:
                    yield {"type": "token", "content": text}
            thread.join()

            if errors:
                error = errors[0]
                if _is_oom(error):
                    torch.cuda.empty_cache()
                    raise ValueError(
                        f"Native LLM provider: ran out of GPU memory generating with '{config.model}'"
                    ) from error
                raise error

            completion_tokens = completion_tokens_box[0]
            yield {
                "type": "usage",
                "tokens_used": prompt_tokens + completion_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }

    async def generate_with_tools(
        self,
        messages: list[Dict[str, Any]],
        config: LLMConfig,
        system_message: str,
        tools: List[Dict] = None,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        """Always prompt-injected — see the module docstring. ``response.tool_calls``
        stays None; the executor's XML-parsing fallback picks up the
        ``<tool_call>`` block from ``response.content`` the same way it does
        for any other client that lacks structured tool calling."""
        merged_system_message = self._inject_tools_into_system_message(system_message, tools)
        return await self.generate_with_history(messages, config, merged_system_message, image_data, options_override)

    async def stream_with_tools(
        self,
        messages: list[Dict[str, Any]],
        config: LLMConfig,
        system_message: str,
        tools: Optional[List[Dict]] = None,
        image_data: Optional[str] = None,
        options_override: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[dict, None]:
        merged_system_message = self._inject_tools_into_system_message(system_message, tools)
        async for event in self.stream_with_history(messages, config, merged_system_message, image_data, options_override):
            yield event
