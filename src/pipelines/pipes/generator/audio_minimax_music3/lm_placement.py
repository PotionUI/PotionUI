from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import torch

from src.platform.runtime.native.engine import TextEncoderStreaming
from src.platform.runtime.native.errors import retry_after_cuda_oom
from src.platform.runtime.native.memory.residency import (
    free_vram_gb,
    get_residency_registry,
    minimum_inference_memory_gb,
)
from src.platform.runtime.native.memory.text_encoder import module_weights_bytes

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1024 ** 3
_KV_BATCH = 2
_KV_DTYPE_BYTES = 2


def kv_cache_gb(cfg: Any, max_len: int) -> float:
    per_layer = 2 * _KV_BATCH * int(cfg.num_key_value_heads) * int(max_len) * int(cfg.head_dim) * _KV_DTYPE_BYTES
    return per_layer * int(cfg.num_layers) / _BYTES_PER_GB


@dataclass(frozen=True)
class LMPlacementPlan:
    stream: bool
    need_gb: float
    available_gb: Optional[float]
    layers_budget_gb: float


def plan_lm_placement(
    *,
    total_gb: float,
    layers_gb: float,
    kv_gb: float,
    reserve_gb: float,
    free_gb: Optional[float],
    budget_gb: Optional[float],
) -> LMPlacementPlan:
    need = total_gb + kv_gb + reserve_gb
    candidates = [value for value in (free_gb, budget_gb) if value is not None]
    if not candidates:
        return LMPlacementPlan(stream=False, need_gb=need, available_gb=None, layers_budget_gb=layers_gb)
    available = min(candidates)
    if need <= available:
        return LMPlacementPlan(stream=False, need_gb=need, available_gb=available, layers_budget_gb=layers_gb)
    fixed_gb = max(0.0, total_gb - layers_gb)
    layers_budget = max(0.0, available - fixed_gb - kv_gb - reserve_gb)
    return LMPlacementPlan(stream=True, need_gb=need, available_gb=available, layers_budget_gb=layers_budget)


class PlacedLM:
    def __init__(self, lm_model: Any, streaming: Optional[TextEncoderStreaming], plan: Optional[LMPlacementPlan]) -> None:
        self.lm_model = lm_model
        self.streaming = streaming
        self.plan = plan

    @property
    def resident(self) -> bool:
        return self.streaming is None

    def release(self) -> None:
        if self.streaming is None:
            self.lm_model.offload()
            return
        streaming, self.streaming = self.streaming, None
        streaming.teardown()
        self.lm_model.device = "cpu"


def _layers_of(module: Any) -> Optional[torch.nn.Module]:
    inner = getattr(module, "model", None)
    layers = getattr(inner, "layers", None)
    return layers if isinstance(layers, torch.nn.Module) else None


class OutsideLayers:
    def __init__(self, module: torch.nn.Module) -> None:
        self.module = module
        self.owner = module.model

    def to(self, *args: Any, **kwargs: Any) -> "OutsideLayers":
        saved = self.owner._modules
        object.__setattr__(self.owner, "_modules", {k: v for k, v in saved.items() if k != "layers"})
        try:
            self.module.to(*args, **kwargs)
        finally:
            object.__setattr__(self.owner, "_modules", saved)
        return self


def place_lm(
    lm_model: Any,
    device: str,
    *,
    budget_gb: Optional[float],
    max_len: int,
    own_models: Iterable[Any] = (),
) -> PlacedLM:
    module = lm_model.module
    layers = _layers_of(module)
    if not str(device).startswith("cuda") or layers is None:
        lm_model.move_to(device)
        return PlacedLM(lm_model, None, None)

    total_gb = module_weights_bytes(module) / _BYTES_PER_GB
    layers_gb = module_weights_bytes(layers) / _BYTES_PER_GB
    kv_gb = kv_cache_gb(module.cfg, max_len)
    reserve_gb = minimum_inference_memory_gb()
    exclude = [lm_model, *[model for model in own_models if model is not None]]
    registry = get_residency_registry()

    free = free_vram_gb(device)
    need = total_gb + kv_gb + reserve_gb
    if free is not None and need > free:
        registry.ensure_free(device, need, free, exclude=exclude)
        free = free_vram_gb(device)

    plan = plan_lm_placement(
        total_gb=total_gb, layers_gb=layers_gb, kv_gb=kv_gb, reserve_gb=reserve_gb,
        free_gb=free, budget_gb=budget_gb,
    )
    if not plan.stream:
        lm_model.move_to(device)
        return PlacedLM(lm_model, None, plan)

    logger.info(
        "[GENERATOR MINIMAX-MUSIC3] AR core needs %.1fGB but %.1fGB is available; "
        "streaming its LLM layers with a %.1fGB resident budget",
        plan.need_gb, plan.available_gb, plan.layers_budget_gb,
    )
    streaming = TextEncoderStreaming(OutsideLayers(module), modules=[layers])
    try:
        streaming.apply(device, plan.layers_budget_gb)
    except RuntimeError as exc:
        if not retry_after_cuda_oom(exc):
            raise
        logger.warning(
            "[GENERATOR MINIMAX-MUSIC3] AR core placement OOM'd (layers budget %.1fGB); "
            "evicting foreign residents and streaming every layer", plan.layers_budget_gb,
        )
        streaming.teardown()
        registry.offload_all(device, exclude=exclude)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        streaming.apply(device, 0.0)
    lm_model.device = str(device)
    return PlacedLM(lm_model, streaming, plan)
