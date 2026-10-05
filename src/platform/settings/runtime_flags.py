from __future__ import annotations

import importlib
import logging
import math
import os
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Literal, Mapping, Optional

logger = logging.getLogger(__name__)

FlagKind = Literal["bool", "choice", "float", "int", "string"]
FlagApplies = Literal["live", "next_load", "restart"]
FlagGroup = Literal["speed", "memory", "debug", "diagnostics"]
FlagScope = Literal["backend", "app"]

_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "auto"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", ""})

_VALUE_TYPES = {"bool": "boolean", "choice": "string", "string": "string", "float": "float", "int": "integer"}

BACKEND_LOCATION = "Admin -> Backends -> <backend> -> Optimizations"
APP_LOCATION = "Admin -> System Settings -> Diagnostics"


@dataclass(frozen=True)
class RuntimeFlag:
    key: str
    env_var: Optional[str]
    kind: FlagKind
    default: Any
    applies: FlagApplies
    group: FlagGroup
    scope: FlagScope
    description: str
    choices: tuple[str, ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None

    @property
    def value_type(self) -> str:
        return _VALUE_TYPES[self.kind]

    @property
    def location(self) -> str:
        return BACKEND_LOCATION if self.scope == "backend" else APP_LOCATION


RUNTIME_FLAGS: tuple[RuntimeFlag, ...] = (
    RuntimeFlag(
        "native_fp8_matmul", "NATIVE_FP8_MATMUL", "bool", False, "live", "speed", "backend",
        "Multiply fp8 checkpoints directly on fp8 tensor cores (RTX 40/50 and newer).",
    ),
    RuntimeFlag(
        "native_nvfp4_matmul", "NATIVE_NVFP4_MATMUL", "bool", False, "live", "speed", "backend",
        "Multiply nvfp4 checkpoints directly on fp4 tensor cores (RTX 50 and newer).",
    ),
    RuntimeFlag(
        "native_lora_fused", "NATIVE_LORA_FUSED", "bool", True, "live", "speed", "backend",
        "Fold LoRA weights into the base weight once per run instead of adding them on every step.",
    ),
    RuntimeFlag(
        "native_torch_compile", "NATIVE_TORCH_COMPILE", "bool", False, "next_load", "speed", "backend",
        "Compile the diffusion model's blocks with torch.compile when it is fully on the GPU.",
    ),
    RuntimeFlag(
        "native_qwen3_te_bf16", "NATIVE_QWEN3_TE_BF16", "bool", False, "live", "speed", "backend",
        "Run the Qwen3 text encoder in bf16 instead of fp32.",
    ),
    RuntimeFlag(
        "native_attention_backend", "NATIVE_ATTENTION", "string", "", "live", "speed", "backend",
        "Pinned attention backend (empty = pick the fastest installed one).",
    ),
    RuntimeFlag(
        "native_sol_attn_backend", "NATIVE_SOL_ATTN_BACKEND", "choice", "flex", "restart", "speed", "backend",
        "Which Sol-Attn implementation to use: flex (torch flex attention) or kernel (compiled kernel).",
        choices=("flex", "kernel"),
    ),
    RuntimeFlag(
        "native_stream_prefetch", "NATIVE_STREAM_PREFETCH", "bool", False, "live", "memory", "backend",
        "Copy the next streamed layer to the GPU while the current one computes.",
    ),
    RuntimeFlag(
        "native_fp8_quantize", "NATIVE_FP8_QUANTIZE", "choice", "auto", "next_load", "memory", "backend",
        "Quantize a bf16 diffusion model to fp8 at load: auto (only when that makes it fit), off, or force.",
        choices=("auto", "off", "force"),
    ),
    RuntimeFlag(
        "native_min_inference_memory_gb", "NATIVE_MIN_INFERENCE_MEMORY_GB", "float", 1.0, "live", "memory",
        "backend", "VRAM in GB kept free on top of the model weights for the sampling step itself.",
        minimum=0.0, maximum=64.0,
    ),
    RuntimeFlag(
        "native_ltx_decode_tile_px", "NATIVE_LTX_DIFFUSION_TILE_PX", "int", 0, "live", "memory", "backend",
        "Fixed LTX video decode tile size in pixels. 0 picks it from free VRAM.",
        minimum=0, maximum=4096,
    ),
    RuntimeFlag(
        "native_ltx_decode_tile_frames", "NATIVE_LTX_DIFFUSION_TILE_FRAMES", "int", 0, "live", "memory", "backend",
        "Fixed LTX video decode tile length in frames. 0 picks it from free VRAM.",
        minimum=0, maximum=1024,
    ),
    RuntimeFlag(
        "native_sol_attn_debug", "NATIVE_SOL_ATTN_DEBUG", "bool", False, "live", "debug", "backend",
        "Log the shape and routing of every Sol-Attn call.",
    ),
    RuntimeFlag(
        "profiling_enabled", "POTIONUI_PROFILE", "bool", False, "live", "diagnostics", "app",
        "Record a performance profile (stage timings, RAM and VRAM over time, model events) for each generation.",
    ),
    RuntimeFlag(
        "profiling_census", None, "bool", True, "live", "diagnostics", "app",
        "Add a census of every live tensor to each profile, written after the generation finishes.",
    ),
)

RUNTIME_FLAG_BY_KEY: dict[str, RuntimeFlag] = {flag.key: flag for flag in RUNTIME_FLAGS}
ENGINE_FLAGS: tuple[RuntimeFlag, ...] = tuple(flag for flag in RUNTIME_FLAGS if flag.scope == "backend")
APP_FLAGS: tuple[RuntimeFlag, ...] = tuple(flag for flag in RUNTIME_FLAGS if flag.scope == "app")
ENGINE_FLAG_KEYS = frozenset(flag.key for flag in ENGINE_FLAGS)
APP_FLAG_KEYS = frozenset(flag.key for flag in APP_FLAGS)

_VENDOR_OPS = "vendor.gpl.comfyui.ops"

_values: dict[str, Any] = {flag.key: flag.default for flag in RUNTIME_FLAGS}
_settings: Any = None
_warned_env: set[str] = set()
_vendor_bound = False


def runtime_flag(key: str) -> Any:
    return _values[key]


def runtime_flag_values() -> dict[str, Any]:
    return _values


def coerce_runtime_flag(key: str, value: Any) -> Any:
    flag = RUNTIME_FLAG_BY_KEY[key]
    if flag.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        word = str(value).strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
        raise ValueError(f"{key} must be on or off, got {value!r}")
    if flag.kind == "string":
        if value is None:
            return ""
        word = str(value).strip().lower()
        return "" if word == "auto" else word
    if flag.kind == "choice":
        word = str(value).strip().lower()
        if word not in flag.choices:
            raise ValueError(f"{key} must be one of {', '.join(flag.choices)}, got {value!r}")
        return word
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number, got {value!r}")
    try:
        number = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a finite number, got {value!r}")
    if flag.minimum is not None and number < flag.minimum:
        raise ValueError(f"{key} must be at least {flag.minimum:g}, got {value!r}")
    if flag.maximum is not None and number > flag.maximum:
        raise ValueError(f"{key} must be at most {flag.maximum:g}, got {value!r}")
    if flag.kind == "int":
        if number != int(number):
            raise ValueError(f"{key} must be a whole number, got {value!r}")
        return int(number)
    return number


def validate_runtime_flag(key: str, value: Any) -> Optional[str]:
    if key not in APP_FLAG_KEYS:
        return None
    try:
        coerce_runtime_flag(key, value)
    except ValueError as exc:
        return str(exc)
    return None


def normalize_engine_flags(values: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        if key not in ENGINE_FLAG_KEYS:
            raise ValueError(f"unknown engine setting {key!r}")
        normalized[key] = coerce_runtime_flag(key, value)
    return normalized


def sanitize_engine_flags(stored: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (stored or {}).items():
        if key not in ENGINE_FLAG_KEYS:
            logger.warning("engine setting %r is unknown; dropping it", key)
            continue
        try:
            clean[key] = coerce_runtime_flag(key, value)
        except ValueError:
            logger.warning("engine setting %s: stored value %r is invalid; dropping it", key, value)
    return clean


def engine_flag_values(stored: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    resolved = {flag.key: flag.default for flag in ENGINE_FLAGS}
    for key, value in (stored or {}).items():
        if key not in ENGINE_FLAG_KEYS:
            continue
        try:
            resolved[key] = coerce_runtime_flag(key, value)
        except ValueError:
            logger.warning("engine setting %s: stored value %r is invalid; using the default", key, value)
    return resolved


def activate_engine_flags(stored: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    resolved = engine_flag_values(stored)
    _values.update(resolved)
    bind_vendor_runtime_flags(import_if_missing=False)
    return resolved


def apply_saved_setting(key: str, value: Any) -> None:
    if key not in APP_FLAG_KEYS:
        return
    try:
        _values[key] = coerce_runtime_flag(key, value)
    except ValueError:
        logger.warning("runtime setting %s: stored value %r is invalid; using the default", key, value)
        _values[key] = RUNTIME_FLAG_BY_KEY[key].default


def apply_saved_settings(values: Mapping[str, Any]) -> None:
    for key, value in values.items():
        apply_saved_setting(key, value)


def reload_runtime_flags() -> None:
    if _settings is None:
        return
    for flag in APP_FLAGS:
        try:
            stored = _settings.get_setting(flag.key, None)
        except Exception:
            logger.warning("runtime setting %s: could not be read; keeping %r", flag.key, _values[flag.key], exc_info=True)
            continue
        if stored is None:
            _values[flag.key] = flag.default
        else:
            apply_saved_setting(flag.key, stored)


def configure_runtime_flags(settings: Any) -> None:
    global _settings
    _settings = settings
    reload_runtime_flags()
    bind_vendor_runtime_flags(import_if_missing=False)


def bind_runtime_flags(module: ModuleType) -> None:
    module.RUNTIME_FLAGS = _values


def bind_vendor_runtime_flags(import_if_missing: bool = True) -> None:
    global _vendor_bound
    if _vendor_bound:
        return
    ops = sys.modules.get(_VENDOR_OPS)
    if ops is None and import_if_missing:
        try:
            ops = importlib.import_module(_VENDOR_OPS)
        except Exception:
            logger.debug("runtime settings: vendor ops not importable; not bound", exc_info=True)
            return
    if ops is None:
        return
    bind_runtime_flags(ops)
    _vendor_bound = True


def reset_runtime_flags() -> None:
    for flag in RUNTIME_FLAGS:
        _values[flag.key] = flag.default


def seed_value_from_env(flag: RuntimeFlag, environ: Mapping[str, str]) -> Optional[Any]:
    if flag.env_var is None:
        return None
    raw = environ.get(flag.env_var)
    if raw is None or not raw.strip():
        return None
    try:
        value = coerce_runtime_flag(flag.key, raw)
    except ValueError:
        logger.warning("runtime setting %s: ignoring unusable %s=%r", flag.key, flag.env_var, raw)
        return None
    if flag.kind == "int" and value == 0:
        return None
    return value


def seed_engine_flags_from_env(environ: Mapping[str, str]) -> dict[str, Any]:
    seeded: dict[str, Any] = {}
    for flag in ENGINE_FLAGS:
        value = seed_value_from_env(flag, environ)
        if value is not None:
            seeded[flag.key] = value
    return seeded


def ignored_env_vars(environ: Optional[Mapping[str, str]] = None) -> list[RuntimeFlag]:
    env = os.environ if environ is None else environ
    return [flag for flag in RUNTIME_FLAGS if flag.env_var and flag.env_var in env]


def warn_ignored_env_vars(environ: Optional[Mapping[str, str]] = None) -> None:
    for flag in ignored_env_vars(environ):
        if flag.env_var in _warned_env:
            continue
        _warned_env.add(flag.env_var)
        logger.warning(
            "%s is set but ignored: it is now the '%s' setting in %s",
            flag.env_var, flag.key, flag.location,
        )
