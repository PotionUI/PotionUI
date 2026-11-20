from __future__ import annotations

import logging

import torch

from ...text_encoders._functional import module_device
from .lm import GlobalLMKVCache, MiniMaxMusic3AudioLM

logger = logging.getLogger(__name__)

_WARMUP_STEPS = 3
_warned_capture_failed = False


class StaticLmStep:
    def __init__(self, lm: MiniMaxMusic3AudioLM, cache: GlobalLMKVCache, *, capture: bool) -> None:
        device = module_device(lm)
        self._lm = lm
        self._cache = cache
        self._capture = capture and device.type == "cuda"
        self._pos = torch.zeros(1, dtype=torch.long, device=device)
        self._positions = torch.arange(cache.max_len, device=device)
        self._graph: torch.cuda.CUDAGraph | None = None
        self._inputs: torch.Tensor | None = None
        self._output: torch.Tensor | None = None
        self._capture_failed = False

    @property
    def graphed(self) -> bool:
        return self._graph is not None

    def __enter__(self) -> "StaticLmStep":
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False

    def close(self) -> None:
        self._graph = None
        self._inputs = None
        self._output = None

    def _run(self, inputs_embeds: torch.Tensor) -> torch.Tensor:
        mask = torch.zeros(1, 1, 1, self._cache.max_len, dtype=inputs_embeds.dtype, device=inputs_embeds.device)
        mask = mask.masked_fill(self._positions > self._pos, float("-inf"))
        return self._lm.step_static(inputs_embeds, self._cache, self._pos, mask)

    def _capture_graph(self, inputs_embeds: torch.Tensor) -> None:
        self._inputs = inputs_embeds.detach().clone()
        side = torch.cuda.Stream()
        side.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(side):
            for _ in range(_WARMUP_STEPS):
                self._run(self._inputs)
        torch.cuda.current_stream().wait_stream(side)
        torch.cuda.synchronize()
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph, capture_error_mode="thread_local"):
            self._output = self._run(self._inputs)
        self._graph = graph

    def _try_capture(self, inputs_embeds: torch.Tensor) -> None:
        global _warned_capture_failed
        try:
            self._capture_graph(inputs_embeds)
        except Exception as exc:  # noqa: BLE001 - capture is an optimisation; never fatal
            self._capture_failed = True
            self._graph = None
            self._inputs = None
            self._output = None
            if not _warned_capture_failed:
                _warned_capture_failed = True
                logger.warning(
                    "MiniMax-Music3: CUDA-graph capture of the LM step failed (%s: %s); running the LM step eagerly",
                    type(exc).__name__, exc,
                )

    def __call__(self, inputs_embeds: torch.Tensor) -> torch.Tensor:
        inputs_embeds = inputs_embeds.contiguous()
        pos = self._cache.filled_len
        if pos >= self._cache.max_len:
            raise RuntimeError(f"MiniMax-Music3 KV cache is full ({pos}/{self._cache.max_len} positions)")
        if self._capture and self._graph is None and not self._capture_failed:
            self._pos.fill_(pos)
            self._try_capture(inputs_embeds)
        if self._graph is None:
            return self._lm.step(inputs_embeds, self._cache)
        self._pos.fill_(pos)
        self._inputs.copy_(inputs_embeds)
        self._graph.replay()
        out = self._output.clone()
        self._cache.filled_len = pos + 1
        return out
