from __future__ import annotations

import logging
import weakref
from typing import Collection, Sequence

import torch
import torch.nn as nn

from src.platform.observability.profiling import add_pinned_bytes

logger = logging.getLogger(__name__)

ARENA_ALIGNMENT = 4096
_HOST_REGISTER_PORTABLE = 1
_LIVE_ARENAS: weakref.WeakSet = weakref.WeakSet()


def caching_host_block_bytes(nbytes: int) -> int:
    return 1 << (nbytes - 1).bit_length() if nbytes > 1 else nbytes


def aligned_bytes(nbytes: int, alignment: int = ARENA_ALIGNMENT) -> int:
    return -(-nbytes // alignment) * alignment


def plan_segments(
    sizes: Sequence[int], segment_bytes: int, alignment: int = ARENA_ALIGNMENT,
) -> list[tuple[int, list[tuple[int, int]]]]:
    segments: list[tuple[int, list[tuple[int, int]]]] = []
    slots: list[tuple[int, int]] = []
    used = 0
    for index, nbytes in enumerate(sizes):
        span = aligned_bytes(nbytes, alignment)
        if slots and used + span > segment_bytes:
            segments.append((used, slots))
            slots, used = [], 0
        slots.append((index, used))
        used += span
    if slots:
        segments.append((used, slots))
    return segments


def _host_register(ptr: int, nbytes: int) -> None:
    torch.cuda.check_error(torch.cuda.cudart().cudaHostRegister(ptr, nbytes, _HOST_REGISTER_PORTABLE))


def _host_unregister(ptr: int) -> None:
    torch.cuda.check_error(torch.cuda.cudart().cudaHostUnregister(ptr))


def _synchronize(device: torch.device | None) -> None:
    torch.cuda.synchronize(device)


def is_host_pinned(tensor: torch.Tensor) -> bool:
    if any(arena.owns(tensor) for arena in list(_LIVE_ARENAS)):
        return True
    return bool(tensor.is_pinned())


def _storage_ptr(tensor: torch.Tensor) -> int:
    return tensor.untyped_storage().data_ptr()


class _Segment:
    __slots__ = ("buffer", "storage_ptr", "ptr", "nbytes", "registered", "slots")

    def __init__(self, nbytes: int, alignment: int) -> None:
        self.buffer: torch.Tensor | None = torch.empty(nbytes + alignment, dtype=torch.uint8, device="cpu")
        self.storage_ptr = self.buffer.data_ptr()
        self.ptr = self.storage_ptr + (-self.storage_ptr % alignment)
        self.nbytes = nbytes
        self.registered = False
        self.slots: dict[int, weakref.ref] = {}

    def view(self, offset: int, like: torch.Tensor) -> torch.Tensor:
        start = self.ptr - self.storage_ptr + offset
        end = start + like.numel() * like.element_size()
        return self.buffer[start:end].view(like.dtype).view(like.shape)


def _release_segments(segments: list[_Segment], device: torch.device | None) -> None:
    registered = [segment for segment in segments if segment.registered]
    if registered:
        try:
            _synchronize(device)
        except Exception:
            logger.warning("pinned arena: device sync before unregister failed", exc_info=True)
        for segment in registered:
            segment.registered = False
            try:
                _host_unregister(segment.ptr)
            except Exception:
                logger.warning("pinned arena: cudaHostUnregister failed", exc_info=True)
    for segment in segments:
        segment.buffer = None
        segment.slots.clear()


class PinnedArena:
    def __init__(
        self,
        *,
        segment_bytes: int,
        device: str | torch.device | None = None,
        alignment: int = ARENA_ALIGNMENT,
    ) -> None:
        self._alignment = alignment
        self._segment_bytes = max(int(segment_bytes), alignment)
        self._device = torch.device(device) if device is not None and str(device).startswith("cuda") else None
        self._segments: list[_Segment] = []
        self._by_storage: dict[int, _Segment] = {}
        self._finalizer = weakref.finalize(self, _release_segments, self._segments, self._device)
        self._finalizer.atexit = False
        _LIVE_ARENAS.add(self)

    @property
    def pinned_bytes(self) -> int:
        return sum(segment.nbytes for segment in self._segments if segment.registered)

    @property
    def registered_segments(self) -> int:
        return sum(1 for segment in self._segments if segment.registered)

    def pin(self, params: Sequence[nn.Parameter]) -> None:
        sizes = [p.data.numel() * p.data.element_size() for p in params]
        for used, slots in plan_segments(sizes, self._segment_bytes, self._alignment):
            segment = _Segment(used, self._alignment)
            self._segments.append(segment)
            self._by_storage[segment.storage_ptr] = segment
            _host_register(segment.ptr, used)
            segment.registered = True
            add_pinned_bytes(used)
            for index, offset in slots:
                param = params[index]
                view = segment.view(offset, param.data)
                view.copy_(param.data)
                param.data = view
                segment.slots[id(param)] = weakref.ref(param)

    def owns(self, tensor: torch.Tensor) -> bool:
        return _storage_ptr(tensor) in self._by_storage

    def unpin(self, param: nn.Parameter) -> bool:
        data = param.data
        segment = self._by_storage.get(_storage_ptr(data))
        if segment is None:
            return False
        unpinned = torch.empty_like(data, pin_memory=False)
        unpinned.copy_(data)
        param.data = unpinned
        segment.slots.pop(id(param), None)
        if not segment.slots:
            self._release(segment)
        return True

    def release_unused(self, keep: Collection[int] = ()) -> None:
        for segment in list(self._by_storage.values()):
            for key, ref in list(segment.slots.items()):
                param = ref()
                if param is None or (key not in keep and _storage_ptr(param.data) != segment.storage_ptr):
                    del segment.slots[key]
            if not segment.slots:
                self._release(segment)

    def release(self) -> None:
        self._by_storage.clear()
        self._finalizer()

    def _release(self, segment: _Segment) -> None:
        self._by_storage.pop(segment.storage_ptr, None)
        _release_segments([segment], self._device)
