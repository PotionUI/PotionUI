from __future__ import annotations

import torch
import torch.nn as nn

from src.platform.runtime.model_lifecycle import lifecycle
from src.platform.runtime.native.memory import partial

PIN_REFUSED = "CUDA error: out of memory"


class FakePinning:
    def __init__(self, monkeypatch, *, allow: int | None = 0) -> None:
        self.allow = allow
        self.attempts = 0
        self.pins = 0
        self.counted: list[int] = []
        self.pool_releases = 0
        self._held: dict[int, torch.Tensor] = {}
        fake = self

        def pin_memory(tensor, *args, **kwargs):
            fake.attempts += 1
            if fake.allow is not None and fake.pins >= fake.allow:
                raise RuntimeError(PIN_REFUSED)
            pinned = tensor.clone()
            fake.pins += 1
            fake._held[pinned.data_ptr()] = pinned
            return pinned

        def is_pinned(tensor, *args, **kwargs):
            ptr = tensor.data_ptr()
            return ptr != 0 and ptr in fake._held

        def release_pool():
            fake.pool_releases += 1

        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(torch.Tensor, "pin_memory", pin_memory)
        monkeypatch.setattr(torch.Tensor, "is_pinned", is_pinned)
        monkeypatch.setattr(partial, "add_pinned_bytes", self.counted.append)
        monkeypatch.setattr(lifecycle, "empty_pinned_host_cache", release_pool)
        monkeypatch.setattr(lifecycle, "trim_host_allocator", lambda: None)

    def pinned_in(self, *modules: nn.Module) -> int:
        total = 0
        for module in modules:
            for tensor in list(module.parameters()) + list(module.buffers()):
                if tensor.is_pinned():
                    total += 1
        return total


def hooks_in(*modules: nn.Module) -> int:
    return sum(
        len(m._forward_hooks) + len(m._forward_pre_hooks) for module in modules for m in module.modules()
    )


def streamed_flags_in(*modules: nn.Module) -> int:
    total = 0
    for module in modules:
        for m in module.modules():
            default = getattr(type(m), "comfy_cast_weights", None)
            if "stream_non_blocking" in m.__dict__ or m.__dict__.get("comfy_cast_weights", default) != default:
                total += 1
    return total
