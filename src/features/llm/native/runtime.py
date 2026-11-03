
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import contextvars
import io
import json
import logging
import os
import threading
import uuid
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional

from src.features.llm.context_budget import TokenCounter
from src.features.llm.model_listing import DiscoveredWindow, positive_int
from src.features.llm.native_library import (
    NATIVE_LLM_QUANT_MODES,
    NATIVE_LLM_QUANT_SIZE_FACTORS,
    SUPPORTED_MODEL_TYPES,
    TEXT_MODEL_TYPES,
    VISION_MODEL_TYPES,
    checkpoint_size_gb,
    list_native_checkpoints,
    resolve_native_checkpoint_path,
)
from src.features.llm.native_te_adoption import (
    build_adopted_te,
    is_adopted_te_reference,
    resolve_adopted_te_path,
)
from src.features.llm.repository import LLMConfig
from src.platform.filesystem.model_roots import ModelRootResolver
from src.platform.runtime.model_lifecycle.lifecycle import (
    ModelLifecycle,
    file_size_gb,
    get_model_lifecycle,
)

logger = logging.getLogger(__name__)

_LIFECYCLE_KEY_PREFIX = "native/llm/"
_LIFECYCLE_TE_KEY_PREFIX = "native/llm-te/"
_SENTINEL = object()
_STOP_WAIT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_NEW_TOKENS = 4096


class NativeOutOfMemory(RuntimeError):
    pass


@dataclass
class RunStats:
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    finish: Optional[str] = None
    thinking_mode: Optional[Dict[str, Any]] = None


class _CheckpointGate:

    __slots__ = ("lock", "refcount")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.refcount = 0


_EXECUTION_GATES: Dict[tuple, "_CheckpointGate"] = {}


def _acquire_execution_gate(models: "ModelLifecycle", key: str) -> tuple[tuple, "_CheckpointGate"]:
    identity = (id(models), key)
    gate = _EXECUTION_GATES.get(identity)
    if gate is None:
        gate = _CheckpointGate()
        _EXECUTION_GATES[identity] = gate
    gate.refcount += 1
    return identity, gate


def _release_execution_gate(identity: tuple, gate: "_CheckpointGate") -> None:
    gate.refcount -= 1
    if gate.refcount <= 0:
        _EXECUTION_GATES.pop(identity, None)


class _LeaseHandoff:

    __slots__ = ("teardown", "taken_over", "_registry")

    def __init__(self, registry: "set[asyncio.Task]") -> None:
        self.teardown: Optional[Callable[[], Any]] = None
        self.taken_over = False
        self._registry = registry

    def defer(self, worker_done: threading.Event, model_name: str, key: str) -> None:
        self.taken_over = True
        task = asyncio.create_task(_supervised_teardown(self.teardown, worker_done, model_name, key))
        self._registry.add(task)
        task.add_done_callback(self._registry.discard)


async def _supervised_teardown(
    teardown: Callable[[], Any], worker_done: threading.Event, model_name: str, key: str
) -> None:
    await asyncio.to_thread(worker_done.wait)
    await teardown()
    logger.info(
        "[NativeLLM] supervised cleanup for '%s' (%s) completed now that the "
        "streaming worker exited", model_name, key,
    )


_EXECUTORS_BY_LOOP: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, concurrent.futures.ThreadPoolExecutor]" = weakref.WeakKeyDictionary()


def _executor_for_loop(loop: asyncio.AbstractEventLoop) -> concurrent.futures.ThreadPoolExecutor:
    executor = _EXECUTORS_BY_LOOP.get(loop)
    if executor is None:
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(32, (os.cpu_count() or 1) + 4),
            thread_name_prefix="native-llm",
        )
        _EXECUTORS_BY_LOOP[loop] = executor
        weakref.finalize(loop, executor.shutdown, wait=False)
    return executor


def _submit_cancellable(fn: Callable[[], Any]) -> "tuple[asyncio.Future, Any, threading.Event, list]":
    loop = asyncio.get_running_loop()
    executor = _executor_for_loop(loop)

    done = threading.Event()
    outcome: list = []
    ctx = contextvars.copy_context()

    def _wrapped():
        try:
            value = ctx.run(fn)
        except BaseException as e:
            outcome.append(("error", e))
            done.set()
            raise
        outcome.append(("ok", value))
        done.set()
        return value

    raw_future = executor.submit(_wrapped)
    async_future = asyncio.wrap_future(raw_future, loop=loop)
    return async_future, raw_future, done, outcome


def _is_oom(error: BaseException) -> bool:
    from src.platform.runtime.native.errors import retry_after_cuda_oom

    return retry_after_cuda_oom(error) or (isinstance(error, RuntimeError) and "out of memory" in str(error).lower())


@dataclass
class _LoadedCheckpoint:
    model: Any
    tokenizer: Any
    vision: bool
    model_type: str
    quantized: bool = False
    residency_handle: Any = None


class _NativeLLMResidencyHandle:

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


_UNBOUNDED_MODEL_MAX_LENGTH = 10_000_000


def _read_json_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_checkpoint_context_window(directory: str) -> Optional[DiscoveredWindow]:
    config = _read_json_file(os.path.join(directory, "config.json"))
    for section in (config, config.get("text_config"), config.get("llm_config")):
        if isinstance(section, dict):
            tokens = positive_int(section.get("max_position_embeddings"))
            if tokens:
                return DiscoveredWindow(tokens=tokens, kind="trained")
    tokenizer_config = _read_json_file(os.path.join(directory, "tokenizer_config.json"))
    tokens = positive_int(tokenizer_config.get("model_max_length"))
    if tokens and tokens < _UNBOUNDED_MODEL_MAX_LENGTH:
        return DiscoveredWindow(tokens=tokens, kind="trained")
    return None


class NativeRuntime:
    def __init__(self, model_lifecycle: Optional[ModelLifecycle] = None, model_roots: Optional[ModelRootResolver] = None):
        self._model_lifecycle = model_lifecycle
        self._model_roots = model_roots
        self._checkpoint_refs: Dict[str, "weakref.ReferenceType[_LoadedCheckpoint]"] = {}
        self._supervised_teardowns: "set[asyncio.Task]" = set()


    def _models(self) -> ModelLifecycle:
        lifecycle = self._model_lifecycle or get_model_lifecycle()
        if lifecycle is None:
            raise ValueError(
                "Native LLM provider: no ModelLifecycle available yet "
                "(the app container hasn't finished composing)"
            )
        return lifecycle

    def _roots(self) -> ModelRootResolver:
        if self._model_roots is None:
            raise ValueError(
                "Native LLM provider: no ModelRootResolver available yet "
                "(the app container hasn't finished composing)"
            )
        return self._model_roots

    @staticmethod
    def _family(model_type: Optional[str]) -> tuple[bool, str]:
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
        mode = (config.provider_options or {}).get("quantization", "none")
        if mode not in NATIVE_LLM_QUANT_MODES:
            raise ValueError(
                f"Native LLM provider: unknown quantization mode {mode!r}. "
                f"Supported: {', '.join(NATIVE_LLM_QUANT_MODES)}"
            )
        return mode

    @staticmethod
    def _load_kwargs(config: LLMConfig) -> Dict[str, Any]:
        provider_opts = config.provider_options or {}
        kwargs: Dict[str, Any] = {"dtype": provider_opts.get("dtype", "auto")}
        revision = provider_opts.get("revision")
        if revision:
            kwargs["revision"] = revision
        quant_mode = NativeRuntime._quant_mode(config)
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
        import torch

        if not torch.cuda.is_available():
            raise ValueError(
                f"Native LLM provider: quantized loading ({quant_mode}) requires a CUDA "
                f"GPU, which isn't available on this host — use quantization 'none' instead"
            )
        try:
            import bitsandbytes
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
        import torch.nn as nn

        quant_config = NativeRuntime._bnb_config(quant_mode)
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
        model.to("cuda")
        return model

    def _build(self, path: str, load_kwargs: Dict[str, Any]) -> _LoadedCheckpoint:
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
        size = checkpoint_size_gb(path)
        if size is not None and quant_mode != "none":
            size *= NATIVE_LLM_QUANT_SIZE_FACTORS.get(quant_mode, 1.0)
        return size

    def _resolve_model(self, name: str) -> tuple[str, bool]:
        resolver = self._roots()
        if is_adopted_te_reference(name):
            return resolve_adopted_te_path(name, resolver), True
        return resolve_native_checkpoint_path(name, resolver), False

    def _build_adopted(self, path: str, quant_mode: str = "none") -> _LoadedCheckpoint:
        model, tokenizer, model_type = build_adopted_te(path, self._roots())
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

    def _remember_checkpoint(self, key: str, checkpoint: Any) -> None:
        try:
            self._checkpoint_refs[key] = weakref.ref(checkpoint)
        except TypeError:
            self._checkpoint_refs.pop(key, None)

    def _acquire(self, path: str, config: LLMConfig, is_te: bool = False) -> _LoadedCheckpoint:
        if is_te:
            quant_mode = self._quant_mode(config)
            size = file_size_gb(path)
            if size is not None and quant_mode != "none":
                size *= NATIVE_LLM_QUANT_SIZE_FACTORS.get(quant_mode, 1.0)
            checkpoint = self._models().acquire(
                key=self._cache_key(path, True),
                fingerprint=f"te|{path}|bf16|quant={quant_mode}",
                loader=lambda: self._build_adopted(path, quant_mode),
                estimated_vram_gb=size,
            )
            self._remember_checkpoint(self._cache_key(path, True), checkpoint)
            return checkpoint
        load_kwargs = self._load_kwargs(config)
        quant_mode = load_kwargs.get("quantization", "none")
        checkpoint = self._models().acquire(
            key=self._cache_key(path, False),
            fingerprint=self._fingerprint(path, load_kwargs),
            loader=lambda: self._build(path, load_kwargs),
            estimated_vram_gb=self._estimated_size_gb(path, quant_mode),
        )
        self._remember_checkpoint(self._cache_key(path, False), checkpoint)
        return checkpoint

    def _warm_checkpoint(self, config: LLMConfig) -> Optional[_LoadedCheckpoint]:
        try:
            path, is_te = self._resolve_model(config.model)
        except Exception:
            return None
        ref = self._checkpoint_refs.get(self._cache_key(path, is_te))
        return ref() if ref is not None else None

    def probe(self, model: str) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        try:
            path, is_te = self._resolve_model(model)
        except Exception:
            return fields
        if is_te or not os.path.isdir(path):
            return fields
        window = read_checkpoint_context_window(path)
        if window:
            fields["context_window"] = window.tokens
        model_type = _read_json_file(os.path.join(path, "config.json")).get("model_type")
        if model_type in VISION_MODEL_TYPES:
            fields["vision"] = True
        elif model_type in TEXT_MODEL_TYPES:
            fields["vision"] = False
        return fields

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        return [
            {"name": entry.name, "model_type": entry.model_type, "vision": entry.vision}
            for entry in list_native_checkpoints(self._roots())
            if entry.supported
        ]

    def token_counter(self, config: LLMConfig) -> Optional[TokenCounter]:
        checkpoint = self._warm_checkpoint(config)
        if checkpoint is None:
            return None
        tokenizer = checkpoint.tokenizer

        def _count(text: str) -> int:
            encoded = tokenizer(text, return_tensors=None)
            ids = encoded["input_ids"] if isinstance(encoded, dict) else encoded.input_ids
            if ids and isinstance(ids[0], list):
                ids = ids[0]
            return len(ids)

        return _count

    def prompt_token_counter(self, config: LLMConfig, enable_thinking: Optional[bool]) -> Optional[Callable[[List[Dict[str, Any]]], int]]:
        checkpoint = self._warm_checkpoint(config)
        if checkpoint is None:
            return None
        tokenizer = checkpoint.tokenizer
        template_kwargs, _meta = self._chat_template_kwargs(checkpoint, enable_thinking)

        def _count(messages: List[Dict[str, Any]]) -> int:
            chat, _image = self._build_chat(messages, with_image=False)
            prompt_text = tokenizer.apply_chat_template(
                chat, add_generation_prompt=True, tokenize=False, **template_kwargs
            )
            encoded = tokenizer(prompt_text, return_tensors=None)
            ids = encoded["input_ids"] if isinstance(encoded, dict) else encoded.input_ids
            if ids and isinstance(ids[0], list):
                ids = ids[0]
            return len(ids)

        return _count

    async def release(self, config: LLMConfig) -> bool:
        return False

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
        import torch

        models = self._models()
        key = self._cache_key(path, is_te)
        gate_id, gate = _acquire_execution_gate(models, key)
        try:
            await gate.lock.acquire()
        except BaseException:
            _release_execution_gate(gate_id, gate)
            raise

        models.begin_generation(None)
        lease_id = f"native-llm-{uuid.uuid4().hex}"
        models.begin_lease(lease_id)
        checkpoint: Optional[_LoadedCheckpoint] = None
        device = "cpu"
        manage_device = False
        handoff = _LeaseHandoff(self._supervised_teardowns)

        async def _teardown() -> None:
            if checkpoint is not None and manage_device:
                offload_future, offload_raw, offload_done, offload_outcome = _submit_cancellable(
                    lambda: checkpoint.model.to("cpu")
                )
                try:
                    await offload_future
                except asyncio.CancelledError:
                    if offload_raw.cancel():
                        pass
                    else:
                        async def _finish_after_late_offload() -> None:
                            await asyncio.to_thread(offload_done.wait)
                            outcome, _value = offload_outcome[0]
                            if outcome == "ok":
                                torch.cuda.empty_cache()
                                self._note_offloaded(checkpoint)
                            else:
                                logger.warning(
                                    "[NativeLLM] failed to move checkpoint back to CPU "
                                    "after lease; evicting the cache entry key='%s' so "
                                    "it can't be left GPU-resident",
                                    key, exc_info=offload_outcome[0][1],
                                )
                                models.invalidate(key)
                            models.end_lease(lease_id)
                            gate.lock.release()
                            _release_execution_gate(gate_id, gate)

                        task = asyncio.create_task(_finish_after_late_offload())
                        self._supervised_teardowns.add(task)
                        task.add_done_callback(self._supervised_teardowns.discard)
                        logger.warning(
                            "[NativeLLM] offloading '%s' (%s) back to CPU was cancelled "
                            "while the move was still running; the lease and execution "
                            "gate stay held until a supervised cleanup observes it "
                            "actually finish",
                            config.model, key,
                        )
                        return
                except Exception:
                    logger.warning(
                        "[NativeLLM] failed to move checkpoint back to CPU after lease; "
                        "evicting the cache entry key='%s' so it can't be left GPU-resident",
                        key, exc_info=True,
                    )
                    models.invalidate(key)
                else:
                    torch.cuda.empty_cache()
                    self._note_offloaded(checkpoint)
            models.end_lease(lease_id)
            gate.lock.release()
            _release_execution_gate(gate_id, gate)

        handoff.teardown = _teardown
        try:
            acquire_future, acquire_raw, acquire_done, acquire_outcome = _submit_cancellable(
                lambda: self._acquire(path, config, is_te)
            )
            try:
                checkpoint = await acquire_future
            except asyncio.CancelledError:
                if not acquire_raw.cancel():
                    async def _finish_after_late_acquire() -> None:
                        nonlocal checkpoint
                        await asyncio.to_thread(acquire_done.wait)
                        outcome, value = acquire_outcome[0]
                        if outcome == "ok":
                            checkpoint = value
                        await _teardown()

                    handoff.taken_over = True
                    task = asyncio.create_task(_finish_after_late_acquire())
                    self._supervised_teardowns.add(task)
                    task.add_done_callback(self._supervised_teardowns.discard)
                    logger.warning(
                        "[NativeLLM] acquiring '%s' (%s) was cancelled while the loader "
                        "was still running; handing this turn's teardown to a "
                        "supervised cleanup instead of ending the lease before the "
                        "checkpoint is ever accounted for",
                        config.model, key,
                    )
                raise
            device = "cuda" if torch.cuda.is_available() else "cpu"
            manage_device = device != "cpu" and not checkpoint.quantized
            if manage_device:
                place_future, place_raw, place_done, place_outcome = _submit_cancellable(
                    lambda: checkpoint.model.to(device)
                )
                try:
                    await place_future
                except asyncio.CancelledError:
                    if place_raw.cancel():
                        manage_device = False
                    else:
                        async def _finish_after_late_placement() -> None:
                            nonlocal manage_device
                            await asyncio.to_thread(place_done.wait)
                            outcome, _value = place_outcome[0]
                            if outcome == "ok":
                                self._note_resident(checkpoint, key, device)
                            else:
                                manage_device = False
                            await _teardown()

                        handoff.taken_over = True
                        task = asyncio.create_task(_finish_after_late_placement())
                        self._supervised_teardowns.add(task)
                        task.add_done_callback(self._supervised_teardowns.discard)
                        logger.warning(
                            "[NativeLLM] placing '%s' (%s) on %s was cancelled while the "
                            "move was still running; handing this turn's teardown to a "
                            "supervised cleanup instead of moving it back before that "
                            "move actually lands",
                            config.model, key, device,
                        )
                    raise
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
                    self._note_resident(checkpoint, key, device)
            yield checkpoint, device, handoff
        finally:
            if not handoff.taken_over:
                await _teardown()


    @staticmethod
    def _decode_image(image_data: str):
        from PIL import Image

        try:
            raw = base64.b64decode(image_data)
            return Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as e:
            raise ValueError(f"Native LLM provider: could not decode the attached image: {e}") from e

    @staticmethod
    def _selected_chat_template(tokenizer: Any) -> Optional[str]:
        template = getattr(tokenizer, "chat_template", None)
        if isinstance(template, str):
            return template
        if isinstance(template, dict):
            default = template.get("default")
            return default if isinstance(default, str) else None
        return None

    @staticmethod
    def _jinja_parse_environment():
        try:
            from transformers.utils.chat_template_utils import _compile_jinja_template

            return _compile_jinja_template("").environment
        except Exception:
            pass
        import jinja2.ext
        from jinja2.sandbox import ImmutableSandboxedEnvironment

        return ImmutableSandboxedEnvironment(
            trim_blocks=True, lstrip_blocks=True, extensions=[jinja2.ext.loopcontrols],
        )

    @staticmethod
    def _supports_thinking(tokenizer: Any) -> bool:
        template = NativeRuntime._selected_chat_template(tokenizer)
        if template is None:
            return False
        try:
            import jinja2.meta

            env = NativeRuntime._jinja_parse_environment()
            ast = env.parse(template)
            undeclared = jinja2.meta.find_undeclared_variables(ast)
        except Exception:
            return False
        return "enable_thinking" in undeclared

    @staticmethod
    def _chat_template_kwargs(checkpoint: "_LoadedCheckpoint", requested: Optional[bool]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if requested is None:
            return {}, {"requested": None, "effective": "template_default"}
        if not NativeRuntime._supports_thinking(checkpoint.tokenizer):
            return {}, {"requested": requested, "effective": "unsupported"}
        return {"enable_thinking": requested}, {"requested": requested, "effective": requested}

    def _build_chat(self, messages: List[Dict[str, Any]], with_image: bool = True) -> tuple[list, Any]:
        chat: list = []
        image = None
        for message in messages:
            entry = {"role": message["role"], "content": message["content"]}
            images = message.get("images")
            if with_image and images and image is None:
                image = self._decode_image(images[0])
                entry["content"] = [{"type": "image"}, {"type": "text", "text": message["content"]}]
            chat.append(entry)
        return chat, image

    @staticmethod
    def _apply_template(checkpoint: _LoadedCheckpoint, chat: list, image: Any, template_kwargs: Dict[str, Any]) -> Dict[str, Any]:
        prompt_text = checkpoint.tokenizer.apply_chat_template(
            chat, add_generation_prompt=True, tokenize=False, **template_kwargs
        )
        if checkpoint.vision:
            return dict(checkpoint.tokenizer(text=[prompt_text], images=[image] if image is not None else None, return_tensors="pt"))
        return dict(checkpoint.tokenizer(prompt_text, return_tensors="pt"))

    @staticmethod
    def _generation_kwargs(sampling: Mapping[str, Any]) -> Dict[str, Any]:
        temperature = sampling.get("temperature")
        top_p = sampling.get("top_p")
        top_k = sampling.get("top_k")
        min_p = sampling.get("min_p")
        repetition_penalty = sampling.get("repetition_penalty")
        max_new_tokens = sampling.get("max_new_tokens")

        kwargs: Dict[str, Any] = {"max_new_tokens": int(max_new_tokens) if max_new_tokens is not None else DEFAULT_MAX_NEW_TOKENS}
        if temperature is None:
            sampled = None
        else:
            sampled = float(temperature) > 0.0
            kwargs["do_sample"] = sampled
            if sampled:
                kwargs["temperature"] = float(temperature)
        if sampled is not False:
            if top_p is not None:
                kwargs["top_p"] = float(top_p)
            if top_k is not None:
                kwargs["top_k"] = int(top_k)
            if min_p is not None:
                kwargs["min_p"] = float(min_p)
        if repetition_penalty is not None:
            kwargs["repetition_penalty"] = float(repetition_penalty)
        return kwargs

    @staticmethod
    def _eos_token_ids(checkpoint: "_LoadedCheckpoint") -> List[int]:
        generation_config = getattr(checkpoint.model, "generation_config", None)
        eos = getattr(generation_config, "eos_token_id", None) if generation_config is not None else None
        if eos is None:
            return []
        if isinstance(eos, int):
            return [eos]
        return list(eos)

    @staticmethod
    def _finish_reason(last_token_id: Optional[int], completion_tokens: int, max_new_tokens: int, eos_ids: List[int]) -> Optional[str]:
        if last_token_id is not None and last_token_id in eos_ids:
            return "stop"
        if completion_tokens >= max_new_tokens:
            return "length"
        return None

    async def stream(
        self,
        config: LLMConfig,
        messages: List[Dict[str, Any]],
        sampling: Mapping[str, Any],
        enable_thinking: Optional[bool],
        stats: RunStats,
    ) -> AsyncIterator[str]:
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer

        path, is_te = self._resolve_model(config.model)
        key = self._cache_key(path, is_te)
        async with self._leased(path, config, is_te) as (checkpoint, device, handoff):
            if not checkpoint.vision and any(message.get("images") for message in messages):
                raise ValueError(
                    f"Native LLM provider: '{config.model}' ({checkpoint.model_type}) has no "
                    f"vision support, but an image was attached to this turn"
                )
            chat, image = self._build_chat(messages)
            gen_kwargs = self._generation_kwargs(sampling)
            template_kwargs, stats.thinking_mode = self._chat_template_kwargs(checkpoint, enable_thinking)

            inputs = await asyncio.to_thread(self._apply_template, checkpoint, chat, image, template_kwargs)
            inputs = {k: v.to(device) for k, v in inputs.items() if hasattr(v, "to")}
            prompt_tokens = int(inputs["input_ids"].shape[-1])
            stats.prompt_tokens = prompt_tokens

            streamer = TextIteratorStreamer(checkpoint.tokenizer, skip_prompt=True, skip_special_tokens=True)
            errors: List[BaseException] = []
            completion_tokens_box = [0]
            last_token_box: List[Optional[int]] = [None]
            eos_ids = self._eos_token_ids(checkpoint)
            worker_done = threading.Event()
            stop_requested = threading.Event()

            class _StopRequested(StoppingCriteria):
                def __call__(self, *_args, **_kwargs) -> bool:
                    return stop_requested.is_set()

            stopping_criteria = StoppingCriteriaList([_StopRequested()])

            def _run():
                try:
                    with torch.no_grad():
                        output_ids = checkpoint.model.generate(
                            **inputs, streamer=streamer, stopping_criteria=stopping_criteria, **gen_kwargs
                        )
                    completion_tokens_box[0] = int(output_ids.shape[-1]) - prompt_tokens
                    if output_ids.shape[-1] > prompt_tokens:
                        try:
                            last_token_box[0] = int(output_ids[0, -1].item())
                        except (AttributeError, TypeError, IndexError):
                            last_token_box[0] = None
                except BaseException as e:
                    errors.append(e)
                finally:
                    streamer.end()
                    worker_done.set()

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()

            try:
                stream_iter = iter(streamer)
                while True:
                    text = await asyncio.to_thread(next, stream_iter, _SENTINEL)
                    if text is _SENTINEL:
                        break
                    if text:
                        yield text
            finally:
                if not worker_done.is_set():
                    stop_requested.set()
                    try:
                        finished = await asyncio.to_thread(worker_done.wait, _STOP_WAIT_TIMEOUT_SECONDS)
                    except BaseException:
                        handoff.defer(worker_done, config.model, key)
                        raise
                    if not finished:
                        logger.warning(
                            "[NativeLLM] streaming worker for '%s' (%s) did not exit within "
                            "%.1fs of a requested stop; handing this turn's teardown to a "
                            "supervised cleanup instead of releasing the checkpoint out "
                            "from under it",
                            config.model, key, _STOP_WAIT_TIMEOUT_SECONDS,
                        )
                        handoff.defer(worker_done, config.model, key)

            if errors:
                error = errors[0]
                if _is_oom(error):
                    torch.cuda.empty_cache()
                    raise NativeOutOfMemory(
                        f"Native LLM provider: ran out of GPU memory generating with '{config.model}'"
                    ) from error
                raise error

            stats.completion_tokens = completion_tokens_box[0]
            stats.finish = self._finish_reason(
                last_token_box[0], stats.completion_tokens, gen_kwargs["max_new_tokens"], eos_ids
            )
